import pytest
import yaml

import gazetteer_matcher.config as matcher_config
from gazetteer_matcher import GazetteerMatcher
from gazetteer_matcher.config import MatcherConfig


def frame_tuples(result):
    return [(frame.intent, frame.slots) for frame in result.frames]


def test_support_catalog(matcher):
    summary = matcher.support_summary()
    assert summary["total_combinations"] == 217
    assert summary["wildcard_combinations_excluded"] == 26
    assert summary["non_wildcard_combinations"] == 191
    assert summary["non_wildcard_combinations_with_action_mapping"] == 189
    assert summary["unmapped_intents"] == ["HassBroadcast", "HassRespond"]


def test_configuration_can_be_supplied_as_dicts(home_path):
    defaults = GazetteerMatcher(home_path=home_path).config
    dict_matcher = GazetteerMatcher(
        vocabulary=defaults.vocabulary,
        home=defaults.home,
        intents=defaults.intents,
        responses=defaults.responses,
    )

    result = dict_matcher.interpret("turn on the kitchen lights")
    assert result.accepted
    assert result.frames[0].slots == {"domain": "light", "area": "kitchen"}


def test_explicit_empty_intent_catalog_is_preserved():
    config = MatcherConfig.load(intents={})

    assert not config.intents


def test_missing_packaged_intent_metadata_fails_loudly(monkeypatch):
    monkeypatch.setattr(matcher_config, "get_intent_info", lambda: None)

    with pytest.raises(
        RuntimeError,
        match="home-assistant-intents package metadata is unavailable",
    ):
        MatcherConfig.load()


def test_default_home_is_empty():
    config = MatcherConfig.load()

    assert config.home == {"areas": {}, "floors": {}, "entities": {}}


def test_legacy_path_keywords_also_accept_dicts(home_path):
    defaults = GazetteerMatcher(home_path=home_path).config
    dict_matcher = GazetteerMatcher(home_path=defaults.home)

    assert dict_matcher.interpret("turn on the kitchen lights").accepted


def test_configuration_rejects_source_and_path_together(home_path):
    home = GazetteerMatcher(home_path=home_path).config.home

    with pytest.raises(
        TypeError,
        match="pass either home or home_path, not both",
    ):
        GazetteerMatcher(home=home, home_path=home)


def test_exact_turn_on(matcher):
    result = matcher.interpret("turn on the kitchen lights")
    assert result.accepted
    assert frame_tuples(result) == [
        ("HassTurnOn", {"area": "kitchen", "domain": "light"})
    ]


def test_fuzzy_area(matcher):
    result = matcher.interpret("flick on the kichen lights")
    assert result.accepted
    assert result.frames[0].slots == {"area": "kitchen", "domain": "light"}
    assert any(
        span.tag == "area" and span.source == "fuzzy_area" for span in result.spans
    )


def test_fuzzy_action(matcher):
    result = matcher.interpret("flik on the kitchen lights")
    assert result.accepted
    assert result.frames[0].intent == "HassTurnOn"
    assert any(
        span.tag == "action" and span.source == "fuzzy_action" for span in result.spans
    )


def test_entity_name_can_elide_an_interior_word():
    elision_matcher = GazetteerMatcher(
        home={
            "areas": {},
            "floors": {},
            "entities": {
                "light.joshs_office_lights": {
                    "name": "Josh's Office Lights",
                    "domain": "light",
                }
            },
        }
    )

    result = elision_matcher.interpret("are josh's lights on")

    assert result.accepted
    assert result.frames[0].intent == "HassGetState"
    assert result.frames[0].combination == "name_state"
    assert result.frames[0].slots == {
        "name": "light.joshs_office_lights",
        "state": "on",
    }
    assert any(
        span.tag == "name" and span.source == "fuzzy_name_elision"
        for span in result.spans
    )


def test_elided_entity_name_remains_ambiguous_when_not_unique():
    elision_matcher = GazetteerMatcher(
        home={
            "areas": {},
            "floors": {},
            "entities": {
                "light.joshs_office_lights": {
                    "name": "Josh's Office Lights",
                    "domain": "light",
                },
                "light.joshs_desk_lights": {
                    "name": "Josh's Desk Lights",
                    "domain": "light",
                },
            },
        }
    )

    result = elision_matcher.interpret("are josh's lights on")

    assert not result.accepted
    assert result.ambiguous


