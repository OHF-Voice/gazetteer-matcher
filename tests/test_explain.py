"""Tests for the short plain-language notes on how a sentence was read."""

import pytest

from gazetteer_matcher import GazetteerMatcher, Note, explain

HOME = {
    "floors": {"upstairs": {"name": "Upstairs", "aliases": []}},
    "areas": {
        "kitchen": {"name": "Kitchen", "aliases": [], "floor": None},
        "bedroom": {"name": "Bedroom", "aliases": [], "floor": "upstairs"},
    },
    "entities": {
        "light.kitchen_ceiling": {
            "name": "Kitchen Ceiling Lights",
            "aliases": [],
            "domain": "light",
            "area": "kitchen",
        },
        "cover.bedroom_blinds": {
            "name": "Bedroom Blinds",
            "aliases": [],
            "domain": "cover",
            "area": "bedroom",
        },
        "media_player.bedroom_tv": {
            "name": "Bedroom TV",
            "aliases": ["tv"],
            "domain": "media_player",
            "area": "bedroom",
        },
        "media_player.living_room": {
            "name": "Living Room Speakers",
            "aliases": ["stereo", "tv"],
            "domain": "media_player",
            "area": "kitchen",
        },
    },
}


@pytest.fixture(name="matcher")
def matcher_fixture() -> GazetteerMatcher:
    return GazetteerMatcher(home=HOME)


def _notes(
    matcher: GazetteerMatcher, text: str, context_area: str | None = None
) -> dict[str, list[Note]]:
    """Return the notes for a sentence, grouped by code."""
    grouped: dict[str, list[Note]] = {}
    result = matcher.interpret(text, context_area=context_area)
    for note in explain(matcher, result):
        grouped.setdefault(note.code, []).append(note)
    return grouped


def test_keywords_name_what_was_recognized(matcher: GazetteerMatcher) -> None:
    """Test the words that carried meaning are listed in the order they were said."""
    note = _notes(matcher, "turn on the kitchen lights")["keywords"][0]

    assert note.message == "Keywords matched: turn on, kitchen, lights"


def test_keywords_collapse_overlapping_readings(matcher: GazetteerMatcher) -> None:
    """Test a name is not listed again as the shorter words inside it."""
    note = _notes(matcher, "close the bedroom blinds")["keywords"][0]

    assert note.message == "Keywords matched: close, bedroom blinds"


def test_an_action_word_names_the_intents_it_reaches(
    matcher: GazetteerMatcher,
) -> None:
    """Test the action word is reported as pointing at its intents."""
    note = _notes(matcher, "close the bedroom blinds")["intent_hint"][0]

    assert note.message == "'close' suggests HassTurnOff or HassSetPosition."
    assert note.detail["intents"] == ["HassTurnOff", "HassSetPosition"]


def test_a_word_reaching_the_catalog_names_every_intent(
    matcher: GazetteerMatcher,
) -> None:
    """Test a word like "set" names all of them rather than counting the rest.

    A later note can weigh the winner against any of these, and one that was only
    counted reads as having come from nowhere.
    """
    note = _notes(matcher, "set the kitchen to 50%")["intent_hint"][0]

    assert note.message == (
        "'set' suggests HassSetPosition, HassLightSet, HassClimateSetTemperature, "
        "HassSetVolume, HassFanSetSpeed, or HassStartTimer."
    )
    assert all(intent in note.message for intent in note.detail["intents"])


def test_a_misspelled_name_is_flagged_as_uncertain(matcher: GazetteerMatcher) -> None:
    """Test a name reached by spelling reports what it was read as, and how well."""
    note = _notes(matcher, "turn on the kichen lights")["low_confidence"][0]

    assert note.message == (
        "Read 'kichen' as the area Kitchen, but it is only a 86% match."
    )
    assert note.detail["similarity"] < 1.0


def test_an_exact_name_is_not_flagged(matcher: GazetteerMatcher) -> None:
    """Test a name said correctly is not reported as uncertain."""
    assert "low_confidence" not in _notes(matcher, "turn on the kitchen lights")


