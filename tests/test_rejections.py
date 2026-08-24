from pathlib import Path

import pytest
import yaml

REJECTIONS_PATH = Path(__file__).parent / "rejections.yaml"
REJECTION_CASES = (yaml.safe_load(REJECTIONS_PATH.read_text()) or {}).get("cases", [])


@pytest.mark.parametrize(
    "case",
    REJECTION_CASES,
    ids=lambda case: f"{case['category']}:{case['sentence']}",
)
def test_english_hard_rejection(matcher, case):
    result = matcher.interpret(
        case["sentence"],
        context_area=case.get("context_area"),
        context_floor=case.get("context_floor"),
    )
    assert not result.accepted, (
        f"unexpectedly accepted {case['sentence']!r} as "
        f"{[(frame.intent, frame.slots) for frame in result.frames]!r}"
    )
    assert result.rejection_code
    assert result.response
    assert len(result.response) <= 180