def test_separable_action(matcher):
    result = matcher.interpret("flick the kitchen lights on")
    assert result.accepted
    assert result.frames[0].intent == "HassTurnOn"
    assert result.frames[0].slots == {"area": "kitchen", "domain": "light"}


def test_entity_name_wins(matcher):
    result = matcher.interpret("turn on the bedroom lamp")
    assert result.accepted
    assert result.frames[0].slots == {"name": "light.bedroom_lamp"}


def test_reordered_area_qualified_entity_name_wins(matcher):
    result = matcher.interpret("turn on the ceiling lights in the living room")

    assert result.accepted
    assert frame_tuples(result) == [
        ("HassTurnOn", {"name": "light.living_room_ceiling"})
    ]
    assert any(
        span.tag == "name" and span.source == "area_qualified_name"
        for span in result.spans
    )


def test_bare_alias_is_not_reassigned_by_area_qualified_names(matcher):
    result = matcher.interpret("turn on the ceiling lights")

    assert result.accepted
    assert frame_tuples(result) == [("HassTurnOn", {"name": "light.kitchen_ceiling"})]


def test_action_can_wrap_an_exact_entity_target(matcher):
    result = matcher.interpret("return rover to base")

    assert result.accepted
    assert frame_tuples(result) == [
        ("HassVacuumReturnToBase", {"name": "vacuum.rover"})
    ]
    assert result.frames[0].action_span.source == "interstitial_target"
    assert result.frames[0].action_span.meta["consumed_indexes"] == [0, 2, 3]
    assert not any(
        span.tag == "action" and span.value == "mower_dock" for span in result.spans
    )


def test_open_cover(matcher):
    result = matcher.interpret("open the bedroom blinds")
    assert result.accepted
    assert result.frames[0].intent == "HassTurnOn"
    assert result.frames[0].slots == {"name": "cover.bedroom_blinds"}


def test_open_light_rejected(matcher):
    result = matcher.interpret("open the kitchen lights")
    assert not result.accepted


def test_floor(matcher):
    result = matcher.interpret("turn on the lights on the second floor")
    assert result.accepted
    assert result.frames[0].slots == {"floor": "upstairs", "domain": "light"}


def test_conjunction_targets(matcher):
    result = matcher.interpret("turn on the kitchen and hallway lights")
    assert result.accepted
    assert frame_tuples(result) == [
        ("HassTurnOn", {"area": "kitchen", "domain": "light"}),
        ("HassTurnOn", {"area": "hallway", "domain": "light"}),
    ]


def test_conjunction_intents(matcher):
    result = matcher.interpret(
        "turn off the kitchen lights and open the bedroom blinds"
    )
    assert result.accepted
    assert frame_tuples(result) == [
        ("HassTurnOff", {"area": "kitchen", "domain": "light"}),
        ("HassTurnOn", {"name": "cover.bedroom_blinds"}),
    ]


def test_conjunction_properties(matcher):
    result = matcher.interpret("set the living room lights red and fifty percent")
    assert result.accepted
    assert frame_tuples(result) == [
        (
            "HassLightSet",
            {"area": "living_room", "color": "red", "domain": "light"},
        ),
        (
            "HassLightSet",
            {"area": "living_room", "brightness": 50, "domain": "light"},
        ),
    ]


def test_possessive_property_clause_inherits_named_target(matcher):
    result = matcher.interpret(
        "turn on the hallway light and set its brightness to 40%"
    )

    assert result.accepted
    assert frame_tuples(result) == [
        ("HassTurnOn", {"name": "light.hallway"}),
        (
            "HassLightSet",
            {"name": "light.hallway", "brightness": 40},
        ),
    ]
    assert result.frames[1].inherited_slots == 1
    assert result.frames[1].unexplained_tokens == []


def test_singular_possessive_does_not_refer_to_a_group(matcher):
    result = matcher.interpret(
        "turn on the kitchen lights and set its brightness to 40%"
    )

    assert not result.accepted
    assert result.rejection_code == "anaphora_singular_group"


