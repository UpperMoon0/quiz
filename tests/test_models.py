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
    assert not manifest.adaptive


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


def test_adaptive_manifest_requires_schema_v2_and_calibrated_tiers():
    raw = base_manifest()
    raw["schema_version"] = 2
    raw["tiers"] = [{"id": "lv", "label": "LV"}]
    raw["questions"][0]["tier"] = "lv"
    manifest = QuizManifest.model_validate(raw)
    assert manifest.adaptive
    assert manifest.tier_index("lv") == 0

    raw["schema_version"] = 1
    with pytest.raises(ValidationError, match="schema_version 2"):
        QuizManifest.model_validate(raw)


def test_adaptive_manifest_rejects_unknown_or_unrepresented_tiers():
    raw = base_manifest()
    raw["schema_version"] = 2
    raw["tiers"] = [
        {"id": "lv", "label": "LV"},
        {"id": "mv", "label": "MV"},
    ]
    raw["questions"][0]["tier"] = "hv"
    with pytest.raises(ValidationError, match="unknown tiers"):
        QuizManifest.model_validate(raw)

    raw["questions"][0]["tier"] = "lv"
    with pytest.raises(ValidationError, match="every tier needs at least one question"):
        QuizManifest.model_validate(raw)
