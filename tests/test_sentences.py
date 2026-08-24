from pathlib import Path
from typing import Any

import pytest
import yaml

from gazetteer_matcher import GazetteerMatcher, TargetReference

SENTENCES_DIR = Path(__file__).parent / "sentences"


def sentence_values(record: dict[str, Any], label: str) -> list[str]:
    has_sentence = "sentence" in record
    has_sentences = "sentences" in record
    assert (
        has_sentence != has_sentences
    ), f"{label}: define exactly one of sentence or sentences"
    values = [record["sentence"]] if has_sentence else record["sentences"]
    assert (
        isinstance(values, list) and values
    ), f"{label}: sentences must be a non-empty list"
    assert all(
        isinstance(value, str) and value.strip() for value in values
    ), f"{label}: sentences must contain non-empty strings"
    return values


def load_sentence_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted(SENTENCES_DIR.glob("*.yaml")):
        document = yaml.safe_load(path.read_text()) or {}
        assert document.get("language") == "en"
        for case_number, case in enumerate(document.get("cases", []), start=1):
            label = f"{path.name}:case {case_number}"
            for sentence in sentence_values(case, label):
                expanded = {
                    key: value
                    for key, value in case.items()
                    if key not in {"sentence", "sentences"}
                }
                cases.append({**expanded, "sentence": sentence, "_source": path.name})
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
    frames: list[dict[str, Any]] = []
    for frame in result.frames:
        item = {
            "intent": frame.intent,
            "combination": frame.combination,
            "slots": frame.slots,
        }
        if frame.response_key is not None:
            item["response_key"] = frame.response_key
        frames.append(item)
    return frames


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
    if case.get("requires_fuzzy"):
        assert any(
            frame.action_span.source.startswith("fuzzy")
            or any(
                option.source.startswith("fuzzy")
                for option in frame.slot_options.values()
            )
            for frame in result.frames
        ), f"{case['_source']}:{case['sentence']}: expected a selected fuzzy span"


@pytest.mark.parametrize(
    "series",
    SENTENCE_SERIES,
    ids=lambda series: f"{series['_source']}:{series['name']}",
)
def test_home_sentence_series(
    matcher: GazetteerMatcher,
    series: dict[str, Any],
):
    previous_targets: tuple[TargetReference, ...] = ()
    for turn_number, turn in enumerate(series["turns"], start=1):
        base_label = f"{series['_source']}:{series['name']}:turn {turn_number}"
        results = []
        for sentence in sentence_values(turn, base_label):
            result = matcher.interpret(
                sentence,
                context_area=turn.get("context_area", series.get("context_area")),
                context_floor=turn.get("context_floor", series.get("context_floor")),
                previous_targets=previous_targets,
            )

            turn_label = f"{base_label} ({sentence!r})"
            assert result.accepted, f"{turn_label}: {result.reason}"
            assert actual_frames(result) == turn["frames"], turn_label
            results.append(result)

        first_targets = results[0].targets
        assert all(
            result.targets == first_targets for result in results
        ), f"{base_label}: sentence alternatives produced different targets"
        previous_targets = first_targets
