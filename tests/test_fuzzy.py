import random

import pytest

from gazetteer_matcher.fuzzy import (
    ChoiceBatch,
    damerau_levenshtein_distance,
    extract,
    levenshtein_distance,
    native_available,
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


@pytest.mark.skipif(not native_available(), reason="native accelerator not built")
@pytest.mark.parametrize("algorithm", ["levenshtein", "damerau"])
@pytest.mark.parametrize("token_sort", [False, True])
def test_native_batch_exactly_matches_python(algorithm, token_sort):
    choices = [
        "Kitchen Light",
        "kichen light",
        "Hallway",
        "room living",
        "Straße Lamp",
        "strasse lamp",
        "Café 😊",
        "cafe 😊",
        "BAT",
        "bat",
    ]
    queries = [
        "kitchen light",
        "kitcehn light",
        "living room",
        "STRASSE LAMP",
        "café 😊",
        "bat",
        "nothing nearby",
    ]
    native = ChoiceBatch(choices, token_sort=token_sort)
    python = ChoiceBatch(choices, token_sort=token_sort, use_native=False)

    assert native.using_native
    for query in queries:
        assert native.extract(
            query, cutoff=0.2, limit=7, algorithm=algorithm
        ) == python.extract(query, cutoff=0.2, limit=7, algorithm=algorithm)


@pytest.mark.skipif(not native_available(), reason="native accelerator not built")
def test_native_batch_matches_python_for_generated_edits():
    generator = random.Random(42)
    alphabet = "abcdef é😊"
    choices = [
        "".join(generator.choice(alphabet) for _ in range(generator.randrange(1, 20)))
        for _ in range(100)
    ]
    native = ChoiceBatch(choices)
    python = ChoiceBatch(choices, use_native=False)

    for choice in choices[:25]:
        position = generator.randrange(len(choice))
        queries = [
            choice[:position] + choice[position + 1 :],
            choice[:position] + generator.choice(alphabet) + choice[position:],
            choice[:position] + generator.choice(alphabet) + choice[position + 1 :],
        ]
        if position + 1 < len(choice):
            queries.append(
                choice[:position]
                + choice[position + 1]
                + choice[position]
                + choice[position + 2 :]
            )
        for query in queries:
            assert native.extract(query, cutoff=0.1, limit=10) == python.extract(
                query, cutoff=0.1, limit=10
            )


@pytest.mark.skipif(not native_available(), reason="native accelerator not built")
def test_native_batch_token_length_filter_matches_python():
    choices = ["lamp", "kitchen lamp", "upstairs kitchen lamp", "far away"]
    lengths = [1, 2, 3, 2]
    native = ChoiceBatch(choices, token_lengths=lengths)
    python = ChoiceBatch(choices, token_lengths=lengths, use_native=False)

    for query_tokens in (1, 2, 3):
        assert native.extract(
            "kichen lamp",
            cutoff=0.0,
            limit=4,
            query_tokens=query_tokens,
            extra_tokens=1,
        ) == python.extract(
            "kichen lamp",
            cutoff=0.0,
            limit=4,
            query_tokens=query_tokens,
            extra_tokens=1,
        )
