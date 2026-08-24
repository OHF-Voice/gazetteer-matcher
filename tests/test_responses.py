import yaml

from gazetteer_matcher import GazetteerMatcher


def test_accepted_interpretation_has_no_rejection_response(matcher):
    result = matcher.interpret("turn on the kitchen lights")
    assert result.accepted
    assert result.rejection_code is None
    assert result.response is None


def test_named_target_without_action_gets_specific_response(matcher):
    result = matcher.interpret("clippy")
    assert not result.accepted
    assert result.rejection_code == "no_action"
    assert result.response == (
        "Sorry, I see you're targeting 'Clippy' (a lawn mower), "
        "but I don't know what action to take."
    )


def test_unsupported_target_action_names_action_and_target(matcher):
    result = matcher.interpret("open the thermostat")
    assert not result.accepted
    assert result.rejection_code == "unsupported_target_action"
    assert result.response == (
        "Sorry, I can't open 'EcoBee' (a thermostat); " "that action doesn't apply."
    )


def test_partial_understanding_does_not_overstate_wrong_action(matcher):
    result = matcher.interpret("tell me a story about clippy")
    assert not result.accepted
    assert result.rejection_code == "unexplained"
    assert result.response == (
        "Sorry, I see you're referring to 'Clippy', "
        "but I didn't understand the whole request."
    )


def test_missing_target_response_names_understood_action(matcher):
    result = matcher.interpret("turn on banana")
    assert not result.accepted
    assert result.rejection_code == "missing_target"
    assert result.response == (
        "Sorry, I think you're trying to turn something on, but I'm not sure."
    )


def test_missing_timer_details_use_standalone_action_description(matcher):
    result = matcher.interpret("start nothing")
    assert not result.accepted
    assert result.rejection_code == "missing_target"
    assert result.response == (
        "Sorry, I think you're trying to start a timer, but I'm not sure."
    )


def test_ambiguous_and_conflicting_scope_responses_are_concise(matcher):
    ambiguous = matcher.interpret("set the bedroom to fifty percent")
    assert ambiguous.rejection_code == "ambiguous"
    assert ambiguous.response == (
        "Sorry, that could mean more than one thing. Please be more specific."
    )

    conflicting = matcher.interpret("turn off the kitchen lights everywhere")
    assert conflicting.rejection_code == "conflicting_scope"
    assert conflicting.response == ("Sorry, that request names conflicting locations.")


def test_missing_anaphora_target_gets_pronoun_response(matcher):
    result = matcher.interpret("turn it back on")
    assert not result.accepted
    assert result.rejection_code == "anaphora_missing_target"
    assert result.response == "Sorry, I'm not sure what it refers to."


def test_response_templates_can_be_replaced(tmp_path, home_path):
    responses_path = tmp_path / "responses.yaml"
    responses_path.write_text(
        yaml.safe_dump(
            {
                "language": "en-test",
                "templates": {
                    "generic": "Custom rejection.",
                },
            }
        ),
        encoding="utf-8",
    )
    matcher = GazetteerMatcher(home_path=home_path, responses=responses_path)

    result = matcher.interpret("clippy")
    assert result.rejection_code == "no_action"
    assert result.response == "Custom rejection."
