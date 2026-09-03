from __future__ import annotations

import asyncio
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
    question_ids: tuple[str, ...]
    index: int = 0
    correct_question_ids: set[str] = field(default_factory=set)
    last_activity: float = 0.0


@dataclass(frozen=True)
class AnswerResult:
    correct: bool
    complete: bool
    score: float | None
    next_question: Question | None


class QuizService:
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
        selected = tuple(self.rng.sample(manifest.questions, manifest.sample_size))
        session_id = uuid.uuid4().hex
        session = QuizSession(
            id=session_id,
            quiz_id=quiz_id,
            user_id=user_id,
            guild_id=guild_id,
            channel_id=channel_id,
            question_ids=tuple(question.id for question in selected),
            last_activity=self._clock(),
        )
        key = self._key(session)
        old_id = self._active_by_user.get(key)
        if old_id:
            self._drop_session(old_id)
        self._sessions[session_id] = session
        self._active_by_user[key] = session_id
        self._locks[session_id] = asyncio.Lock()
        return session, selected[0]

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

            session.index += 1
            session.last_activity = self._clock()
            if session.index >= len(session.question_ids):
                score = self._score(manifest, session)
                self._drop_session(session_id, expected=session)
                return AnswerResult(
                    correct=choice.correct,
                    complete=True,
                    score=score,
                    next_question=None,
                )

            next_question = manifest.question_by_id(session.question_ids[session.index])
            return AnswerResult(
                correct=choice.correct,
                complete=False,
                score=None,
                next_question=next_question,
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
