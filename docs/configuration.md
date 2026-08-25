# Configuration

The matcher has three replaceable data sources: a dynamic home gazetteer,
language vocabulary, and rejection-response wording. Each may be supplied as a
dictionary or a YAML path.

## Home gazetteer

Applications should generate the home data from their Home Assistant instance.
The top-level collections are `areas`, `floors`, and `entities`:

```yaml
areas:
  kitchen:
    name: Kitchen
    aliases: [kitchen]
    floor: ground

floors:
  ground:
    name: Ground Floor
    aliases: [ground floor, downstairs, first floor]

entities:
  light.kitchen_ceiling:
    name: Kitchen Ceiling Lights
    aliases: [kitchen ceiling lights, ceiling lights]
    domain: light
    area: kitchen
    floor: ground
```

Collection keys become the slot values returned to the application. `name` and
`aliases` are accepted in utterances. Entity metadata supplies its domain,
device class, area, and floor for intent validation and disambiguation.

Use `display_name()` when rendering a returned home ID:

```python
matcher.display_name("name", "lock.front_door")  # "Front Door"
matcher.display_name("area", "kitchen")          # "Kitchen"
```

## Runtime home updates

Call `set_home()` when entities or locations change:

```python
matcher.set_home(updated_home)
```

This rebuilds the home-dependent tagger and rejection responder while keeping
the vocabulary, Home Assistant intent catalog, and shared number-word trie.
Interpretations already in progress retain the tagger they started with;
applications interpreting concurrently should serialize home replacement.

## Vocabulary

`src/gazetteer_matcher/data/vocabulary.yaml` contains English semantic and
matching policy:

- grammatical skip and request-wrapper phrases;
- conjunctions and relation cues;
- domains, device classes, states, colors, and media classes;
- numeric units and number-word sign vocabulary;
- action phrases and virtual-action lowerings;
- anaphora and coordinated-reference vocabulary;
- fuzzy thresholds and ambiguity margins;
- response wording hints and combination-specific cues.

Virtual actions lower natural words to Home Assistant intents with additional
constraints. For example, `open` lowers through a turn-on intent but is limited
to compatible cover and valve domains; `lock` is limited to lock entities.

Some action phrases allow grammatical skip words between their tokens. Matched
indexes remain explicit, preventing semantic content or conjunctions from being
crossed accidentally.

Override the vocabulary in Python:

```python
matcher = GazetteerMatcher(
    home="my-home.yaml",
    vocabulary="my-vocabulary.yaml",
)
```

Or on the CLI:

```bash
gazetteer-match match 'turn on the office lamp' \
  --home my-home.yaml \
  --vocabulary my-vocabulary.yaml
```

## Rejection responses

`src/gazetteer_matcher/data/responses.yaml` contains every user-facing
rejection template plus localized labels for actions, settings, domains,
device classes, and targets. Internal diagnostic strings do not appear in
spoken responses.

Supply replacement responses for another deployment or language:

```python
matcher = GazetteerMatcher(
    home="my-home.yaml",
    responses="my-responses.yaml",
)
```

The compatible `responses_path` argument and CLI `--responses` option are also
available. A replacement file must define `templates.generic`; missing
specialized templates safely fall back to that generic response.

## Number settings

The vocabulary's `numbers` section controls:

- the maximum generated cardinal and ordinal;
- joiners such as English `and`;
- spoken negative signs such as `minus` and `negative`;
- spoken positive signs such as `plus` and `positive`.

Literal signed integers and decimals are tokenized directly. Configured spoken
signs are composed with number words. Intent-specific validation then decides
whether the resulting value is legal: negative climate temperatures are valid,
while percentages must be whole numbers from 0 through 100.

## Fuzzy policy

Fuzzy settings independently control actions, names, areas, and floors.
Opposing actions use an ambiguity margin so a close fuzzy tie cannot choose a
polarity such as on versus off. `max_extra_tokens` restricts candidate windows,
and `limit_per_window` bounds the top matches retained for each window.

The optional native scorer and Python implementation use the same settings and
are required by tests to produce identical scores and ordering.
