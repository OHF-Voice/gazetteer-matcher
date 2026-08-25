# Gazetteer Matcher

An English-language, constraint-driven intent recognizer for Home Assistant
voice commands. It complements Home Assistant's built-in sentence grammars by
handling home-specific names, fuzzy wording, compound requests, and explicit
conversation references while rejecting interpretations that do not fit the
upstream intent schema.

## Features

- **Flexible and fuzzy target matching** for entities, areas, floors, domains,
  and actions: `turn on the bedrom lamp` resolves to `Bedroom Lamp`.
- **Location-aware disambiguation** from spoken qualifiers and voice-satellite
  context: `bedroom TV` selects the TV assigned to the bedroom.
- **Compound commands** with multiple targets, intents, or properties:
  `turn off the kitchen lights and open the bedroom blinds`.
- **Multiple actions on one target**: `turn on the hallway light and set its
  brightness to 40%`.
- **Explicit conversational follow-ups**: `turn on the kitchen lights` followed
  by `turn them off`, or `is the front door locked?` followed by `lock it`.
- **Conservative, schema-backed validation** with structured error categories
  and concise responses for ambiguous, unsupported, and out-of-range requests.

## Install

```bash
pip install gazetteer-matcher
```

Runtime dependencies are `home-assistant-intents`, `unicode-rbnf`, and
`PyYAML`. A source install also attempts to build a self-contained C++17 fuzzy
scorer. Installation still succeeds without a compiler and uses the
behaviorally equivalent Python implementation instead.

For development:

```bash
pip install 'gazetteer-matcher[dev]'
pytest
```

## Quick start

```python
from gazetteer_matcher import GazetteerMatcher

matcher = GazetteerMatcher(
    home={
        "areas": {
            "kitchen": {"name": "Kitchen", "floor": "ground"},
        },
        "floors": {
            "ground": {"name": "Ground Floor"},
        },
        "entities": {},
    }
)

result = matcher.interpret("flick on the kichen lights")
assert result.accepted

frame = result.frames[0]
assert frame.intent == "HassTurnOn"
assert frame.slots == {"area": "kitchen", "domain": "light"}
assert frame.response_key == "lights_area"
```

Pass the voice satellite's location when a command omits it:

```python
result = matcher.interpret("turn off the lights", context_area="Kitchen")
assert result.frames[0].slots == {"domain": "light", "area": "kitchen"}
```

Interpretations can contain several ordered frames:

```python
result = matcher.interpret(
    "turn off the kitchen lights and open the bedroom blinds"
)

assert [frame.intent for frame in result.frames] == [
    "HassTurnOff",
    "HassTurnOn",
]
```

Rejected requests carry an integration-facing category, a diagnostic reason,
and an optional ready-to-use response:

```python
result = matcher.interpret("set bedroom TV volume to 1000%")

assert not result.accepted
assert result.rejection_code == "invalid_percentage"
assert result.response == (
    "Sorry, the volume value must be a whole-number percentage "
    "between 0% and 100%."
)
```

## Core API

`GazetteerMatcher.interpret()` accepts:

- the utterance;
- optional `context_area` and `context_floor` values;
- optional `previous_targets` exported by a prior accepted interpretation.

An accepted `Interpretation` exposes ordered `frames` and reusable `targets`.
Each frame contains the Home Assistant intent, slot combination, resolved slot
values, response key, and selection diagnostics. Rejected interpretations
expose `rejection_code`, `reason`, `response`, and `refusal_target`; their
`targets` collection is always empty.

The matcher itself is stateless. The caller decides whether a prior target is
recent enough to pass back:

```python
previous = matcher.interpret("open the bedroom blinds")
result = matcher.interpret(
    "close them",
    previous_targets=previous.targets,
)
```

Call `matcher.set_home(...)` to replace the entity/area/floor gazetteer without
rebuilding the language vocabulary, intent catalog, or shared number trie.

## Documentation

- [Usage guide](docs/usage.md) — context, compound commands, state questions,
  follow-ups, response keys, and rejection handling
- [Configuration](docs/configuration.md) — the home gazetteer, vocabulary,
  response wording, and runtime updates
- [Development](docs/development.md) — CLI diagnostics, fixture tests,
  rejection tests, and upstream coverage measurement
- [Internals](docs/internals.md) — tagging, intent constraints, number words,
  fuzzy scoring, coordination, scope, and candidate selection

## CLI

```bash
gazetteer-match match 'turn on the kitchen and hallway lights' \
  --home my-home.yaml
gazetteer-match match 'flick on the kichen lights' \
  --home my-home.yaml --debug
gazetteer-match match 'open the bedroom blinds' \
  --home my-home.yaml --debug --json
gazetteer-match spans 'flik the bedroom lights on'
gazetteer-match support
```

`match` requires either `--home PATH` or an explicit `--empty-home`. The latter
is useful for generic timers, date/time, weather, and state questions that need
no home-specific names. `spans` keeps `--home` optional, while `support` does
not use a home gazetteer. Supply `--vocabulary` or `--responses` to override the
language data. Location context is available through `--context-area` and
`--context-floor`. See the [development guide](docs/development.md) for the
complete debugging and coverage workflow.

## Design principle

Fuzzy similarity is evidence, not permission. A close spelling match cannot
outweigh incompatible Home Assistant slots, contradictory scope, unexplained
semantic content, or an equally good competing interpretation. When the
matcher cannot choose one valid meaning, it rejects the request rather than
guessing.

<!-- Links -->
[intents]: https://github.com/OHF-Voice/intents
