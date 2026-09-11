"""Short plain-language notes on how a sentence was read.

`debug.render_interpretation` dumps everything the matcher considered, which is
what you want when the matcher itself is wrong. This is the other audience: a few
sentences saying what was recognized, what it pointed at, what was taken on a
guess, and -- when nothing was matched -- what the sentence was missing.

Notes carry a stable `code` alongside their wording, so a caller can select or
re-translate them rather than parsing the text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Container, Iterable, Sequence

from .models import (
    Cost,
    FrameCandidate,
    Interpretation,
    SegmentDebug,
    Span,
    UnbuiltCombination,
)

if TYPE_CHECKING:
    from .matcher import GazetteerMatcher

# Below this, a name was reached by spelling rather than by what was said.
_CONFIDENT = 0.999

# The tags worth naming. Everything else is scaffolding of the reading rather
# than something the speaker would recognize having said.
_SLOT_LABELS = {
    "action": "action",
    "area": "area",
    "device_class": "device class",
    "domain": "domain",
    "floor": "floor",
    "name": "device",
    "state": "state",
}

_MISSING_LABELS = {
    "area": "a room",
    "device_class": "a class of device",
    "domain": "a kind of device",
    "floor": "a floor",
    "name": "a device",
    "state": "a state",
}

# What a command carries besides its target. Reported so a sentence is not
# described as having been heard as less than it was: "set brightness to 50%"
# turns on nothing that the target tags alone would show.
_VALUE_LABELS = {
    "color": "color",
    "duration_hours": "duration",
    "duration_minutes": "duration",
    "duration_seconds": "duration",
    "percent_value": "percentage",
    "slot_marker": "setting",
    "temperature_value": "temperature",
}

_TAG_LABELS = _SLOT_LABELS | _VALUE_LABELS

# Where a slot came from when no words in the sentence produced it.
_UNSAID_SOURCES = {
    "anaphora": "carried over from the previous turn",
    "context_area": "selected from context",
    "coordination_reference": "carried over from the previous command",
    "fixed_action": "fixed by the action",
    "inferred_action_domain": "inferred from the action",
    "inferred_device_class": "inferred from the action",
    "inferred_intent_schema": "inferred from the intent",
    "inferred_lock_state": "inferred from the action",
    "residual": "taken from the rest of the sentence",
}

# Why one reading outranked another, keyed by the `Cost` field that separated
# them. Only the first field they differ on decides it, so only one is ever said.
_COST_REASONS = {
    "violations": "it breaks fewer rules",
    "unexplained_important": "it leaves fewer recognized words unused",
    "unexplained_unimportant": "it leaves fewer words over",
    "action_words": "it reads more of the sentence as the action",
    "fuzzy_action": "its action was said exactly",
    "fuzzy_slots": "fewer of its targets were reached by spelling",
    "inherited": "it takes less from the previous turn",
    "context_rank": "its target is the nearer one of that name",
    "target_generality": "it names a device rather than standing in for one",
    "fuzzy_distance": "its spelling is a closer match",
    "consumed": "it accounts for more of the sentence",
}

# Things in the home, which are the only values carrying a name of their own.
_HOME_TAGS = frozenset({"area", "floor", "name"})

# Enough to say what the sentence looked like it wanted without listing the catalog.
_MAX_UNBUILT = 2
_MAX_RIVAL_SLOTS = 2


@dataclass(frozen=True)
class Note:
    """One plain-language observation about how a sentence was read."""

    code: str
    message: str
    detail: dict[str, Any]


def explain(matcher: GazetteerMatcher, result: Interpretation) -> list[Note]:
    """Return a few sentences on how `result` was arrived at.

    Ordered as the reading happened: what was recognized, what that pointed at,
    what was uncertain, then the reading itself -- what was chosen or what was
    missing, what it filled in, and why it won -- and last the verdict.

    Anything about the reading follows the reading. A domain inferred from the
    intent cannot be explained before the intent has been named.
    """
    notes: list[Note] = []
    notes.extend(_keyword_notes(result))
    notes.extend(_intent_hint_notes(matcher, result))
    notes.extend(_alias_notes(matcher, result))
    notes.extend(_confidence_notes(matcher, result))

    if result.accepted:
        notes.extend(_chosen_notes(matcher, result))
        notes.extend(_context_notes(matcher, result))
        notes.extend(_preference_notes(matcher, result))
    else:
        notes.extend(_failure_notes(matcher, result))
        notes.extend(_context_notes(matcher, result))

    notes.append(_verdict_note(result))
    return notes


def _verdict_note(result: Interpretation) -> Note:
    """State the outcome outright.

    Every other note is about how the sentence was read, and which of them means
    it was turned down is a convention nobody should have to know.
    """
    if result.accepted:
        return Note(code="verdict", message="Accepted.", detail={"accepted": True})

    code = f" ({result.rejection_code})" if result.rejection_code else ""
    spoken = f' Would say: "{result.response}"' if result.response else ""
    return Note(
        code="verdict",
        message=f"Rejected{code}.{spoken}",
        detail={
            "accepted": False,
            "rejection_code": result.rejection_code,
            "response": result.response,
        },
    )


def _named_spans(result: Interpretation) -> list[Span]:
    """Return the spans worth telling somebody about, in the order they were said.

    A reading that was settled names what it actually used; one that was not has
    to fall back on everything recognized, with overlapping readings of the same
    words collapsed to the longest so "blinds" does not follow "bedroom blinds".
    """
    covered: set[int] = set()
    if not result.frames:
        return sorted(_collapse(result.spans, covered), key=lambda span: span.start)

    # What the reading resolved, taken first so a word read two ways is reported
    # as whichever way won.
    kept = _collapse(
        [frame.action_span for frame in result.frames]
        + [
            span
            for frame in result.frames
            for option in frame.slot_options.values()
            for span in option.spans
        ],
        covered,
    )

    # Then words the reading accounted for but hung no slot on, like the
    # "brightness" in "set brightness to 50%", which was heard all the same.
    unexplained = {
        index for frame in result.frames for index in frame.unexplained_tokens
    }
    kept += _collapse(
        [
            span
            for span in result.spans
            # Any other action span is a rival reading that lost, and the one
            # that won is already in hand
            if span.tag != "action"
            and not unexplained.intersection(span.token_indexes())
        ],
        covered,
    )

    return sorted(kept, key=lambda span: span.start)


def _collapse(spans: Iterable[Span], covered: set[int]) -> list[Span]:
    """Return the reportable spans that cover new words, longest first.

    `covered` is updated, so a later call only fills the gaps a former one left.
    """
    kept: list[Span] = []
    for span in sorted(
        (span for span in spans if span.tag in _TAG_LABELS),
        # Longest first, so a span is only dropped for one that subsumes it
        key=lambda span: (-span.length, span.start),
    ):
        indexes = set(span.token_indexes())
        if indexes <= covered:
            continue
        covered |= indexes
        kept.append(span)

    return kept


def _keyword_notes(result: Interpretation) -> list[Note]:
    spans = _named_spans(result)
    if not spans:
        return []

    return [
        Note(
            code="keywords",
            message="Keywords matched: " + ", ".join(span.text for span in spans),
            detail={
                "keywords": [
                    {"text": span.text, "tag": span.tag, "value": span.value}
                    for span in spans
                ]
            },
        )
    ]


def _intent_hint_notes(matcher: GazetteerMatcher, result: Interpretation) -> list[Note]:
    """Say what each word that points at an intent points at, before one is chosen.

    An action is not the only such word: naming a setting narrows the field just
    as much, and "brightness" is why a reading of "set" came back about lights
    rather than volume. One word can also be read as more than one action --
    "pause" is both media and timers -- and every intent any of them reaches has
    to be named, or a later note weighs one the explanation never introduced.
    """
    notes: list[Note] = []
    reached: dict[str, list[str]] = {}
    said_at: dict[str, int] = {}
    for span in result.spans:
        if span.tag == "action":
            found = (matcher.actions.get(str(span.value)) or {}).get("intents") or []
        elif span.tag == "slot_marker":
            found = _slot_owners(matcher, str(span.value))
        else:
            continue

        if not found:
            continue

        said_at.setdefault(span.text, span.start)
        intents = reached.setdefault(span.text, [])
        for intent in found:
            if intent not in intents:
                intents.append(intent)

    for text in sorted(reached, key=lambda said: said_at[said]):
        intents = reached[text]

        # Every one, however many. A word like "set" reaches most of the catalog,
        # and counting the rest hides the very names the later notes go on to
        # weigh against each other.
        notes.append(
            Note(
                code="intent_hint",
                message=f"{text!r} suggests {_join_or(intents)}.",
                detail={"text": text, "intents": list(intents)},
            )
        )
    return notes


def _slot_owners(matcher: GazetteerMatcher, slot: str) -> list[str]:
    """Return the intents with a shape that takes this slot.

    Wildcards are left out, matching the combinations a reading is built from.
    """
    return sorted(
        {
            combo.intent
            for combo in matcher.catalog.all
            if not combo.is_wildcard and slot in combo.slots
        }
    )


def _join_or(items: Sequence[str]) -> str:
    """Join alternatives the way they would be read aloud."""
    if len(items) <= 2:
        return " or ".join(items)
    return ", ".join(items[:-1]) + f", or {items[-1]}"


def _alias_notes(matcher: GazetteerMatcher, result: Interpretation) -> list[Note]:
    """Name anything the speaker called by one of its other names.

    Only for things in the home, which are the only values with a name of their
    own to differ from; a word like "lights" is vocabulary, not an alias.
    """
    notes: list[Note] = []
    for span in _named_spans(result):
        if span.tag not in _HOME_TAGS or span.similarity < _CONFIDENT:
            # A name reached by spelling is reported as a guess instead
            continue
        canonical = matcher.display_name(span.tag, span.value)
        if str(canonical).casefold() == span.text.casefold():
            continue
        notes.append(
            Note(
                code="alias",
                message=(f"{span.text!r} is an alias of {canonical!r} ({span.value})."),
                detail={"text": span.text, "tag": span.tag, "value": span.value},
            )
        )
    return notes


def _frames_in_play(result: Interpretation) -> list[FrameCandidate]:
    """Return the readings being explained: those settled on, or the nearest miss."""
    if result.frames:
        return list(result.frames)

    for segment in result.segments:
        if segment.chosen is None and segment.frame_candidates:
            return segment.frame_candidates[:1]

    return []


def _context_notes(matcher: GazetteerMatcher, result: Interpretation) -> list[Note]:
    """Name every part of the reading that is not in the sentence anywhere.

    A slot resolved from no words at all is the part a speaker is least able to
    account for: "set brightness to 50%" acts on lights in a room, and said
    neither. A slot option with no spans behind it is exactly that.
    """
    notes: list[Note] = []
    seen: set[tuple[str, Any]] = set()
    for frame in _frames_in_play(result):
        for slot, option in frame.slot_options.items():
            if option.spans or (slot, option.value) in seen:
                continue
            seen.add((slot, option.value))
            notes.append(
                Note(
                    code="from_context",
                    message=(
                        f"{matcher.display_name(slot, option.value)} {slot} "
                        f"{_UNSAID_SOURCES.get(option.source, 'filled in')}."
                    ),
                    detail={
                        "slot": slot,
                        "value": option.value,
                        "source": option.source,
                    },
                )
            )
    return notes


def _confidence_notes(matcher: GazetteerMatcher, result: Interpretation) -> list[Note]:
    """Flag anything reached by spelling rather than by what was actually said."""
    notes: list[Note] = []
    for span in _named_spans(result):
        if span.similarity >= _CONFIDENT:
            continue
        label = _TAG_LABELS[span.tag]
        value = matcher.display_name(span.tag, span.value)
        notes.append(
            Note(
                code="low_confidence",
                message=(
                    f"Read {span.text!r} as the {label} {value}, "
                    f"but it is only a {span.similarity:.0%} match."
                ),
                detail={
                    "text": span.text,
                    "tag": span.tag,
                    "value": span.value,
                    "similarity": span.similarity,
                },
            )
        )
    return notes


def _chosen_notes(matcher: GazetteerMatcher, result: Interpretation) -> list[Note]:
    """Say what was settled on, and name anything that went unused."""
    notes: list[Note] = []
    for frame in result.frames:
        slots = ", ".join(
            f"{slot}={_named_value(matcher, slot, value)}"
            for slot, value in sorted(frame.slots.items())
        )
        notes.append(
            Note(
                code="chosen",
                message=f"Chose {frame.intent} with {slots or 'no targets'}.",
                detail={
                    "intent": frame.intent,
                    "combination": frame.combination,
                    "slots": dict(frame.slots),
                },
            )
        )

    ignored = sorted(
        {
            result.tokens[index].raw
            for frame in result.frames
            for index in frame.unexplained_important_tokens
        }
    )
    if ignored:
        notes.append(
            Note(
                code="unexplained",
                message="Ignored words: " + ", ".join(ignored) + ".",
                detail={"words": ignored},
            )
        )
    return notes


def _closest_unbuilt(
    segment: SegmentDebug, intents: Container[str] | None = None
) -> list[UnbuiltCombination]:
    """Return the readings that got furthest, one per intent.

    Every combination of every intent the action points at is tried, so reporting
    them all would bury the one that nearly worked. The furthest for an intent is
    the one whose missing slot is worth asking about.
    """
    best: dict[str, UnbuiltCombination] = {}
    for unbuilt in segment.unbuilt:
        if unbuilt.slot is None:
            # Passed over before any slot was tried, so there is no missing one
            # to report; `_context_area_notes` accounts for these instead.
            continue
        if intents is not None and unbuilt.intent not in intents:
            continue
        current = best.get(unbuilt.intent)
        if current is None or len(unbuilt.satisfied) > len(current.satisfied):
            best[unbuilt.intent] = unbuilt

    return sorted(best.values(), key=lambda u: (-len(u.satisfied), u.intent))[
        :_MAX_UNBUILT
    ]


def _failure_notes(matcher: GazetteerMatcher, result: Interpretation) -> list[Note]:
    """Say why the sentence was turned down.

    A reading that was built and then rejected knows exactly what was wrong with
    it, and that beats guessing from the shapes that were never built at all.
    """
    segment = next(
        (segment for segment in result.segments if segment.chosen is None), None
    )
    if segment is None:
        return _nothing_built_notes(result)

    if segment.frame_candidates:
        # The reading that was built may not come from the action the sentence
        # most obviously suggested, which reads as a non sequitur unless the
        # abandoned one is accounted for first.
        return _skipped_action_notes(matcher, segment) + _violation_notes(
            matcher, result, segment
        )

    return _missing_notes(matcher, result, segment)


def _skipped_action_notes(
    matcher: GazetteerMatcher, segment: SegmentDebug
) -> list[Note]:
    """Say why an action word was read one way when it could be read another.

    Only the readings ahead of the one that was built are worth accounting for.
    Those are the ones a speaker would have expected to win, and the ones behind
    it were never in the running.
    """
    built = {candidate.action for candidate in segment.frame_candidates}
    notes: list[Note] = []
    for span in segment.action_candidates:
        if str(span.value) in built:
            break
        intents = set((matcher.actions.get(str(span.value)) or {}).get("intents") or [])
        for unbuilt in _closest_unbuilt(segment, intents)[:1]:
            message = (
                f"Could not interpret as {unbuilt.intent}: "
                f"I'm missing {_label(unbuilt.slot)}."
            )
            example = _example(matcher, unbuilt)
            if example:
                message = f"{message} Expected something like {example!r}."
            notes.append(
                Note(
                    code="skipped_intent",
                    message=message,
                    detail={
                        "text": span.text,
                        "intent": unbuilt.intent,
                        "missing": unbuilt.slot,
                        "example": example,
                    },
                )
            )
    return notes


def _named_value(matcher: GazetteerMatcher, slot: str, value: Any) -> str:
    """Return a slot value as a person would say it, keeping the id it stands for.

    Two devices can answer to one name, so the name alone does not say which was
    acted on. Areas and floors are left as they are: their ids are slugs of the
    same name, and repeating those only crowds the line.
    """
    name = matcher.display_name(slot, value)
    if slot == "name" and str(name) != str(value):
        return f"{name} ({value})"
    return str(name)


def _rival_label(
    matcher: GazetteerMatcher, best: FrameCandidate, rival: FrameCandidate
) -> str | None:
    """Name a competing reading by whatever it disagrees about.

    Naming the rival intent explains nothing when both readings share it and
    differ over which thing was meant, which is what duplicate names produce.
    """
    if rival.intent != best.intent:
        return rival.intent

    differing = sorted(
        slot for slot in rival.slots if best.slots.get(slot) != rival.slots.get(slot)
    )
    # Readings pulling apart on several slots at once have no short name, and
    # listing the values bare reads as noise rather than as an alternative.
    if not differing or len(differing) > _MAX_RIVAL_SLOTS:
        return None

    return ", ".join(
        f"{slot}={_named_value(matcher, slot, rival.slots[slot])}" for slot in differing
    )


def _rivalry(
    matcher: GazetteerMatcher, best: FrameCandidate, rival: FrameCandidate
) -> str:
    """Say what a tied reading disagrees about."""
    label = _rival_label(matcher, best, rival)
    if label is None:
        return "another reading fits just as well"
    if rival.intent != best.intent:
        return f"{label} reads it just as well"
    return f"it could just as well be {label}"


def _preference_notes(matcher: GazetteerMatcher, result: Interpretation) -> list[Note]:
    """Say what settled the choice when more than one reading was in the running.

    Readings are ranked on a cost compared field by field, so the first field the
    winner and the runner-up disagree on is the entire reason one of them won.

    At most one, from the first close call there was. This answers a question
    nobody asked unless the reading surprised them, and a sentence holding two
    commands should not answer it twice.
    """
    notes: list[Note] = []
    for segment in result.segments:
        if notes:
            break

        chosen = segment.chosen
        if chosen is None:
            continue

        rival = next(
            (
                candidate
                for candidate in segment.frame_candidates
                if candidate is not chosen
            ),
            None,
        )
        if rival is None:
            continue

        reason = _cost_reason(chosen.cost, rival.cost)
        label = _rival_label(matcher, chosen, rival)
        if reason is None or label is None:
            continue

        notes.append(
            Note(
                code="preferred",
                message=f"Preferred this over {label} because {reason}.",
                detail={
                    "over": label,
                    "field": _cost_field(chosen.cost, rival.cost),
                    "rival_intent": rival.intent,
                },
            )
        )
    return notes


def _cost_field(chosen: Cost, rival: Cost) -> str | None:
    """Return the first ranking field two readings disagree on."""
    if len(chosen) != len(rival):
        return None

    for name, ours, theirs in zip(Cost._fields, chosen, rival, strict=True):
        if ours != theirs:
            return name
    return None


def _cost_reason(chosen: Cost, rival: Cost) -> str | None:
    """Say in words why one reading outranked another."""
    field = _cost_field(chosen, rival)
    return _COST_REASONS.get(field) if field else None


def _violation_notes(
    matcher: GazetteerMatcher, result: Interpretation, segment: SegmentDebug
) -> list[Note]:
    """Explain a reading that was constructed and then failed its own constraints."""
    best = segment.frame_candidates[0]
    slots = ", ".join(
        f"{slot}={_named_value(matcher, slot, value)}"
        for slot, value in sorted(best.slots.items())
    )
    read_as = f"Guessed {best.intent}" + (f" with {slots}" if slots else "")

    if result.ambiguous and len(segment.frame_candidates) > 1:
        problem = _rivalry(matcher, best, segment.frame_candidates[1])
    elif best.violations:
        problem = best.violations[0]
    elif best.unexplained_important_tokens:
        words = ", ".join(
            repr(result.tokens[index].raw)
            for index in best.unexplained_important_tokens
        )
        problem = f"nothing could be made of {words}"
    else:
        problem = result.reason or "it was turned down"

    return [
        Note(
            code="rejected_reading",
            message=f"{read_as}, but {problem}.",
            detail={
                "intent": best.intent,
                "combination": best.combination,
                "slots": dict(best.slots),
                "violations": list(best.violations),
                "rejection_code": result.rejection_code,
            },
        )
    ]


def _context_area_notes(result: Interpretation, segment: SegmentDebug) -> list[Note]:
    """Say when a reading was passed over only for not knowing the speaker's room.

    "set brightness to 50%" is a complete command in an area and nothing at all
    from one nowhere, so the absent room is the whole story and belongs ahead of
    anything the sentence itself left out.

    Only when the sentence named nowhere itself. These shapes are dropped for any
    utterance with no context area, including ones that said which room to act
    on, where a speaker's own room was never what was wanted.
    """
    if any(span.tag in {"area", "floor"} for span in result.spans):
        return []

    intents: list[str] = []
    for unbuilt in segment.unbuilt:
        if unbuilt.needs_context_area and unbuilt.intent not in intents:
            intents.append(unbuilt.intent)

    return [
        Note(
            code="needs_context_area",
            message=(
                f"Could be {intent}, but only within a known area, "
                f"and no context area was present."
            ),
            detail={"intent": intent},
        )
        for intent in intents[:_MAX_UNBUILT]
    ]


def _missing_notes(
    matcher: GazetteerMatcher, result: Interpretation, segment: SegmentDebug
) -> list[Note]:
    """Say what the sentence looked like it wanted, and what it did not say."""
    notes: list[Note] = _context_area_notes(result, segment)

    # One budget across both: a reading held up only by an absent room already
    # says what to do about it, and is worth more than another near miss.
    for unbuilt in _closest_unbuilt(segment)[: _MAX_UNBUILT - len(notes)]:
        missing = _label(unbuilt.slot)
        if unbuilt.satisfied:
            got = " and ".join(_label(slot) for slot in unbuilt.satisfied)
            message = (
                f"Could be {unbuilt.intent}: found {got}, "
                f"but it's missing {missing}."
            )
        else:
            message = f"Could be {unbuilt.intent}, but it's missing {missing}."

        example = _example(matcher, unbuilt)
        if example:
            message = f"{message} Expected something like {example!r}."

        notes.append(
            Note(
                code="missing_slot",
                message=message,
                detail={
                    "intent": unbuilt.intent,
                    "combination": unbuilt.combination,
                    "missing": unbuilt.slot,
                    "satisfied": list(unbuilt.satisfied),
                    "example": example,
                },
            )
        )

    return notes or _nothing_built_notes(result)


def _nothing_built_notes(result: Interpretation) -> list[Note]:
    """Say which half of the sentence landed, when no reading was ever started."""
    if any(span.tag == "action" for span in result.spans):
        message = "Found something to do, but nothing to do it to."
    elif _named_spans(result):
        message = "Nothing here looked like an action."
    else:
        message = "None of this was recognized."

    return [
        Note(
            code="nothing_built",
            message=message,
            detail={
                "rejection_code": result.rejection_code,
                "reason": result.reason,
            },
        )
    ]


def _label(slot: str | None) -> str:
    """Return how to name a slot to somebody who never heard of slots."""
    if slot is None:
        return "a target"

    known = _MISSING_LABELS.get(slot)
    if known is not None:
        return known

    words = slot.replace("_", " ")
    # "nothing said a hours": a plural slot takes no article
    return words if words.endswith("s") else f"a {words}"


def _example(matcher: GazetteerMatcher, unbuilt: UnbuiltCombination) -> str | None:
    """Return one example sentence for a combination, if the catalog carries any."""
    for combo in matcher.catalog.combinations(unbuilt.intent):
        if combo.name != unbuilt.combination:
            continue
        example = combo.example
        if isinstance(example, list):
            return str(example[0]) if example else None
        return str(example) if example else None
    return None


def render(notes: list[Note]) -> str:
    """Return the notes as lines, for a log or a terminal."""
    return "\n".join(note.message for note in notes)