def test_possessive_property_requires_a_preceding_target(matcher):
    result = matcher.interpret("set its brightness to 40%")

    assert not result.accepted
    assert result.rejection_code == "anaphora_missing_target"
    assert result.response == "Sorry, I'm not sure what its refers to."


def test_number_words(matcher):
    result = matcher.interpret("set the bedroom brightness to fifty percent")
    assert result.accepted
    assert result.frames[0].intent == "HassLightSet"
    assert result.frames[0].slots["brightness"] == 50


def test_number_joiner_does_not_become_conjunction(matcher):
    result = matcher.interpret("start a timer for one hundred and twenty seconds")
    assert result.accepted
    assert len(result.frames) == 1
    assert result.frames[0].intent == "HassStartTimer"
    assert result.frames[0].slots == {"seconds": 120}


@pytest.mark.parametrize(
    ("text", "slots"),
    [
        ("start a timer for half an hour", {"minutes": 30}),
        (
            "start a timer for one and a half hours",
            {"hours": 1, "minutes": 30},
        ),
    ],
)
def test_fractional_timer_durations(matcher, text, slots):
    result = matcher.interpret(text)
    assert result.accepted
    assert result.frames[0].intent == "HassStartTimer"
    assert result.frames[0].slots == slots


def test_timer_relations_assign_base_and_adjustment_roles(matcher):
    result = matcher.interpret("increase timer for 5 minutes by 1 hour")
    assert result.accepted
    assert result.frames[0].intent == "HassIncreaseTimer"
    assert result.frames[0].slots == {"start_minutes": 5, "hours": 1}


def test_spoken_decimal_temperature(matcher):
    result = matcher.interpret("set bedroom temperature to twenty point five degrees")
    assert result.accepted
    assert result.frames[0].intent == "HassClimateSetTemperature"
    assert result.frames[0].slots["temperature"] == 20.5


def test_qualitative_brightness(matcher):
    result = matcher.interpret("set bedroom brightness to maximum")
    assert result.accepted
    assert result.frames[0].intent == "HassLightSet"
    assert result.frames[0].slots["brightness"] == 100


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("start the timer for one minute", "HassStartTimer"),
        ("cancel the timer", "HassCancelTimer"),
        ("pause the timer", "HassPauseTimer"),
        ("resume the timer", "HassUnpauseTimer"),
    ],
)
def test_timer_actions_allow_interstitial_skip_words(matcher, text, intent):
    result = matcher.interpret(text)
    assert result.accepted
    assert result.frames[0].intent == intent
    action_span = result.frames[0].action_span
    assert action_span.source == "interstitial_skip"
    assert action_span.meta["consumed_indexes"] == [0, 2]


@pytest.mark.parametrize(
    ("text", "consumed_indexes"),
    [
        ("cancel all the timers", [0, 1, 3]),
        ("cancel all of my timers", [0, 1, 4]),
    ],
)
def test_interstitial_skip_preserves_cancel_all_action(matcher, text, consumed_indexes):
    result = matcher.interpret(text)
    assert result.accepted
    assert result.frames[0].intent == "HassCancelAllTimers"
    assert result.frames[0].action_span.meta["consumed_indexes"] == consumed_indexes


def test_constrained_timer_anchor_allows_target_between_action_and_marker(matcher):
    result = matcher.interpret("cancel the kitchen timer")
    assert result.accepted
    assert result.frames[0].intent == "HassCancelTimer"
    assert result.frames[0].slots == {"area": "kitchen"}


def test_interstitial_skip_does_not_cross_semantic_content(matcher):
    result = matcher.interpret("cancel and start timer")
    assert not result.accepted


def test_climate_temperature(matcher):
    result = matcher.interpret("set the bedroom temperature to seventy two degrees")
    assert result.accepted
    assert result.frames[0].intent == "HassClimateSetTemperature"
    assert result.frames[0].slots == {"area": "bedroom", "temperature": 72}


