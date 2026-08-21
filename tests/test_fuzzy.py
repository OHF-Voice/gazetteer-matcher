from gazetteer_matcher.fuzzy import (
    damerau_levenshtein_distance,
    extract,
    levenshtein_distance,
    ratio,
    token_sort_ratio,
)


def test_levenshtein():
    assert levenshtein_distance("kitchen", "kichen") == 1


def test_damerau_transposition():
    assert damerau_levenshtein_distance("switch", "swtich") == 1


def test_ratio():
    assert ratio("kitchen", "kichen") > 0.85
    assert ratio("kitchen", "banana") < 0.4


def test_token_sort():
    assert token_sort_ratio("living room", "room living") == 1.0


def test_extract():
    matches = extract("kichen", ["hallway", "kitchen", "bedroom"], cutoff=0.5)
    assert matches[0].choice == "kitchen"
