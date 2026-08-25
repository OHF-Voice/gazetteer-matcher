"""Tests for the surface an embedding application uses."""

from pathlib import Path

import pytest
import yaml

from gazetteer_matcher import GazetteerMatcher, TargetReference

HOME = yaml.safe_load((Path(__file__).parent / "home.yaml").read_text())

OTHER_HOME = {
    "areas": {"garage": {"name": "Garage"}},
    "floors": {},
    "entities": {
        "cover.garage_door": {
            "name": "Garage Door",
            "domain": "cover",
            "area": "garage",
        }
    },
}


@pytest.fixture(name="matcher")
def matcher_fixture() -> GazetteerMatcher:
    return GazetteerMatcher(home=HOME)


def test_set_home_replaces_the_gazetteer(matcher: GazetteerMatcher) -> None:
    """Test a matcher can follow a home that changes while it runs."""
    assert matcher.interpret("turn on the kitchen lights").accepted
    assert not matcher.interpret("open the garage door").accepted

    matcher.set_home(OTHER_HOME)

    assert matcher.interpret("open the garage door").accepted
    assert not matcher.interpret("turn on the kitchen lights").accepted


def test_set_home_keeps_the_rest_of_the_configuration(
    matcher: GazetteerMatcher,
) -> None:
    """Test only the home changes: vocabulary, catalog and number words survive."""
    vocabulary = matcher.config.vocabulary
    catalog = matcher.catalog

    matcher.set_home(OTHER_HOME)

    assert matcher.config.vocabulary is vocabulary
    assert matcher.catalog is catalog
    assert matcher.interpret("set a timer for two minutes").accepted


@pytest.mark.parametrize(
    ("slot", "value", "expected"),
    [
        ("name", "lock.front_door", "Front Door"),
        ("area", "kitchen", "Kitchen"),
        ("brightness", 50, "50"),
        ("name", "light.not_in_this_home", "light.not_in_this_home"),
    ],
    ids=["entity", "area", "not_a_reference", "unknown"],
)
def test_display_name(
    matcher: GazetteerMatcher, slot: str, value: object, expected: str
) -> None:
    """Test ids can be turned back into what a person calls them."""
    assert matcher.display_name(slot, value) == expected


def test_refusal_target_names_what_was_resolved(matcher: GazetteerMatcher) -> None:
    """Test a refusal that got as far as the home says so."""
    result = matcher.interpret("write a poem about my kitchen lights")

    assert not result.accepted
    assert result.refusal_target
    assert result.refusal_target in (result.response or "")


@pytest.mark.parametrize("text", ["asdfgh", "do something", "hello"])
def test_refusal_target_is_none_when_nothing_resolved(
    matcher: GazetteerMatcher, text: str
) -> None:
    """Test noise explains nothing, so a caller can prefer its own error."""
    result = matcher.interpret(text)

    assert not result.accepted
    assert result.refusal_target is None


def test_frames_carry_a_response_key_from_the_corpus(
    matcher: GazetteerMatcher,
) -> None:
    """Test keys come from the corpus, narrowed by the target's domain."""
    lights = matcher.interpret("turn on the kitchen lights")
    blinds = matcher.interpret("open the bedroom blinds")

    assert lights.frames[0].response_key == "lights_area"
    assert blinds.frames[0].response_key == "cover"


def test_unqualified_area_state_question_uses_any_response(
    matcher: GazetteerMatcher,
) -> None:
    """Test a plural area question asks whether any matching entity is on."""
    result = matcher.interpret("are the kitchen lights on")

    assert result.accepted
    assert result.frames[0].response_key == "any"


@pytest.mark.parametrize(
    ("slots", "scope", "message"),
    [
        ({}, "entity", "non-empty subset"),
        ({"colour": "red"}, "entity", "non-empty subset"),
        ({"area": "kitchen"}, "entity", "requires a 'name'"),
        ({"name": "light.a"}, "floor", "requires a 'floor'"),
        ({"name": "light.a"}, "home", "local selector"),
        ({"name": "light.a"}, "somewhere", "unsupported target scope"),
    ],
)
def test_target_reference_is_validated_when_built(
    slots: dict, scope: str, message: str
) -> None:
    """Test a bad target fails where it was written, not deep inside interpret."""
    with pytest.raises(ValueError, match=message):
        TargetReference(slots=slots, scope=scope)  # type: ignore[arg-type]


def test_target_reference_constructors(matcher: GazetteerMatcher) -> None:
    """Test the shorthand builds targets a follow-up can actually use."""
    previous = (TargetReference.for_entity("lock.front_door"),)
    result = matcher.interpret("unlock it", previous_targets=previous)

    assert result.accepted
    assert result.frames[0].slots == {"name": "lock.front_door"}

    area = TargetReference.for_area("kitchen", domain="light")
    assert area.scope == "area"
    assert area.slots == {"area": "kitchen", "domain": "light"}