def test_light_color_temperature(matcher):
    result = matcher.interpret("set the bedroom color temperature to 2700 kelvin")
    assert result.accepted
    assert result.frames[0].intent == "HassLightSet"
    assert result.frames[0].slots == {
        "area": "bedroom",
        "temperature": 2700,
        "domain": "light",
    }


def test_named_light_temperature(matcher):
    result = matcher.interpret("set the bedroom lamp temperature to warm white")
    assert result.accepted
    assert result.frames[0].intent == "HassLightSet"
    assert result.frames[0].slots == {
        "name": "light.bedroom_lamp",
        "temperature": 2700,
    }


def test_get_entity_state(matcher):
    result = matcher.interpret("is the bedroom lamp on")
    assert result.accepted
    assert result.frames[0].intent == "HassGetState"
    assert result.frames[0].slots == {
        "name": "light.bedroom_lamp",
        "state": "on",
    }


def test_get_temperature(matcher):
    result = matcher.interpret("what is the temperature in the bedroom")
    assert result.accepted
    assert result.frames[0].intent == "HassClimateGetTemperature"
    assert result.frames[0].slots == {"area": "bedroom"}


def test_current_date(matcher):
    result = matcher.interpret("what is the date")
    assert result.accepted
    assert result.frames[0].intent == "HassGetCurrentDate"


def test_one_unimportant_unexplained_token_allowed(matcher):
    result = matcher.interpret("turn on the kitchen lights banana")
    assert result.accepted
    assert result.frames[0].unexplained_important_tokens == []
    assert result.frames[0].unexplained_unimportant_tokens == [5]


def test_two_unimportant_unexplained_tokens_allowed(matcher):
    result = matcher.interpret("turn on the kitchen lights banana potato")
    assert result.accepted
    assert result.frames[0].unexplained_important_tokens == []
    assert result.frames[0].unexplained_unimportant_tokens == [5, 6]


def test_three_unimportant_unexplained_tokens_rejected(matcher):
    result = matcher.interpret("turn on the kitchen lights banana potato tomato")
    assert not result.accepted
    candidate = result.segments[0].frame_candidates[0]
    assert candidate.unexplained_important_tokens == []
    assert candidate.unexplained_unimportant_tokens == [5, 6, 7]


def test_one_important_unexplained_token_allowed(matcher):
    result = matcher.interpret("turn on seventeen kitchen lights")
    assert result.accepted
    assert result.frames[0].unexplained_important_tokens == [2]
    assert result.frames[0].unexplained_unimportant_tokens == []


def test_two_important_unexplained_tokens_rejected(matcher):
    result = matcher.interpret("turn on seventeen red kitchen lights")
    assert not result.accepted
    candidate = result.segments[0].frame_candidates[0]
    assert candidate.unexplained_important_tokens == [2, 3]
    assert candidate.unexplained_unimportant_tokens == []


def test_important_and_unimportant_limits_are_independent(matcher):
    result = matcher.interpret("turn on seventeen kitchen lights banana potato")
    assert result.accepted
    assert result.frames[0].unexplained_important_tokens == [2]
    assert result.frames[0].unexplained_unimportant_tokens == [5, 6]


def test_ambiguous_set_rejected(matcher):
    result = matcher.interpret("set the bedroom to fifty percent")
    assert not result.accepted
    assert result.ambiguous


def test_volume_relative(matcher):
    result = matcher.interpret("increase the bedroom volume by twenty percent")
    assert result.accepted
    assert result.frames[0].intent == "HassSetVolumeRelative"
    assert result.frames[0].slots == {"volume_step": 20, "area": "bedroom"}


@pytest.mark.parametrize(
    ("text", "intent", "slots"),
    [
        (
            "turn the volume down to 90 percent",
            "HassSetVolume",
            {"volume_level": 90, "area": "kitchen"},
        ),
        (
            "turn the volume down by 20 percent",
            "HassSetVolumeRelative",
            {"volume_step": -20, "area": "kitchen"},
        ),
    ],
)
def test_volume_relation_controls_absolute_vs_relative(matcher, text, intent, slots):
    result = matcher.interpret(text, context_area="Kitchen")
    assert result.accepted
    assert result.frames[0].intent == intent
    assert result.frames[0].slots == slots


