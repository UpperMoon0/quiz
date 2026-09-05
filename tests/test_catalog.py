from quiz_discord_addon.catalog import DEFAULT_QUESTION_SETS, parse_enabled_question_sets


def test_gtnh_and_tfg_are_enabled_by_default():
    assert DEFAULT_QUESTION_SETS == frozenset({"gtnh", "tfg"})
    assert parse_enabled_question_sets(None) == DEFAULT_QUESTION_SETS
    assert parse_enabled_question_sets("") == DEFAULT_QUESTION_SETS
    assert parse_enabled_question_sets("   ") == DEFAULT_QUESTION_SETS


def test_question_set_environment_variable_remains_an_optional_override():
    assert parse_enabled_question_sets("gtnh") == frozenset({"gtnh"})
    assert parse_enabled_question_sets("tfg") == frozenset({"tfg"})
    assert parse_enabled_question_sets("gtnh,tfg") == DEFAULT_QUESTION_SETS
    assert parse_enabled_question_sets("*") is None