def test_a_missing_slot_names_what_was_not_said(matcher: GazetteerMatcher) -> None:
    """Test a sentence that named a target but no device says which half is missing."""
    note = _notes(matcher, "turn on the bedroom")["missing_slot"][0]

    assert note.message == (
        "Could be HassTurnOn: found a room, but it's missing a kind of device. "
        "Expected something like 'turn on the lights in the kitchen'."
    )
    assert note.detail["missing"] == "domain"
    assert note.detail["satisfied"] == ["area"]


def test_a_reading_that_failed_its_own_rule_says_so(matcher: GazetteerMatcher) -> None:
    """Test a constructed reading reports its violation, not a guess at what is missing.

    Guessing from the shapes that were never built would blame a missing slot for
    a sentence whose slots were all found and one of them was out of range.
    """
    notes = _notes(matcher, "set the bedroom blinds to 250%")

    assert "missing_slot" not in notes
    assert notes["rejected_reading"][0].message == (
        "Guessed HassSetPosition with name=Bedroom Blinds (cover.bedroom_blinds), "
        "position=250, "
        "but percentage must be an integer from 0 to 100."
    )


def test_an_ambiguous_sentence_names_the_rival_reading(
    matcher: GazetteerMatcher,
) -> None:
    """Test ambiguity is reported as another intent fitting, not as a raw reason."""
    note = _notes(matcher, "set the kitchen to 50%")["rejected_reading"][0]

    assert note.message.endswith("reads it just as well.")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("asdfgh qwerty", "None of this was recognized."),
        ("the kitchen lights", "Nothing here looked like an action."),
    ],
    ids=["noise", "target_only"],
)
def test_a_sentence_with_no_reading_says_which_half_landed(
    matcher: GazetteerMatcher, text: str, message: str
) -> None:
    """Test a sentence nothing was built from still says what was understood."""
    assert _notes(matcher, text)["nothing_built"][0].message == message


def test_an_accepted_sentence_reports_what_was_chosen(
    matcher: GazetteerMatcher,
) -> None:
    """Test the intent settled on is named with the targets it resolved."""
    note = _notes(matcher, "turn on the kitchen lights")["chosen"][0]

    assert note.message == "Chose HassTurnOn with area=Kitchen, domain=light."


def test_each_command_of_a_coordinated_sentence_is_reported(
    matcher: GazetteerMatcher,
) -> None:
    """Test a sentence holding two commands names both intents."""
    notes = _notes(matcher, "open the bedroom blinds and turn on the kitchen lights")[
        "chosen"
    ]

    assert [note.detail["intent"] for note in notes] == ["HassTurnOn", "HassTurnOn"]
    assert [note.detail["slots"] for note in notes] == [
        {"name": "cover.bedroom_blinds"},
        {"area": "kitchen", "domain": "light"},
    ]


def test_notes_stay_short(matcher: GazetteerMatcher) -> None:
    """Test the explanation is a handful of lines rather than a dump.

    The point of this over `debug.render_interpretation` is that somebody will
    actually read it. A sentence holding two commands names each of them, so the
    bound is roughly per command rather than per sentence.
    """
    for text in (
        "turn on the kichen lights",
        "set the kitchen to 50%",
        "turn on the bedroom",
        "open the bedroom blinds and turn on the kitchen lights",
    ):
        assert len(explain(matcher, matcher.interpret(text))) <= 8


def test_another_name_for_a_thing_is_named(matcher: GazetteerMatcher) -> None:
    """Test a thing called by one of its aliases says what it actually is."""
    note = _notes(matcher, "pause the stereo")["alias"][0]

    assert note.message == (
        "'stereo' is an alias of 'Living Room Speakers' (media_player.living_room)."
    )
    assert note.detail["value"] == "media_player.living_room"


def test_a_thing_called_by_its_own_name_is_not_an_alias(
    matcher: GazetteerMatcher,
) -> None:
    """Test the canonical name is not reported as an alias of itself."""
    assert "alias" not in _notes(matcher, "turn on the kitchen ceiling lights")


