import pytest
from pydantic import ValidationError

from quiz_discord_addon.models import QuizManifest


def base_manifest():
    return {
        "schema_version": 1,
        "id": "sample",
        "title": "Sample",
        "description": "Sample quiz",
        "sample_size": 1,
        "questions": [
            {
                "id": "q1",
                "prompt": "Question?",
                "weight": 2,
                "choices": [
                    {"id": "a", "text": "A", "correct": True},
                    {"id": "b", "text": "B", "correct": False},
                ],
            }
        ],
    }


def test_valid_manifest():
    manifest = QuizManifest.model_validate(base_manifest())
    assert manifest.questions[0].correct_choice.id == "a"


def test_accepts_descriptive_choice_text_longer_than_button_limit():
    raw = base_manifest()
    text = "A detailed progression answer that is intentionally longer than a Discord button label."
    assert len(text) > 80
    raw["questions"][0]["choices"][0]["text"] = text
    manifest = QuizManifest.model_validate(raw)
    assert manifest.questions[0].choices[0].text == text


def test_rejects_choice_text_beyond_embed_contract():
    raw = base_manifest()
    raw["questions"][0]["choices"][0]["text"] = "x" * 501
    with pytest.raises(ValidationError):
        QuizManifest.model_validate(raw)


def test_rejects_multiple_correct_choices():
    raw = base_manifest()
    raw["questions"][0]["choices"][1]["correct"] = True
    with pytest.raises(ValidationError):
        QuizManifest.model_validate(raw)


def test_rejects_sample_larger_than_bank():
    raw = base_manifest()
    raw["sample_size"] = 2
    with pytest.raises(ValidationError):
        QuizManifest.model_validate(raw)


def test_rejects_duplicate_choice_text():
    raw = base_manifest()
    raw["questions"][0]["choices"][1]["text"] = "A"
    with pytest.raises(ValidationError):
        QuizManifest.model_validate(raw)
