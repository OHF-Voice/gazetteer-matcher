from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import yaml
from home_assistant_intents import get_intent_info

from .models import Token

ConfigData = dict[str, Any]
ConfigSource = str | Path | ConfigData


def _load_yaml(path: Path | Traversable) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file_obj:
        return yaml.safe_load(file_obj) or {}


def package_data_path(filename: str) -> Traversable:
    return files("gazetteer_matcher").joinpath("data", filename)


def _select_source(
    name: str,
    source: ConfigSource | None,
    path_source: ConfigSource | None,
) -> ConfigSource | None:
    if source is not None and path_source is not None:
        raise TypeError(f"pass either {name} or {name}_path, not both")
    return source if source is not None else path_source


def _load_source(
    source: ConfigSource | None,
    default: Callable[[], ConfigSource | Traversable],
) -> ConfigData:
    selected: ConfigSource | Traversable = source if source is not None else default()
    if isinstance(selected, dict):
        return selected
    if isinstance(selected, (str, Path)):
        return _load_yaml(Path(selected))
    return _load_yaml(selected)


def _load_intents(intents: ConfigData | None) -> ConfigData:
    if intents is not None:
        return intents
    intent_info = get_intent_info()
    if intent_info is None:
        raise RuntimeError("home-assistant-intents package metadata is unavailable")
    return intent_info


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
        vocabulary: ConfigSource | None = None,
        home: ConfigSource | None = None,
        intents: dict[str, Any] | None = None,
        responses: ConfigSource | None = None,
        vocabulary_path: ConfigSource | None = None,
        home_path: ConfigSource | None = None,
        responses_path: ConfigSource | None = None,
    ) -> "MatcherConfig":
        vocabulary_source = _select_source("vocabulary", vocabulary, vocabulary_path)
        home_source = _select_source("home", home, home_path)
        responses_source = _select_source("responses", responses, responses_path)
        return cls(
            vocabulary=_load_source(
                vocabulary_source, partial(package_data_path, "vocabulary.yaml")
            ),
            home=_load_source(
                home_source,
                lambda: {"areas": {}, "floors": {}, "entities": {}},
            ),
            intents=_load_intents(intents),
            responses=_load_source(
                responses_source, partial(package_data_path, "responses.yaml")
            ),
        )


def normalize_tokens(text: str) -> list[Token]:
    """Tokenize while retaining token and character offsets.

    Hyphens are separators so RBNF forms such as ``twenty-one`` line up with
    ordinary ASR output like ``twenty one``. Apostrophes are retained.
    """
    import re

    # Keep decimal literals together. A comma is accepted as a decimal
    # separator here; NumberWordTrie normalizes it before parsing.
    token_re = re.compile(r"\d+(?:[.,]\d+)?|[\w]+(?:['’][\w]+)?|%", re.UNICODE)
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
