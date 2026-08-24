# gazetteer-matcher

An MVP, constraint-driven intent recognizer for Home Assistant voice commands.
It is deliberately **not** a statistical intent classifier. It tags lexical
spans, generates possible semantic frames, validates those frames against
the Home Assistant intent metadata, and rejects interpretations that leave
content unexplained.

The project is intended as an experiment/prototype, not a drop-in replacement
for HassIL.

## Design goals

- Keep language vocabulary in YAML rather than Python.
- Treat fuzzy matching as tolerant gazetteer lookup, not an intent score.
- Use pure-Python edit-distance algorithms; there is no RapidFuzz dependency.
- Use `unicode-rbnf`/CLDR output as the source for number-word forms.
- Support areas, floors, entity names/aliases, domains, device classes, states,
  colors, numeric properties, timer durations, and selected residual text slots.
- Handle conjunctions by splitting coordinated segments and conservatively
  inheriting actions/targets/properties when the omitted value is unique.
- Validate against the real Home Assistant slot-combination catalog.
- Prefer lexicographic costs (violations, unexplained content, fuzziness,
  inheritance, etc.) over arbitrary weighted evidence scores.
- Make every intermediate span and candidate frame inspectable.

## The intent metadata catalog

The `home-assistant-intents` dependency provides the upstream Home Assistant
slot-combination catalog. `GazetteerMatcher` loads it automatically with
`get_intent_info()`; no catalog file, environment variable, or CLI option is
required. Construction raises `RuntimeError` if the installed dependency does
not contain its generated metadata instead of silently using an empty catalog.

Callers that need an explicit catalog for testing may still pass an `intents`
dictionary to `GazetteerMatcher` or `MatcherConfig.load`.

### Reference numbers

Against `home-assistant-intents` 2026.8.24:

- 217 total slot combinations
- 26 combinations with explicit `wildcard_slots` (excluded)
- 191 non-wildcard combinations
- 36 intents containing those 191 non-wildcard combinations

The default `vocabulary.yaml` has at least one action mapping to all 36 of those
intents, so `gazetteer-match support` reports all 191 as *schema reachable*.
That does **not** mean the MVP has exhaustive English phrasing for all 191
combinations. It means the generic slot machinery can construct/validate them
when the required lexical evidence is available. The timer/media/free-text
edges in particular are intentionally MVP-level.

These counts are tied to that package release; a different catalog revision
will move them, along with the `test_support_catalog` assertions.

## Install

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

For development:

```bash
pip install -e '.[dev]'
pytest
```

Dependencies are only:

- `PyYAML`
- `unicode-rbnf`
- `home-assistant-intents`

## Quick start

```python
from gazetteer_matcher import GazetteerMatcher

matcher = GazetteerMatcher(
    home={
        "areas": {"kitchen": {"name": "Kitchen"}},
        "floors": {},
        "entities": {},
    }
)

result = matcher.interpret("flick on the kichen lights")
assert result.accepted

for frame in result.frames:
    print(frame.intent, frame.combination, frame.slots, frame.response_key)
```

The default home is empty. Home configuration may be supplied directly as a
dictionary or loaded from a YAML path. The `*_path` keywords remain available
for compatibility:

```python
matcher = GazetteerMatcher(
    home={
        "areas": {"kitchen": {"name": "Kitchen"}},
        "floors": {},
        "entities": {
            "light.kitchen": {
                "name": "Kitchen Light",
                "domain": "light",
                "area": "kitchen",
            }
        },
    },
    responses="my-responses.yaml",
)
```

With this home configuration, the quick-start example yields approximately:

```text
HassTurnOn area_domain {'area': 'kitchen', 'domain': 'light'}
```

The typo `kichen` is a fuzzy `AREA` span. Fuzzy similarity is a late
lexicographic tie-breaker; it does not make an otherwise invalid frame valid.

Pass the voice satellite's location when it is available. IDs, configured
names, and aliases are accepted; the floor is derived from the area unless it
is supplied explicitly:

```python
result = matcher.interpret(
    "turn on the lights",
    context_area="Kitchen",
    context_floor="Ground Floor",
)
assert result.frames[0].slots == {"domain": "light", "area": "kitchen"}
```

Location context also ranks otherwise identical entity names. For example,
when several areas contain an entity named `Ceiling Light`, the entity in the
context area wins; context floor is a secondary fallback. Without context the
same duplicate-name match remains ambiguous.

## Follow-up targets

The matcher is stateless, but a caller may pass the targets from the most
recent successful interpretation to resolve a small set of explicit follow-up
phrases:

```python
previous = matcher.interpret("open the bedroom blinds")
result = matcher.interpret(
    "close them",
    previous_targets=previous.targets,
)

assert result.frames[0].slots == {"name": "cover.bedroom_blinds"}
```

