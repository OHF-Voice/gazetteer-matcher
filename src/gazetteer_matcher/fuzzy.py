from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import import_module
from types import ModuleType
from typing import Iterable

_SPACE_RE = re.compile(r"\s+")

_NATIVE: ModuleType | None
try:
    _NATIVE = import_module("gazetteer_matcher._fuzzy_native")
except ImportError:
    _NATIVE = None


def normalize(text: str) -> str:
    return _SPACE_RE.sub(" ", text.casefold().strip())


def levenshtein_distance(a: str, b: str) -> int:
    """Memory-efficient Levenshtein edit distance."""
    a = normalize(a)
    b = normalize(b)
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(
                min(
                    current[j - 1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (ca != cb),
                )
            )
        previous = current
    return previous[-1]


def damerau_levenshtein_distance(a: str, b: str) -> int:
    """Optimal-string-alignment Damerau-Levenshtein distance.

    Adjacent transpositions count as one edit. This is intentionally small and
    dependency-free; for short voice/gazetteer phrases it is more than fast
    enough.
    """
    a = normalize(a)
    b = normalize(b)
    rows, cols = len(a) + 1, len(b) + 1
    d = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        d[i][0] = i
    for j in range(cols):
        d[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(
                d[i - 1][j] + 1,
                d[i][j - 1] + 1,
                d[i - 1][j - 1] + cost,
            )
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def ratio(a: str, b: str, *, algorithm: str = "damerau") -> float:
    a_n = normalize(a)
    b_n = normalize(b)
    denom = max(len(a_n), len(b_n), 1)
    if algorithm == "levenshtein":
        distance = levenshtein_distance(a_n, b_n)
    elif algorithm == "damerau":
        distance = damerau_levenshtein_distance(a_n, b_n)
    else:
        raise ValueError(f"Unknown fuzzy algorithm: {algorithm}")
    return max(0.0, 1.0 - (distance / denom))


def token_sort_ratio(a: str, b: str, *, algorithm: str = "damerau") -> float:
    a_sorted = " ".join(sorted(normalize(a).split()))
    b_sorted = " ".join(sorted(normalize(b).split()))
    return ratio(a_sorted, b_sorted, algorithm=algorithm)


@dataclass(frozen=True)
class Match:
    choice: str
    score: float


def native_available() -> bool:
    """Return whether the optional compiled batch scorer was imported."""
    return _NATIVE is not None


class ChoiceBatch:
    """A reusable set of choices scored together, natively when available."""

    def __init__(
        self,
        choices: Iterable[str],
        *,
        token_sort: bool = False,
        use_native: bool = True,
        token_lengths: Iterable[int] | None = None,
    ) -> None:
        self.choices = tuple(choices)
        self.token_sort = token_sort
        self.token_lengths = (
            tuple(token_lengths)
            if token_lengths is not None
            else (-1,) * len(self.choices)
        )
        if len(self.token_lengths) != len(self.choices):
            raise ValueError("choices and token_lengths must have equal lengths")
        scoring_choices = tuple(self._scoring_text(choice) for choice in self.choices)
        self._native = (
            _NATIVE.compile_choices(scoring_choices, self.choices, self.token_lengths)
            if use_native and _NATIVE is not None
            else None
        )

    def _scoring_text(self, text: str) -> str:
        normalized = normalize(text)
        if self.token_sort:
            return " ".join(sorted(normalized.split()))
        return normalized

    @property
    def using_native(self) -> bool:
        """Return whether this batch is backed by the compiled scorer."""
        return self._native is not None

    @property
    def max_tokens(self) -> int:
        """Return the largest configured token length, or zero when unspecified."""
        return max((length for length in self.token_lengths if length >= 0), default=0)

    def eligible_choices(
        self, query_tokens: int | None, extra_tokens: int
    ) -> list[str]:
        """Return choices inside an optional token-length window."""
        if query_tokens is None:
            return list(self.choices)
        return [
            choice
            for choice, length in zip(self.choices, self.token_lengths)
            if abs(length - query_tokens) <= extra_tokens
        ]

    def extract(
        self,
        query: str,
        *,
        cutoff: float = 0.0,
        limit: int = 3,
        algorithm: str = "damerau",
        query_tokens: int | None = None,
        extra_tokens: int = 0,
    ) -> list[Match]:
        """Return the best choices with the same ordering as :func:`extract`."""
        if algorithm not in {"levenshtein", "damerau"}:
            raise ValueError(f"Unknown fuzzy algorithm: {algorithm}")
        if limit <= 0:
            return []

        if self._native is not None:
            native = _NATIVE
            assert native is not None
            matches = native.extract(
                self._scoring_text(query),
                self._native,
                cutoff=cutoff,
                limit=limit,
                algorithm=algorithm,
                query_tokens=query_tokens if query_tokens is not None else -1,
                extra_tokens=extra_tokens,
            )
            return [
                Match(choice=self.choices[index], score=score)
                for index, score in matches
            ]

        scorer = token_sort_ratio if self.token_sort else ratio
        matches = [
            Match(choice=choice, score=scorer(query, choice, algorithm=algorithm))
            for choice, length in zip(self.choices, self.token_lengths)
            if query_tokens is None or abs(length - query_tokens) <= extra_tokens
        ]
        matches = [match for match in matches if match.score >= cutoff]
        matches.sort(key=lambda match: (-match.score, len(match.choice), match.choice))
        return matches[:limit]


def extract(
    query: str,
    choices: Iterable[str],
    *,
    cutoff: float = 0.0,
    limit: int = 3,
    algorithm: str = "damerau",
    token_sort: bool = False,
) -> list[Match]:
    return ChoiceBatch(choices, token_sort=token_sort).extract(
        query, cutoff=cutoff, limit=limit, algorithm=algorithm
    )
