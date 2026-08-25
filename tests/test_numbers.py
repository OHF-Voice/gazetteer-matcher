"""Tests for the number-word trie and its sharing."""

from gazetteer_matcher.numbers import NumberWordTrie


def _settings() -> dict:
    return {
        "max_cardinal": 20,
        "max_ordinal": 5,
        "joiners": ["and"],
        "negative_signs": ["minus", "negative"],
        "positive_signs": ["plus", "positive"],
    }


def test_shared_returns_one_trie_per_settings() -> None:
    """Test a trie is built once and handed to every caller that wants the same one.

    Building one spells every number in the language, so a caller that rebuilds a
    matcher whenever its home changes would otherwise pay for it every time.
    """
    first = NumberWordTrie.shared("en", **_settings())
    second = NumberWordTrie.shared("en", **_settings())

    assert first is second


def test_shared_separates_different_settings() -> None:
    """Test settings that change the contents do not share a trie."""
    shared = NumberWordTrie.shared("en", **_settings())

    assert (
        NumberWordTrie.shared("en", **{**_settings(), "max_cardinal": 21}) is not shared
    )
    assert NumberWordTrie.shared("en", **{**_settings(), "joiners": []}) is not shared
    assert (
        NumberWordTrie.shared("en", **{**_settings(), "negative_signs": ["minus"]})
        is not shared
    )


def test_constructing_directly_gives_a_private_trie() -> None:
    """Test the cache is opt-in, so a caller can still build its own."""
    assert NumberWordTrie("en", **_settings()) is not NumberWordTrie.shared(
        "en", **_settings()
    )