def test_a_misspelled_name_is_a_guess_not_an_alias(matcher: GazetteerMatcher) -> None:
    """Test a name reached by spelling is reported once, as the guess it is."""
    notes = _notes(matcher, "turn on the kichen lights")

    assert "alias" not in notes
    assert "low_confidence" in notes


def test_a_word_read_as_two_actions_names_both(matcher: GazetteerMatcher) -> None:
    """Test one word reaching two actions names every intent it could mean.

    "pause" is both media and timers, and naming only the first makes the
    reading that is settled on later look like it came from nowhere.
    """
    note = _notes(matcher, "pause the speakers")["intent_hint"][0]

    assert note.message == "'pause' suggests HassMediaPause or HassPauseTimer."


def test_an_abandoned_reading_is_accounted_for(matcher: GazetteerMatcher) -> None:
    """Test the intent a word obviously suggested says why it was not used.

    Without this, "pause the speakers" is explained as a timer with no hint of
    why the media reading everyone expects was passed over.
    """
    notes = _notes(matcher, "pause the speakers")

    assert notes["skipped_intent"][0].message.startswith(
        "Could not interpret as HassMediaPause: I'm missing a device."
    )
    assert notes["rejected_reading"][0].message.startswith(
        "Guessed HassPauseTimer, but"
    )


def test_the_obvious_reading_winning_needs_no_account(
    matcher: GazetteerMatcher,
) -> None:
    """Test a reading behind the one that won is not raised as a road not taken.

    "set" also reaches timers, but nothing about a rejected position suggests a
    timer was ever in the running.
    """
    assert "skipped_intent" not in _notes(matcher, "set the bedroom blinds to 250%")


def test_a_plural_slot_is_named_without_an_article(matcher: GazetteerMatcher) -> None:
    """Test slot names are worded as English rather than pasted in raw."""
    from gazetteer_matcher.explain import _label

    assert _label("start_hours") == "start hours"
    assert _label("query") == "a query"


def test_an_accepted_sentence_says_so(matcher: GazetteerMatcher) -> None:
    """Test the outcome is stated rather than left to the wording of other notes."""
    note = _notes(matcher, "turn on the kitchen lights")["verdict"][0]

    assert note.message == "Accepted."
    assert note.detail["accepted"] is True


def test_a_rejected_sentence_says_so_and_what_it_would_answer(
    matcher: GazetteerMatcher,
) -> None:
    """Test a rejection is stated outright, with its code and spoken response."""
    note = _notes(matcher, "turn on the bedroom")["verdict"][0]

    assert note.message.startswith("Rejected (no_action). Would say: ")
    assert note.detail["accepted"] is False
    assert note.detail["rejection_code"] == "no_action"


def test_the_verdict_comes_last(matcher: GazetteerMatcher) -> None:
    """Test the outcome closes the narration rather than interrupting it."""
    notes = explain(matcher, matcher.interpret("turn on the bedroom"))

    assert notes[-1].code == "verdict"


def test_a_reading_needing_the_speakers_room_says_so(matcher: GazetteerMatcher) -> None:
    """Test a shape resolving its target from where the speaker is says as much.

    "set brightness to 50%" is a whole command from a device in a room, so an
    absent area is the reason rather than anything the sentence left out.
    """
    note = _notes(matcher, "set brightness to 50%")["needs_context_area"][0]

    assert note.message.endswith(
        "but only within a known area, and no context area was present."
    )
    assert note.detail["intent"] in {"HassLightSet", "HassSetPosition"}


def test_the_same_sentence_is_accepted_from_a_room(matcher: GazetteerMatcher) -> None:
    """Test the explanation was right: supplying the area is all it needed."""
    result = matcher.interpret("set brightness to 50%", context_area="kitchen")

    assert result.accepted
    assert result.frames[0].intent == "HassLightSet"


def test_a_named_room_is_not_reported_as_a_missing_one(
    matcher: GazetteerMatcher,
) -> None:
    """Test a sentence saying where to act is never told it gave no area.

    Shapes wanting the speaker's own room are dropped for any utterance with no
    context area, including ones that named the room to act on, where such a
    shape was never what the sentence was reaching for.
    """
    assert "needs_context_area" not in _notes(matcher, "turn on the bedroom")