Only `it` and `them` trigger target reuse. The modifiers `back` and `again`
are accepted with those pronouns, and reuse is limited to turn-on, turn-off,
open, close, lock, and unlock actions. `it` requires one named entity; `them`
may also reuse an area, floor, or whole-home selector. Repeated references to
the same target across a multi-frame interpretation are coalesced. The current
implementation rejects multiple distinct previous target references, pronouns
mixed with an explicit target, and any action whose existing intent/domain
constraints do not support the target.

`Interpretation.targets` is empty for rejected interpretations, so a partial
multi-command match cannot accidentally replace conversation state. Previous
targets are ignored unless an explicit supported pronoun is present. The
caller remains responsible for deciding how recent a successful turn must be
before passing its targets back to the matcher. A context-area selector is
reusable only when `context_area` was supplied and therefore materialized as a
concrete area in the original frame.

## CLI/debug tooling

Interpret normally:

```bash
gazetteer-match match 'turn on the kitchen and hallway lights'
```

Supply location context on the CLI with:

```bash
gazetteer-match match 'turn off the lights' \
  --context-area Kitchen --context-floor 'Ground Floor'
```

Inspect tokens, every span, candidate frames, costs, inheritance, violations,
and unexplained tokens:

```bash
gazetteer-match match 'flick on the kichen lights' --debug
```

JSON including candidates:

```bash
gazetteer-match match 'open the bedroom blinds' --debug --json
```

Only show lexical spans:

```bash
gazetteer-match spans 'flik the bedroom lights on'
```

Show slot-combination reachability:

```bash
gazetteer-match support
```

Measure strict intent, combination, and slot coverage against the non-wildcard
English fallback tests in a local `intent-sentences` checkout. By default,
sentences already handled by the lean `speech_to_phrase` HassIL subset are
removed before running the gazetteer matcher:

```bash
python script/run_english_coverage.py
```

Run one intent, or repeat the option to select several:

```bash
python script/run_english_coverage.py --intent HassTurnOn
python script/run_english_coverage.py \
  --intent HassTurnOn --intent HassTurnOff
```

Measure only the existing test sentences that the lean
`speech_to_phrase: true` HassIL template subset recognizes with the expected
intent and slot combination:

```bash
python script/run_english_coverage.py --speech-to-phrase
```

To reproduce coverage across every non-wildcard sentence, including the lean
HassIL cohort, use:

```bash
python script/run_english_coverage.py --all-sentences
```

This mode uses the local HassIL checkout at `~/opt/hassil`; override it with
`--hassil-dir` when needed. It can be combined with one or more `--intent`
filters.

### Home fixture tests

