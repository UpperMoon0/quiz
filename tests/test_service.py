import random

import pytest

from quiz_discord_addon.catalog import QuizCatalog
from quiz_discord_addon.models import QuizManifest
from quiz_discord_addon.service import QuizAccessError, QuizService, QuizStateError


def make_manifest():
    return QuizManifest.model_validate(
        {
            "schema_version": 1,
            "id": "sample",
            "title": "Sample",
            "description": "Sample quiz",
            "sample_size": 2,
            "questions": [
                {
                    "id": "q1",
                    "prompt": "One?",
                    "weight": 1,
                    "choices": [
                        {"id": "a", "text": "yes", "correct": True},
                        {"id": "b", "text": "no", "correct": False},
                    ],
                },
                {
                    "id": "q2",
                    "prompt": "Two?",
                    "weight": 3,
                    "choices": [
                        {"id": "a", "text": "yes", "correct": True},
                        {"id": "b", "text": "no", "correct": False},
                    ],
                },
            ],
        }
    )


def make_adaptive_manifest():
    tiers = [{"id": f"t{index}", "label": f"Tier {index}"} for index in range(5)]
    questions = []
    for tier_index in range(5):
        for question_index in range(3):
            questions.append(
                {
                    "id": f"t{tier_index}-q{question_index}",
                    "prompt": f"Tier {tier_index} question {question_index}?",
                    "tier": f"t{tier_index}",
                    "choices": [
                        {"id": "a", "text": "right", "correct": True},
                        {"id": "b", "text": "wrong 1", "correct": False},
                        {"id": "c", "text": "wrong 2", "correct": False},
                        {"id": "d", "text": "wrong 3", "correct": False},
                    ],
                }
            )
    return QuizManifest.model_validate(
        {
            "schema_version": 2,
            "id": "adaptive",
            "title": "Adaptive",
            "description": "Adaptive tier quiz",
            "sample_size": 5,
            "tiers": tiers,
            "questions": questions,
        }
    )


@pytest.mark.asyncio
async def test_weighted_score_replay_protection_and_completion_cleanup():
    manifest = make_manifest()
    service = QuizService(QuizCatalog({"sample": manifest}), rng=random.Random(1))
    session, first = service.start("sample", user_id=10, guild_id=20, channel_id=30)

    first_correct = first.correct_choice.id
    result = await service.answer(
        session.id,
        user_id=10,
        question_index=0,
        choice_id=first_correct,
    )
    assert not result.complete

    with pytest.raises(QuizStateError):
        await service.answer(
            session.id,
            user_id=10,
            question_index=0,
            choice_id=first_correct,
        )

    next_question = result.next_question
    wrong = next(choice.id for choice in next_question.choices if not choice.correct)
    final = await service.answer(
        session.id,
        user_id=10,
        question_index=1,
        choice_id=wrong,
    )
    assert final.complete

    selected = [manifest.question_by_id(qid) for qid in session.question_ids]
    expected = selected[0].weight / sum(q.weight for q in selected) * 100
    assert final.score == pytest.approx(expected)
    assert final.tier_estimate is None
    assert session.id not in service._sessions
    assert session.id not in service._locks


@pytest.mark.asyncio
async def test_only_owner_can_answer():
    manifest = make_manifest()
    service = QuizService(QuizCatalog({"sample": manifest}), rng=random.Random(1))
    session, first = service.start("sample", user_id=10, guild_id=20, channel_id=30)

    with pytest.raises(QuizAccessError):
        await service.answer(
            session.id,
            user_id=11,
            question_index=0,
            choice_id=first.correct_choice.id,
        )


@pytest.mark.asyncio
async def test_restarting_attempt_invalidates_old_session():
    manifest = make_manifest()
    service = QuizService(QuizCatalog({"sample": manifest}), rng=random.Random(1))
    old, first = service.start("sample", user_id=10, guild_id=20, channel_id=30)
    new, _ = service.start("sample", user_id=10, guild_id=20, channel_id=30)

    assert old.id != new.id
    with pytest.raises(QuizStateError):
        await service.answer(
            old.id,
            user_id=10,
            question_index=0,
            choice_id=first.correct_choice.id,
        )


def test_expired_sessions_are_pruned_on_activity():
    manifest = make_manifest()
    now = [100.0]
    service = QuizService(
        QuizCatalog({"sample": manifest}),
        rng=random.Random(1),
        idle_ttl_seconds=10,
        clock=lambda: now[0],
    )
    old, _ = service.start("sample", user_id=10, guild_id=20, channel_id=30)
    now[0] = 111.0
    service.start("sample", user_id=11, guild_id=20, channel_id=30)
    assert old.id not in service._sessions
    assert old.id not in service._locks


@pytest.mark.asyncio
async def test_adaptive_quiz_moves_next_question_up_after_correct_answer():
    manifest = make_adaptive_manifest()
    service = QuizService(QuizCatalog({"adaptive": manifest}), rng=random.Random(4))
    session, first = service.start("adaptive", user_id=10, guild_id=20, channel_id=30)

    assert first.tier == "t2"
    result = await service.answer(
        session.id,
        user_id=10,
        question_index=0,
        choice_id=first.correct_choice.id,
    )

    assert result.next_question.tier == "t3"
    assert result.tier_estimate.id == "t3"


@pytest.mark.asyncio
async def test_adaptive_quiz_moves_next_question_down_after_wrong_answer():
    manifest = make_adaptive_manifest()
    service = QuizService(QuizCatalog({"adaptive": manifest}), rng=random.Random(4))
    session, first = service.start("adaptive", user_id=10, guild_id=20, channel_id=30)

    wrong = next(choice.id for choice in first.choices if not choice.correct)
    result = await service.answer(
        session.id,
        user_id=10,
        question_index=0,
        choice_id=wrong,
    )

    assert result.next_question.tier == "t1"
    assert result.tier_estimate.id == "t1"


@pytest.mark.asyncio
async def test_adaptive_quiz_reports_score_and_tier_on_completion():
    manifest = make_adaptive_manifest()
    service = QuizService(QuizCatalog({"adaptive": manifest}), rng=random.Random(2))
    session, question = service.start("adaptive", user_id=10, guild_id=20, channel_id=30)

    final = None
    for index in range(manifest.sample_size):
        final = await service.answer(
            session.id,
            user_id=10,
            question_index=index,
            choice_id=question.correct_choice.id,
        )
        if final.complete:
            break
        question = final.next_question

    assert final.complete
    assert final.score == pytest.approx(100.0)
    assert final.tier_estimate is not None
    assert final.tier_estimate.id in {"t3", "t4"}
    assert len(session.question_ids) == manifest.sample_size
