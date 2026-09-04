from __future__ import annotations

import asyncio
import math
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from .catalog import QuizCatalog
from .models import Question, QuizManifest


class QuizAccessError(PermissionError):
    pass


class QuizStateError(RuntimeError):
    pass


@dataclass
class QuizSession:
    id: str
    quiz_id: str
    user_id: int
    guild_id: int | None
    channel_id: int
    question_ids: list[str]
    target_question_count: int
    remaining_question_ids: set[str] = field(default_factory=set)
    tier_posterior: list[float] | None = None
    index: int = 0
    correct_question_ids: set[str] = field(default_factory=set)
    last_activity: float = 0.0


@dataclass(frozen=True)
class TierEstimate:
    id: str
    label: str
    confidence: float
    lower_label: str
    upper_label: str


@dataclass(frozen=True)
class AnswerResult:
    correct: bool
    complete: bool
    score: float | None
    next_question: Question | None
    tier_estimate: TierEstimate | None = None


class QuizService:
    _TIER_SCALE = 1.25

    def __init__(
        self,
        catalog: QuizCatalog,
        rng: random.Random | None = None,
        *,
        idle_ttl_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if idle_ttl_seconds <= 0:
            raise ValueError("idle_ttl_seconds must be positive")
        self.catalog = catalog
        self.rng = rng or random.SystemRandom()
        self.idle_ttl_seconds = idle_ttl_seconds
        self._clock = clock
        self._sessions: dict[str, QuizSession] = {}
        self._active_by_user: dict[tuple[int | None, int, int], str] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def start(self, quiz_id: str, *, user_id: int, guild_id: int | None, channel_id: int) -> tuple[QuizSession, Question]:
        self._prune_expired()
        manifest = self.catalog.get(quiz_id)
        session_id = uuid.uuid4().hex

        if manifest.adaptive:
            posterior = [1.0 / len(manifest.tiers)] * len(manifest.tiers)
            remaining = {question.id for question in manifest.questions if question.tier is not None}
            first = self._select_adaptive_question(manifest, remaining, posterior)
            remaining.remove(first.id)
            question_ids = [first.id]
        else:
            selected = self.rng.sample(manifest.questions, manifest.sample_size)
            first = selected[0]
            posterior = None
            remaining = set()
            question_ids = [question.id for question in selected]

        session = QuizSession(
            id=session_id,
            quiz_id=quiz_id,
            user_id=user_id,
            guild_id=guild_id,
            channel_id=channel_id,
            question_ids=question_ids,
            target_question_count=manifest.sample_size,
            remaining_question_ids=remaining,
            tier_posterior=posterior,
            last_activity=self._clock(),
        )
        key = self._key(session)
        old_id = self._active_by_user.get(key)
        if old_id:
            self._drop_session(old_id)
        self._sessions[session_id] = session
        self._active_by_user[key] = session_id
        self._locks[session_id] = asyncio.Lock()
        return session, first

    async def answer(
        self,
        session_id: str,
        *,
        user_id: int,
        question_index: int,
        choice_id: str,
    ) -> AnswerResult:
        self._prune_expired()
        session = self._sessions.get(session_id)
        lock = self._locks.get(session_id)
        if session is None or lock is None:
            raise QuizStateError("quiz session is no longer active")
        if session.user_id != user_id:
            raise QuizAccessError("only the user who started the quiz may answer")

        async with lock:
            if self._sessions.get(session_id) is not session or self._active_by_user.get(self._key(session)) != session_id:
                raise QuizStateError("quiz session is no longer active")
            if question_index != session.index:
                raise QuizStateError("stale or replayed quiz answer")

            manifest = self.catalog.get(session.quiz_id)
            question = manifest.question_by_id(session.question_ids[session.index])
            choice = next((item for item in question.choices if item.id == choice_id), None)
            if choice is None:
                raise QuizStateError("unknown choice")

            if choice.correct:
                session.correct_question_ids.add(question.id)

            if manifest.adaptive and question.tier is not None and session.tier_posterior is not None:
                session.tier_posterior = self._update_tier_posterior(
                    manifest,
                    session.tier_posterior,
                    question,
                    choice.correct,
                )

            session.index += 1
            session.last_activity = self._clock()
            estimate = self._tier_estimate(manifest, session.tier_posterior)

            if session.index >= session.target_question_count:
                score = self._score(manifest, session)
                self._drop_session(session_id, expected=session)
                return AnswerResult(
                    correct=choice.correct,
                    complete=True,
                    score=score,
                    next_question=None,
                    tier_estimate=estimate,
                )

            if manifest.adaptive:
                if session.tier_posterior is None or not session.remaining_question_ids:
                    raise QuizStateError("adaptive quiz ran out of calibrated questions")
                next_question = self._select_adaptive_question(
                    manifest,
                    session.remaining_question_ids,
                    session.tier_posterior,
                )
                session.remaining_question_ids.remove(next_question.id)
                session.question_ids.append(next_question.id)
            else:
                next_question = manifest.question_by_id(session.question_ids[session.index])

            return AnswerResult(
                correct=choice.correct,
                complete=False,
                score=None,
                next_question=next_question,
                tier_estimate=estimate,
            )

    def cancel(self, session_id: str, *, user_id: int | None = None) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        if user_id is not None and session.user_id != user_id:
            raise QuizAccessError("only the user who started the quiz may cancel it")
        self._drop_session(session_id, expected=session)
        return True

    def _score(self, manifest: QuizManifest, session: QuizSession) -> float:
        selected = [manifest.question_by_id(qid) for qid in session.question_ids]
        total_weight = sum(question.weight for question in selected)
        earned_weight = sum(
            question.weight for question in selected if question.id in session.correct_question_ids
        )
        return (earned_weight / total_weight) * 100.0

    def _select_adaptive_question(
        self,
        manifest: QuizManifest,
        candidate_ids: set[str],
        posterior: list[float],
    ) -> Question:
        target_tier = self._posterior_median_index(posterior)
        tiered = [
            manifest.question_by_id(question_id)
            for question_id in candidate_ids
            if manifest.question_by_id(question_id).tier is not None
        ]
        if not tiered:
            raise QuizStateError("adaptive quiz has no calibrated questions remaining")

        distance = min(abs(manifest.tier_index(question.tier) - target_tier) for question in tiered)
        nearest = [
            question
            for question in tiered
            if abs(manifest.tier_index(question.tier) - target_tier) == distance
        ]
        return self.rng.choice(nearest)

    def _update_tier_posterior(
        self,
        manifest: QuizManifest,
        posterior: list[float],
        question: Question,
        correct: bool,
    ) -> list[float]:
        likelihoods = self._correct_likelihoods(manifest, question)
        weighted = [
            prior * (likelihood if correct else 1.0 - likelihood)
            for prior, likelihood in zip(posterior, likelihoods, strict=True)
        ]
        return self._normalized(weighted)

    def _correct_likelihoods(self, manifest: QuizManifest, question: Question) -> list[float]:
        if question.tier is None:
            raise QuizStateError("adaptive question is missing a tier")
        difficulty = manifest.tier_index(question.tier)
        guess_floor = 1.0 / len(question.choices)
        return [
            guess_floor
            + (1.0 - guess_floor)
            / (1.0 + math.exp(-(ability - difficulty) / self._TIER_SCALE))
            for ability in range(len(manifest.tiers))
        ]

    def _tier_estimate(self, manifest: QuizManifest, posterior: list[float] | None) -> TierEstimate | None:
        if not manifest.adaptive or posterior is None:
            return None
        best_index = self._posterior_median_index(posterior)

        cumulative = 0.0
        lower_index = 0
        upper_index = len(posterior) - 1
        lower_found = False
        for index, probability in enumerate(posterior):
            cumulative += probability
            if not lower_found and cumulative >= 0.10:
                lower_index = index
                lower_found = True
            if cumulative >= 0.90:
                upper_index = index
                break

        tier = manifest.tiers[best_index]
        return TierEstimate(
            id=tier.id,
            label=tier.label,
            confidence=posterior[best_index],
            lower_label=manifest.tiers[lower_index].label,
            upper_label=manifest.tiers[upper_index].label,
        )

    @staticmethod
    def _posterior_median_index(posterior: list[float]) -> int:
        cumulative = 0.0
        for index, probability in enumerate(posterior):
            cumulative += probability
            if cumulative >= 0.5:
                return index
        return len(posterior) - 1

    @staticmethod
    def _normalized(values: list[float]) -> list[float]:
        total = sum(values)
        if total <= 0:
            return [1.0 / len(values)] * len(values)
        return [value / total for value in values]

    def _key(self, session: QuizSession) -> tuple[int | None, int, int]:
        return (session.guild_id, session.channel_id, session.user_id)

    def _drop_session(self, session_id: str, *, expected: QuizSession | None = None) -> None:
        session = self._sessions.get(session_id)
        if session is None or (expected is not None and session is not expected):
            return
        self._sessions.pop(session_id, None)
        self._locks.pop(session_id, None)
        key = self._key(session)
        if self._active_by_user.get(key) == session_id:
            self._active_by_user.pop(key, None)

    def _prune_expired(self) -> None:
        cutoff = self._clock() - self.idle_ttl_seconds
        expired = [sid for sid, session in self._sessions.items() if session.last_activity <= cutoff]
        for session_id in expired:
            self._drop_session(session_id)

    def clear(self) -> None:
        self._sessions.clear()
        self._active_by_user.clear()
        self._locks.clear()
