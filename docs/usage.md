# Usage guide

This guide covers the matcher behavior an embedding application normally uses.
See [configuration](configuration.md) for the home gazetteer and language data,
and [internals](internals.md) for candidate generation and validation.

## Home and location context

Construct a matcher with the areas, floors, and entities exported by a Home
Assistant instance:

```python
from gazetteer_matcher import GazetteerMatcher

matcher = GazetteerMatcher(home="my-home.yaml")
```

Pass the voice satellite's location when it is available. IDs, configured
names, and aliases are accepted. A configured floor is derived from the area
unless an explicit floor is also supplied:

```python
result = matcher.interpret(
    "turn on the lights",
    context_area="Kitchen",
    context_floor="Ground Floor",
)

assert result.frames[0].slots == {
    "domain": "light",
    "area": "kitchen",
}
```

Location context ranks otherwise identical entity names. If two areas each
contain an entity named `Ceiling Light`, the entity in the context area wins;
the context floor is a secondary fallback. Without location evidence, equally
good duplicate names remain ambiguous.

An explicit location in the utterance can also qualify duplicate entity text:
`set bedroom TV volume to 50%` selects the entity named `TV` in the bedroom
without adding an `area` slot to the resulting name-targeted frame.

## Compound commands

One utterance may produce several ordered intent frames.

Multiple targets can share an action and a uniquely determined domain:

```text
turn on the kitchen and hallway lights
```

```text
HassTurnOn(area=kitchen, domain=light)
HassTurnOn(area=hallway, domain=light)
```

Independent actions retain their own targets:

```text
turn off the kitchen lights and open the bedroom blinds
```

```text
HassTurnOff(area=kitchen, domain=light)
HassTurnOn(name=cover.bedroom_blinds)
```

Properties can be coordinated too:

```text
set the living room lights red and fifty percent
```

This produces one color frame and one brightness frame. A singular possessive
can reuse a preceding named target:

```text
turn on the hallway light and set its brightness to 40%
```

If the named target has duplicate aliases, location context must resolve the
first clause before the possessive can reuse it:

```python
result = matcher.interpret(
    "turn on the TV and set its volume to 50%",
    context_area="Living Room",
)
```

Without location evidence, two equally good TVs remain ambiguous and the
compound request is rejected.

Inheritance is conservative: only unique values are shared, and an inherited
action with no local target must retain the preceding target rather than
silently changing scope.

## State questions and response keys

State questions preserve wording that is not represented by Home Assistant
slots:

```text
are any kitchen lights on       -> response_key="any"
are the kitchen lights on       -> response_key="any"
which kitchen lights are on     -> response_key="which"
how many lights are on          -> response_key="how_many"
are all the lights off          -> response_key="all"
```

Every accepted frame carries the response key written by the matching
`home-assistant-intents` sentence shape, narrowed by domain. For example,
turning on an area of lights uses `lights_area`, while opening a named blind
uses `cover`.

Where one upstream shape has several possible responses, configured lexical
hints decide among them. If neither the shape nor wording names one response,
`response_key` is `None` rather than a guessed template name.

## Follow-up targets

The matcher is stateless. An application may pass targets from the most recent
accepted interpretation into the next one:

```python
previous = matcher.interpret("open the bedroom blinds")
result = matcher.interpret(
    "close them",
    previous_targets=previous.targets,
)

assert result.frames[0].slots == {"name": "cover.bedroom_blinds"}
```

A caller whose prior turn came from another recognizer can construct the
target explicitly:

```python
from gazetteer_matcher import TargetReference

previous = (TargetReference.for_entity("cover.bedroom_blinds"),)
result = matcher.interpret("close them", previous_targets=previous)
```

`TargetReference.for_area()` and `.for_floor()` accept optional `domain` and
`device_class` values, allowing `turn them off` to retain the lights in an area
rather than treating the whole area as an untyped target.

Only explicit `it` and `them` pronouns trigger cross-turn reuse. `back` and
`again` are supported modifiers. Reuse is limited to turn-on, turn-off, open,
close, lock, and unlock actions. `it` requires a named entity; `them` can also
refer to an area, floor, or whole-home selector.

`them` reaches every target the previous turn named, producing one frame for
each. How the sentence reads is settled against the first target and the same
intent is applied to the rest, so targets of differing scope are reached
together — a turn naming one device and one room is followed by `turn them off`
without either being lost. A target the action cannot apply to rejects the
sentence rather than acting on only some of them.

```python
previous = matcher.interpret("turn on the bedroom lamp and the kitchen lights")
result = matcher.interpret("turn them off", previous_targets=previous.targets)

assert [frame.slots for frame in result.frames] == [
    {"name": "light.bedroom_lamp"},
    {"area": "kitchen", "domain": "light"},
]
```

Rejected interpretations export no targets. `it` after a turn that named more
than one target, pronouns mixed with an explicit target, and actions
incompatible with the target are rejected.

## Quantity and geographic scope

`all` and `every` describe quantity, not location. Scope comes from the rest of
the utterance:

- a named area or floor selects that location;
- `here` selects `context_area`;
- `everywhere`, `in the house`, and similar phrases select the whole home;
- an unscoped `all` uses a global combination when the intent and domain
  support one, otherwise it uses the context area.

Thus `turn all the lights in the kitchen off` targets the kitchen, while `turn
all lights off` uses the whole-home light combination. Conflicting local and
home-wide scope, such as `kitchen lights everywhere`, is rejected.

## Rejections

Rejected interpretations separate stable integration behavior from internal
diagnostics:

- `rejection_code` is a stable category suitable for application policy;
- `reason` explains the exact internal constraint for debugging;
- `response` is concise user-facing wording;
- `refusal_target` names the resolved target, if there was one.

```python
result = matcher.interpret("set bedroom TV volume to 1000%")

assert result.rejection_code == "invalid_percentage"
assert result.response == (
    "Sorry, the volume value must be a whole-number percentage "
    "between 0% and 100%."
)
```

Noise generally has no refusal target, while a request that resolved home
semantics before failing can expose one:

```python
matcher.interpret("asdfgh").refusal_target
# None

matcher.interpret("write a poem about my kitchen lights").refusal_target
# "the lights in Kitchen"
```

Applications may use the supplied response, substitute wording based on the
code, or let a downstream conversational model handle selected categories.