The sample gazetteer lives in `tests/home.yaml`. Positive end-to-end cases are
grouped by intent family under `tests/sentences/`; each record contains an
utterance, optional location context, and the complete ordered list of expected
intent frames. The corpus is based on Home Assistant's
[built-in sentence starter pack](https://www.home-assistant.io/voice_control/builtin_sentences).

```yaml
cases:
  - sentences:
      - is the front door locked
      - is the front door currently locked
    frames:
      - intent: HassGetState
        combination: name_state
        response_key: one_yesno
        slots: {name: lock.front_door, state: locked}
```

`tests/test_sentences.py` discovers every YAML file in that directory and
compares every resulting intent, combination, slot dictionary, and response
key. Add home-specific positive coverage there instead of embedding it in
Python test code.

A case or series turn may use either one `sentence` or a non-empty `sentences`
list. Every sentence in the list is tested with the same context and expected
frames. Alternatives on a series turn must also produce identical follow-up
targets, so the next turn has unambiguous conversation state. Use a list for
natural equivalent forms, not for different intents or target scopes.

Set `requires_fuzzy: true` on a case that specifically exercises typo recovery.
Besides checking the frames, the runner then requires at least one selected
lexical span to come from fuzzy matching.

For `HassGetState`, the response key preserves question wording that is not
represented by slots. For example, `are any doors unlocked` and `which doors
are unlocked` both produce `HassGetState.domain_state` with `{domain: lock,
state: unlocked}`, but their frames carry `any` and `which` respectively.
Aggregate query keys (`any`, `all`, `which`, and `how_many`) come from lexical
hints configured in `vocabulary.yaml`; fixed shapes use their corresponding
combination default (`one`, `one_yesno`, or `where`).

Multi-turn follow-ups use a `series` record. Turns run in order, and the
targets from each accepted result are automatically passed to the next turn as
`previous_targets`:

```yaml
series:
  - name: kitchen lights follow-up
    turns:
      - sentence: turn on the kitchen lights
        frames:
          - intent: HassTurnOn
            combination: area_domain
            slots: {area: kitchen, domain: light}
      - sentence: turn them off
        frames:
          - intent: HassTurnOff
            combination: area_domain
            slots: {area: kitchen, domain: light}
```

`context_area` and `context_floor` may be set on the series as defaults or on
an individual turn as overrides. Existing `cases` and `series` may coexist in
the same YAML file.

The matcher runs after the built-in sentence and HassIL recognizers, so this
home corpus is not intended to duplicate every upstream sentence. It keeps a
small set of canonical anchors, then emphasizes useful fallback behavior such
as aliases, terse queries, alternate word order, scoped state questions,
coordination, anaphora, and fuzzy names. The broader upstream fallback cohort
is measured separately by `script/run_english_coverage.py`.

When adding alternatives to a `sentences` list, first check them against the
full English HassIL grammar. Keep additions that receive no built-in match and
that represent wording a user might reasonably choose; the purpose is to show
how this matcher complements the built-in grammar, not to accumulate contrived
phrases it happens to accept.

### Rejection tests

Negative examples live in `tests/rejections.yaml`, separate from positive
intent coverage so the two metrics cannot mask one another. Records look like:

```yaml
cases:
  - sentence: turn on seventeen red kitchen lights
    category: contradictory_semantic_evidence
  - sentence: write a poem about my kitchen lights
    category: downstream_llm
  - sentence: ceiling light
    context_area: kitchen
    category: incomplete_command
```

The data-driven `tests/test_rejections.py` test calls `interpret` with any
supplied context and asserts only that `accepted` is false; rejection-reason
strings are diagnostic and too brittle to make part of the contract. These
should be reported separately as a false acceptance rate, with paired
positive/negative examples when a small wording change is safety-significant.

### Rejection responses

Rejected interpretations include both a stable category and a concise,
user-facing response:

```python
result = matcher.interpret("clippy")

assert result.rejection_code == "no_action"
assert result.response == (
    "Sorry, I see you're targeting 'Clippy' (a lawn mower), "
    "but I don't know what action to take."
)
```

`reason` remains the detailed matcher diagnostic for debugging. It should not
be spoken to a user. `rejection_code` lets an integration choose a different
delivery policy, while `response` is ready to use when no fallback LLM is
available. Accepted interpretations have neither field.

All response wording, action labels, device/domain labels, and target phrase
templates live in `data/responses.yaml`. Supply `responses` (a dictionary or
YAML path), the compatible `responses_path`, or `--responses` to the CLI to
replace them for another deployment or language.

The runner excludes combinations declaring `wildcard_slots`, prints coverage
by intent and categorized failure samples, and exits successfully even when
sentences are uncovered. Use `--json` for machine-readable output or
`--tests-dir` to select a different checkout.

Override any data file:

```bash
gazetteer-match match 'turn on the office lamp' \
  --home my-home.yaml \
  --vocabulary my-vocabulary.yaml \
  --responses my-responses.yaml
```

## Vocabulary/data files

### `data/vocabulary.yaml`

Contains the language-dependent vocabulary:

- skip/request-wrapper phrases
- conjunctions
- relation/property cues
- domain words
- device-class words
- states
- colors
- media classes
- units
- action phrases and virtual-action lowerings
- fuzzy thresholds/policies
- a small number of combination-specific semantic cues

For example, `open` is a virtual action that lowers to `HassTurnOn` but is
constrained to `cover`/`valve` domains. `lock` similarly lowers to
`HassTurnOn` but is constrained to `lock` entities.

Actions may opt in to exact matching across grammatical skip tokens with
`allow_interstitial_skips: true`. Only skip tokens between phrase tokens are
ignored, one token per gap by default; matched action indexes remain explicit,
so semantic content and conjunctions cannot be crossed accidentally.

### `tests/home.yaml`

The test-only sample dynamic gazetteer. Applications should pass a dictionary
or YAML path generated from their own Home Assistant instance:

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

Exact aliases are tagged first. Pure-Python fuzzy lookup adds additional
`name`, `area`, and `floor` candidates only where useful. Entity names may
also omit a complete interior word when the first and final words still match;
for example, `Josh's Lights` can match `Josh's Office Lights`. Equally good
shortened names remain ambiguous.

### Home Assistant intent metadata

The matcher reads the catalog returned by `home_assistant_intents.get_intent_info()`
instead of duplicating combinations in Python or loading a separate YAML file.
Combinations with `wildcard_slots` are intentionally ignored.

### `data/responses.yaml`

Contains rejection templates plus localized action, domain, device-class, and
target labels. The matcher selects a structured rejection category in code,
but all words presented to the user come from this file. Missing specialized
templates fall back to its `generic` response.

## Number words with Unicode RBNF

`unicode-rbnf` is a formatter, not a number-word parser. The MVP reverses it:

1. Ask `RbnfEngine` for cardinal/ordinal spellings up to configured maxima.
2. Normalize those spellings using the same tokenizer as utterances.
3. Insert the spellings into a trie.
4. Longest-match the trie against input tokens.

This makes CLDR/RBNF the number-word vocabulary source without embedding a
second English number lexicon in Python.

Step 1 spells tens of thousands of numbers and dominates the cost of building a
matcher. The trie depends only on the language and the maxima, and is read-only
once built, so it is cached and shared by every matcher that wants the same one.
This matters to applications that rebuild a matcher when their home changes:
without sharing, renaming one entity re-spells every number in the language.

The configured number joiner (`and` for English) can be skipped *inside an
otherwise valid longer number*. This lets:

```text
one hundred and twenty seconds
```

become a single `120` number span rather than a conjunction between two
commands.

## Pure-Python fuzzy matching

`gazetteer_matcher/fuzzy.py` implements:

- Levenshtein distance
- optimal-string-alignment Damerau-Levenshtein distance
- normalized similarity
- token-sort similarity
- `extract()` for top-N vocabulary matches

The default is Damerau-Levenshtein because adjacent transpositions are common
in noisy text/ASR-like output and names are short.

Actions use a higher threshold than entity/location names. For opposing action
families (`on/off`, `open/close`, `lock/unlock`, etc.), a close fuzzy tie is
not allowed to decide the polarity.

## Conjunction model

The MVP treats configured conjunctions as coordination boundaries.

```text
turn on the kitchen and hallway lights
```

is split into two semantic segments. The second target supplies the unique
`light` domain, which can be inherited by the first target, while the
`TURN_ON` action is inherited by the second segment:

```text
HassTurnOn(area=kitchen, domain=light)
HassTurnOn(area=hallway, domain=light)
```

Multiple actions work independently:

```text
turn off the kitchen lights and open the bedroom blinds
```

becomes:

```text
HassTurnOff(area=kitchen, domain=light)
HassTurnOn(name=cover.bedroom_blinds)
```

Property coordination can reuse a preceding target:

```text
set the living room lights red and fifty percent
```

becomes two `HassLightSet` frames, one for color and one for brightness.

The inheritance is deliberately conservative: only a unique value is shared,
and an action-inherited segment with no local target is required to retain the
previous target rather than silently changing scope.

## Quantity and scope

Quantity words such as `all` and `every` do not determine location. They apply
within the scope selected by the rest of the command:

- a named area or floor selects that location
- `here` selects the supplied context area
- `everywhere` or `in the house` selects the whole home
- an unscoped `all` uses a global combination when the intent/domain supports
  one; otherwise it uses the context-area combination

For example, `turn all the lights in the kitchen off` targets the kitchen,
`turn off all the fans` uses the context area, and `turn all lights off` uses
the whole-house light combination. Conflicting local and home-wide scope, such
as `kitchen lights everywhere`, is rejected.

## Candidate selection

Candidates are compared lexicographically. Conceptually:

```text
(
    constraint violations,
    unexplained important tokens,
    unexplained unimportant tokens,
    -action evidence tokens,
    fuzzy action count,
    fuzzy slot count,
    inherited fields,
    context mismatch rank,
    target generality,
    fuzzy distance,
    -consumed token count,
)
```

An unexplained token is important when it participates in a recognized
semantic span, such as an action, slot value, number, unit, marker, or cue; it
may support a different intent or interpretation. Other unmatched words are
unimportant. By default, a candidate may leave at most one important token and
two unimportant tokens unexplained. The two limits are independent.

This means a 99% fuzzy match cannot compensate for extra semantic evidence or
a violated intent constraint. If two distinct semantics have exactly the same
best cost, the MVP rejects the utterance as ambiguous.

Example:

```text
turn on seventeen red kitchen lights
```

recognizes both the number `17` and the color `red`, but no valid `HassTurnOn`
combination consumes them. The utterance is rejected because it has two
unexplained important tokens.

## Intent constraints currently enforced

The generic validator uses the Home Assistant intent metadata for:

- exact required slot sets per combination
- wildcard exclusion
- `context_area` metadata
- `inferred_domains`
- `name_domains`

It additionally enforces:

- virtual-action domain constraints
- entity/area and entity/floor consistency
- context-area materialization and duplicate-name context ranking
- independent quantity and geographic-scope constraints
- device-class/domain compatibility
- explicit combination cues configured in YAML
- no incompatible reuse of the same lexical evidence for multiple slots
- separate limits for unexplained semantic and unmatched content