def test_entity_first_power_command(matcher):
    result = matcher.interpret("kitchen lights on")
    assert result.accepted
    assert result.frames[0].intent == "HassTurnOn"
    assert result.frames[0].slots == {"area": "kitchen", "domain": "light"}


@pytest.mark.parametrize("intent_word", ["on", "off"])
def test_context_area_materializes_for_bare_domain_command(matcher, intent_word):
    result = matcher.interpret(f"turn {intent_word} the lights", context_area="Kitchen")
    assert result.accepted
    assert result.frames[0].combination == "domain_only"
    assert result.frames[0].slots == {
        "domain": "light",
        "area": "kitchen",
    }


@pytest.mark.parametrize("intent_word", ["on", "off"])
def test_bare_domain_command_requires_context_area(matcher, intent_word):
    result = matcher.interpret(f"turn {intent_word} the lights")

    assert not result.accepted
    assert result.frames == []


def test_brightness_only_requires_and_materializes_context_area(matcher):
    without_context = matcher.interpret("set brightness to 50%")
    assert not without_context.accepted
    assert without_context.frames == []

    with_context = matcher.interpret(
        "set brightness to 50%",
        context_area="Kitchen",
    )
    assert with_context.accepted
    assert with_context.frames[0].combination == "brightness_only"
    assert with_context.frames[0].slots == {
        "brightness": 50,
        "domain": "light",
        "area": "kitchen",
    }


def test_explicit_whole_house_scope_ignores_context_area(matcher):
    result = matcher.interpret("turn off all the lights", context_area="Kitchen")
    assert result.accepted
    assert result.frames[0].combination == "domain_all"
    assert result.frames[0].slots == {"domain": "light"}


@pytest.mark.parametrize(
    "text",
    [
        "turn off all the kitchen lights",
        "turn all the lights in the kitchen off",
    ],
)
def test_all_quantifier_uses_explicit_area_scope(matcher, text):
    result = matcher.interpret(text)
    assert result.accepted
    assert result.frames[0].combination == "area_domain"
    assert result.frames[0].slots == {
        "area": "kitchen",
        "domain": "light",
    }


def test_all_quantifier_uses_explicit_floor_scope(matcher):
    result = matcher.interpret("turn off all the lights on the first floor")
    assert result.accepted
    assert result.frames[0].combination == "floor_domain"
    assert result.frames[0].slots == {
        "floor": "ground",
        "domain": "light",
    }


def test_all_in_here_uses_context_area(matcher):
    result = matcher.interpret(
        "turn off all the lights in here", context_area="Kitchen"
    )
    assert result.accepted
    assert result.frames[0].combination == "domain_only"
    assert result.frames[0].slots == {
        "domain": "light",
        "area": "kitchen",
    }


def test_all_uses_context_when_domain_has_no_global_combination(matcher):
    result = matcher.interpret("turn off all the fans", context_area="Kitchen")
    assert result.accepted
    assert result.frames[0].combination == "domain_only"
    assert result.frames[0].slots == {
        "domain": "fan",
        "area": "kitchen",
    }


@pytest.mark.parametrize(
    "text",
    [
        "turn all lights off",
        "turn off every light",
        "turn off the lights everywhere",
    ],
)
def test_unscoped_all_or_home_scope_uses_global_combination(matcher, text):
    result = matcher.interpret(text, context_area="Kitchen")
    assert result.accepted
    assert result.frames[0].combination == "domain_all"
    assert result.frames[0].slots == {"domain": "light"}


def test_conflicting_local_and_home_scope_is_rejected(matcher):
    result = matcher.interpret("turn off the kitchen lights everywhere")
    assert not result.accepted
    assert "conflicts with a local target" in (result.reason or "")


def test_named_entity_can_be_reused_by_it(matcher):
    previous = matcher.interpret("turn off the bedroom lamp")
    assert previous.accepted
    assert previous.targets[0].scope == "entity"
    assert previous.targets[0].slots == {"name": "light.bedroom_lamp"}

    result = matcher.interpret("turn it back on", previous_targets=previous.targets)
    assert result.accepted
    assert result.frames[0].intent == "HassTurnOn"
    assert result.frames[0].combination == "name_only"
    assert result.frames[0].slots == {"name": "light.bedroom_lamp"}