def test_the_values_a_command_carries_are_reported(matcher: GazetteerMatcher) -> None:
    """Test a sentence is not described as having been heard as less than it was."""
    note = _notes(matcher, "set brightness to 50%")["keywords"][0]

    assert note.message == "Keywords matched: set, brightness, 50 %"


def test_a_target_taken_from_context_is_named(matcher: GazetteerMatcher) -> None:
    """Test a room nobody said is spelled out as having come from the speaker."""
    notes = _notes(matcher, "set brightness to 50%", context_area="kitchen")[
        "from_context"
    ]
    note = next(n for n in notes if n.detail["slot"] == "area")

    assert note.message == "Kitchen area selected from context."
    assert note.detail == {
        "slot": "area",
        "value": "kitchen",
        "source": "context_area",
    }


def test_a_target_the_sentence_named_is_not_from_context(
    matcher: GazetteerMatcher,
) -> None:
    """Test a sentence naming its own target is not credited to the context."""
    notes = _notes(matcher, "turn on the kitchen lights", context_area="bedroom")

    assert "from_context" not in notes


def test_context_is_named_for_a_rejected_reading_too(
    matcher: GazetteerMatcher,
) -> None:
    """Test the room is still accounted for when the reading using it was refused."""
    notes = _notes(matcher, "set brightness to 250%", context_area="kitchen")

    messages = [note.message for note in notes["from_context"]]
    assert "Kitchen area selected from context." in messages
    assert notes["verdict"][0].detail["rejection_code"] == "invalid_percentage"


def test_a_word_the_reading_used_but_hung_no_slot_on_is_reported(
    matcher: GazetteerMatcher,
) -> None:
    """Test a sentence is described the same way whether or not it was accepted.

    "brightness" names the setting rather than filling a slot, so it hangs off no
    resolved value and was dropped from an accepted reading while surviving in a
    rejected one -- the same words reported as two different sentences.
    """
    accepted = _notes(matcher, "set brightness to 50%", context_area="kitchen")
    rejected = _notes(matcher, "set brightness to 50%")

    assert accepted["verdict"][0].detail["accepted"] is True
    assert rejected["verdict"][0].detail["accepted"] is False
    for notes in (accepted, rejected):
        assert notes["keywords"][0].message == "Keywords matched: set, brightness, 50 %"


def test_a_rival_reading_of_the_action_is_not_listed(
    matcher: GazetteerMatcher,
) -> None:
    """Test only the action that won is named, not every way the words were read.

    "set a timer" and "timer for" overlap without either containing the other, so
    both survive collapsing and would be listed as two things that were heard.
    """
    note = _notes(matcher, "set a timer for 5 minutes")["keywords"][0]

    assert note.message == "Keywords matched: set a timer, 5 minutes"


def test_a_slot_no_words_produced_is_named(matcher: GazetteerMatcher) -> None:
    """Test a value the sentence never contained is accounted for.

    "set brightness to 50%" acts on lights in a room and said neither, so both
    have to be spelled out or the reading looks like it came from nowhere.
    """
    messages = [
        note.message
        for note in _notes(matcher, "set brightness to 50%", context_area="kitchen")[
            "from_context"
        ]
    ]

    assert "light domain inferred from the intent." in messages
    assert "Kitchen area selected from context." in messages


def test_a_device_is_named_with_the_id_it_resolved_to(
    matcher: GazetteerMatcher,
) -> None:
    """Test the reading says which thing it acted on, not just what it is called."""
    note = _notes(matcher, "pause the stereo")["chosen"][0]

    assert note.message == (
        "Chose HassMediaPause with name=Living Room Speakers "
        "(media_player.living_room)."
    )


