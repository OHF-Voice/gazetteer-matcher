from pathlib import Path
from typing import Any

import pytest
import yaml

from gazetteer_matcher import GazetteerMatcher

SENTENCES_DIR = Path(__file__).parent / "sentences"


def load_sentence_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted(SENTENCES_DIR.glob("*.yaml")):
        document = yaml.safe_load(path.read_text()) or {}
        assert document.get("language") == "en"
        cases.extend(
            {**case, "_source": path.name} for case in document.get("cases", [])
        )
    return cases


def load_sentence_series() -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    for path in sorted(SENTENCES_DIR.glob("*.yaml")):
        document = yaml.safe_load(path.read_text()) or {}
        assert document.get("language") == "en"
        for item in document.get("series", []):
            assert item.get("name"), f"{path.name}: every series requires a name"
            assert item.get("turns"), f"{path.name}:{item['name']}: no turns"
            series.append({**item, "_source": path.name})
    return series


def actual_frames(result) -> list[dict[str, Any]]:
    return [
        {
            "intent": frame.intent,
            "combination": frame.combination,
            "slots": frame.slots,
        }
        for frame in result.frames
    ]


SENTENCE_CASES = load_sentence_cases()
SENTENCE_SERIES = load_sentence_series()


@pytest.mark.parametrize(
    "case",
    SENTENCE_CASES,
    ids=lambda case: f"{case['_source']}:{case['sentence']}",
)
def test_home_sentence_frames(
    matcher: GazetteerMatcher,
    case: dict[str, Any],
):
    result = matcher.interpret(
        case["sentence"],
        context_area=case.get("context_area"),
        context_floor=case.get("context_floor"),
    )

    assert result.accepted, result.reason
    assert actual_frames(result) == case["frames"]


@pytest.mark.parametrize(
    "series",
    SENTENCE_SERIES,
    ids=lambda series: f"{series['_source']}:{series['name']}",
)
def test_home_sentence_series(
    matcher: GazetteerMatcher,
    series: dict[str, Any],
):
    previous_targets = ()
    for turn_number, turn in enumerate(series["turns"], start=1):
        result = matcher.interpret(
            turn["sentence"],
            context_area=turn.get("context_area", series.get("context_area")),
            context_floor=turn.get("context_floor", series.get("context_floor")),
            previous_targets=previous_targets,
        )

        turn_label = (
            f"{series['_source']}:{series['name']}:turn {turn_number} "
            f"({turn['sentence']!r})"
        )
        assert result.accepted, f"{turn_label}: {result.reason}"
        assert actual_frames(result) == turn["frames"], turn_label
        previous_targets = result.targets