def test_area_group_can_be_reused_by_them(matcher):
    previous = matcher.interpret("turn on the kitchen lights")
    assert previous.accepted
    assert previous.targets[0].scope == "area"

    result = matcher.interpret("turn them off", previous_targets=previous.targets)
    assert result.accepted
    assert result.frames[0].intent == "HassTurnOff"
    assert result.frames[0].combination == "area_domain"
    assert result.frames[0].slots == {
        "area": "kitchen",
        "domain": "light",
    }


@pytest.mark.parametrize("text", ["close them", "close it"])
def test_named_blinds_can_be_reused_by_pronoun(matcher, text):
    previous = matcher.interpret("open the bedroom blinds")
    result = matcher.interpret(text, previous_targets=previous.targets)
    assert result.accepted
    assert result.frames[0].intent == "HassTurnOff"
    assert result.frames[0].slots == {"name": "cover.bedroom_blinds"}


def test_again_is_consumed_for_anaphoric_target(matcher):
    previous = matcher.interpret("close the bedroom blinds")
    result = matcher.interpret("open them again", previous_targets=previous.targets)
    assert result.accepted
    assert result.frames[0].intent == "HassTurnOn"
    assert result.frames[0].slots == {"name": "cover.bedroom_blinds"}
    assert result.frames[0].unexplained_tokens == []


@pytest.mark.parametrize(
    ("follow_up", "intent"),
    [("unlock it", "HassTurnOff"), ("lock it", "HassTurnOn")],
)
def test_named_lock_from_state_query_can_be_reused_by_it(matcher, follow_up, intent):
    previous = matcher.interpret("is the front door locked")
    assert previous.accepted
    assert previous.frames[0].intent == "HassGetState"
    assert len(previous.targets) == 1
    assert previous.targets[0].slots == {"name": "lock.front_door"}

    result = matcher.interpret(follow_up, previous_targets=previous.targets)
    assert result.accepted
    assert result.frames[0].intent == intent
    assert result.frames[0].slots == {"name": "lock.front_door"}


def test_repeated_target_across_frames_is_exported_once_for_follow_up(matcher):
    previous = matcher.interpret(
        "turn on the hallway light and set its brightness to 40%"
    )
    assert previous.accepted
    assert len(previous.frames) == 2
    assert len(previous.targets) == 1
    assert previous.targets[0].slots == {"name": "light.hallway"}

    result = matcher.interpret("turn it off", previous_targets=previous.targets)
    assert result.accepted
    assert result.frames[0].intent == "HassTurnOff"
    assert result.frames[0].slots == {"name": "light.hallway"}


@pytest.mark.parametrize("text", ["turn it back on", "close them"])
def test_anaphora_requires_a_previous_target(matcher, text):
    result = matcher.interpret(text)
    assert not result.accepted
    assert "no previous target" in (result.reason or "")


def test_it_rejects_a_group_target(matcher):
    previous = matcher.interpret("turn on the kitchen lights")
    result = matcher.interpret("turn it off", previous_targets=previous.targets)
    assert not result.accepted
    assert "single named entity" in (result.reason or "")


def test_anaphora_rejects_an_incompatible_action(matcher):
    previous = matcher.interpret("turn on the bedroom lamp")
    result = matcher.interpret("close it", previous_targets=previous.targets)
    assert not result.accepted
    assert "incompatible with virtual action" in (result.reason or "")


def test_anaphora_cannot_be_mixed_with_an_explicit_target(matcher):
    previous = matcher.interpret("turn off the hallway light")
    result = matcher.interpret(
        "turn it and the bedroom lamp on",
        previous_targets=previous.targets,
    )
    assert not result.accepted
    assert "explicit target" in (result.reason or "")


def test_multiple_previous_targets_are_not_resolved(matcher):
    previous = matcher.interpret("turn on the kitchen and hallway lights")
    assert len(previous.targets) == 2

    result = matcher.interpret("turn them off", previous_targets=previous.targets)
    assert not result.accepted
    assert result.reason == "multiple previous targets are not supported"