def test_a_tie_over_one_name_says_what_it_could_have_been(
    matcher: GazetteerMatcher,
) -> None:
    """Test two things sharing a name are both named when neither wins.

    Reporting the rival intent says nothing here: both readings are the same
    intent, disagreeing only over which device was meant.
    """
    note = _notes(matcher, "pause the tv")["rejected_reading"][0]

    assert "could just as well be" in note.message
    assert "media_player.bedroom_tv" in note.message
    assert "media_player.living_room" in note.message


def test_the_field_that_settled_a_close_call_is_named(
    matcher: GazetteerMatcher,
) -> None:
    """Test the reason one reading beat another is the field ranking separated them on.

    Two devices answer to "tv" and the speaker's room is what tells them apart,
    which is `context_rank` and nothing else.
    """
    note = _notes(matcher, "turn on the tv", context_area="bedroom")["preferred"][0]

    assert note.message == (
        "Preferred this over name=Living Room Speakers (media_player.living_room) "
        "because its target is the nearer one of that name."
    )
    assert note.detail["field"] == "context_rank"


def test_readings_pulling_apart_on_many_slots_are_not_named(
    matcher: GazetteerMatcher,
) -> None:
    """Test a rival with no short name is passed over rather than listed raw.

    Bare values from several slots at once read as noise, not as an alternative.
    """
    note = _notes(matcher, "turn on the kitchen lights", context_area="bedroom")[
        "preferred"
    ][0]

    assert note.message == (
        "Preferred this over area=Bedroom because it leaves fewer recognized "
        "words unused."
    )


def test_a_recognized_word_the_reading_did_not_use_is_reported(
    matcher: GazetteerMatcher,
) -> None:
    """Test a word that was understood but acted on by nothing is called out.

    "red" is a colour the matcher knows and HassTurnOn has nowhere to put, so the
    command runs having quietly dropped part of what was asked for.
    """
    notes = _notes(matcher, "turn on the kitchen lights red")

    assert notes["verdict"][0].detail["accepted"] is True
    assert notes["unexplained"][0].message == "Ignored words: red."


def test_cost_fields_all_have_a_reason(matcher: GazetteerMatcher) -> None:
    """Test every way a reading can win has wording for it."""
    from gazetteer_matcher.explain import _COST_REASONS
    from gazetteer_matcher.models import Cost

    assert set(Cost._fields) == set(_COST_REASONS)


def test_a_rival_named_in_the_reason_was_named_up_front(
    matcher: GazetteerMatcher,
) -> None:
    """Test no note weighs an intent the explanation never introduced."""
    notes = _notes(matcher, "set brightness to 50%", context_area="kitchen")
    introduced = {
        intent for note in notes["intent_hint"] for intent in note.detail["intents"]
    }

    for note in notes.get("preferred", []):
        assert note.detail["rival_intent"] in introduced


def test_a_named_setting_points_at_an_intent_too(matcher: GazetteerMatcher) -> None:
    """Test the word naming a setting is credited with narrowing the field.

    "set" alone reaches most of the catalog; "brightness" is why the reading came
    back about lights rather than volume, and saying nothing about it leaves that
    unaccounted for.
    """
    messages = [
        note.message
        for note in _notes(matcher, "set brightness to 50%", context_area="kitchen")[
            "intent_hint"
        ]
    ]

    assert "'brightness' suggests HassLightSet." in messages


def test_hints_are_given_in_the_order_they_were_said(
    matcher: GazetteerMatcher,
) -> None:
    """Test the words are accounted for as the sentence runs, not as tags are met."""
    notes = _notes(matcher, "set brightness to 50%", context_area="kitchen")[
        "intent_hint"
    ]

    assert [note.detail["text"] for note in notes] == ["set", "brightness"]


def test_what_a_reading_filled_in_follows_the_reading(
    matcher: GazetteerMatcher,
) -> None:
    """Test a slot inferred from the intent is not explained before the intent.

    "light domain inferred from the intent" names an intent the reader has not
    met yet if it comes first.
    """
    codes = [
        note.code
        for note in explain(
            matcher,
            matcher.interpret("set brightness to 50%", context_area="kitchen"),
        )
    ]

    assert codes.index("chosen") < codes.index("from_context")
