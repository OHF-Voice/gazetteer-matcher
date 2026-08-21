from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import yaml

from .models import Token


def _load_yaml(path: Path | Traversable) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file_obj:
        return yaml.safe_load(file_obj) or {}


def package_data_path(filename: str) -> Traversable:
    return files("gazetteer_matcher").joinpath("data", filename)


@dataclass
class MatcherConfig:
    vocabulary: dict[str, Any]
    home: dict[str, Any]
    intents: dict[str, Any]
    responses: dict[str, Any]

    @classmethod
    def load(
        cls,
        *,
        vocabulary_path: str | Path | None = None,
        home_path: str | Path | None = None,
        intents_path: str | Path | None = None,
        responses_path: str | Path | None = None,
    ) -> "MatcherConfig":
        vocabulary = _load_yaml(Path(vocabulary_path) if vocabulary_path else package_data_path("vocabulary.yaml"))
        home = _load_yaml(Path(home_path) if home_path else package_data_path("home.yaml"))
        intents = _load_yaml(Path(intents_path) if intents_path else package_data_path("intents.yaml"))
        responses = _load_yaml(
            Path(responses_path)
            if responses_path
            else package_data_path("responses.yaml")
        )
        return cls(
            vocabulary=vocabulary,
            home=home,
            intents=intents,
            responses=responses,
        )


def normalize_tokens(text: str) -> list[Token]:
    """Tokenize while retaining token and character offsets.

    Hyphens are separators so RBNF forms such as ``twenty-one`` line up with
    ordinary ASR output like ``twenty one``. Apostrophes are retained.
    """
    import re

    # Keep decimal literals together. A comma is accepted as a decimal
    # separator here; NumberWordTrie normalizes it before parsing.
    token_re = re.compile(
        r"\d+(?:[.,]\d+)?|[\w]+(?:['’][\w]+)?|%", re.UNICODE
    )
    tokens: list[Token] = []
    for index, match in enumerate(token_re.finditer(text)):
        raw = match.group(0)
        tokens.append(
            Token(
                index=index,
                text=raw.casefold().replace("’", "'"),
                raw=raw,
                start_char=match.start(),
                end_char=match.end(),
            )
        )
    return tokens


def phrase_tokens(phrase: str) -> tuple[str, ...]:
    return tuple(token.text for token in normalize_tokens(phrase))