def test_previous_target_is_ignored_without_anaphor(matcher):
    previous = matcher.interpret("turn off the bedroom lamp")
    result = matcher.interpret(
        "turn on the kitchen lights",
        previous_targets=previous.targets,
    )
    assert result.accepted
    assert result.frames[0].slots == {
        "area": "kitchen",
        "domain": "light",
    }


def test_grammatical_it_does_not_trigger_device_anaphora(matcher):
    previous = matcher.interpret("turn off the bedroom lamp")
    result = matcher.interpret(
        "what time is it",
        previous_targets=previous.targets,
    )
    assert result.accepted
    assert result.frames[0].intent == "HassGetCurrentTime"


def test_home_target_is_not_silently_narrowed_for_follow_up(matcher):
    previous = matcher.interpret("turn off all the lights")
    assert previous.targets[0].scope == "home"

    result = matcher.interpret(
        "turn them back on",
        context_area="Kitchen",
        previous_targets=previous.targets,
    )
    assert not result.accepted
    assert result.reason == "previous target is not supported by this action"


def test_home_person_state_is_not_treated_as_device_scope(tmp_path):
    home_path = tmp_path / "home.yaml"
    home_path.write_text(
        yaml.safe_dump(
            {
                "areas": {},
                "floors": {},
                "entities": {
                    "person.jane": {
                        "name": "Jane",
                        "domain": "person",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    person_matcher = GazetteerMatcher(home_path=home_path)

    result = person_matcher.interpret("is jane in the home")
    assert result.accepted
    assert result.frames[0].intent == "HassGetState"
    assert result.frames[0].combination == "name_zone"
    assert result.frames[0].slots == {
        "name": "person.jane",
        "state": "home",
    }


def test_context_disambiguates_duplicate_entity_names(tmp_path):
    home_path = tmp_path / "home.yaml"
    home_path.write_text(
        yaml.safe_dump(
            {
                "areas": {
                    "kitchen": {"name": "Kitchen", "floor": "ground"},
                    "bedroom": {"name": "Bedroom", "floor": "upstairs"},
                },
                "floors": {
                    "ground": {"name": "Ground Floor"},
                    "upstairs": {"name": "Upstairs"},
                },
                "entities": {
                    "light.kitchen_ceiling": {
                        "name": "Ceiling Light",
                        "domain": "light",
                        "area": "kitchen",
                    },
                    "light.bedroom_ceiling": {
                        "name": "Ceiling Light",
                        "domain": "light",
                        "area": "bedroom",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    duplicate_matcher = GazetteerMatcher(home_path=home_path)

    without_context = duplicate_matcher.interpret("turn on ceiling light")
    assert not without_context.accepted
    assert without_context.ambiguous

    in_kitchen = duplicate_matcher.interpret(
        "turn on ceiling light", context_area="kitchen"
    )
    assert in_kitchen.accepted
    assert in_kitchen.frames[0].slots == {"name": "light.kitchen_ceiling"}

    upstairs = duplicate_matcher.interpret(
        "turn on ceiling light", context_floor="Upstairs"
    )
    assert upstairs.accepted
    assert upstairs.frames[0].slots == {"name": "light.bedroom_ceiling"}


def test_context_area_derives_and_validates_floor(matcher):
    result = matcher.interpret("turn on bedroom lamp", context_area="Bedroom")
    assert result.accepted
    assert result.frames[0].slots == {"name": "light.bedroom_lamp"}

    remote_unique_name = matcher.interpret(
        "turn on bedroom lamp", context_area="Kitchen"
    )
    assert remote_unique_name.accepted
    assert remote_unique_name.frames[0].slots == {"name": "light.bedroom_lamp"}

    with pytest.raises(ValueError, match="is not on context floor"):
        matcher.interpret(
            "turn on bedroom lamp",
            context_area="Bedroom",
            context_floor="Ground Floor",
        )


def test_lock_state_disambiguates_door_domain(matcher):
    result = matcher.interpret("are any doors unlocked")
    assert result.accepted
    assert result.frames[0].intent == "HassGetState"
    assert result.frames[0].combination == "domain_state"
    assert result.frames[0].slots == {"domain": "lock", "state": "unlocked"}
