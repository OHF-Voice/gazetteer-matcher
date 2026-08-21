from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
from typing import Iterable


_SPACE_RE = re.compile(r"\s+")


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


def extract(
    query: str,
    choices: Iterable[str],
    *,
    cutoff: float = 0.0,
    limit: int = 3,
    algorithm: str = "damerau",
    token_sort: bool = False,
) -> list[Match]:
    scorer = token_sort_ratio if token_sort else ratio
    matches = [
        Match(choice=choice, score=scorer(query, choice, algorithm=algorithm))
        for choice in choices
    ]
    matches = [match for match in matches if match.score >= cutoff]
    matches.sort(key=lambda match: (-match.score, len(match.choice), match.choice))
    return matches[:limit]
