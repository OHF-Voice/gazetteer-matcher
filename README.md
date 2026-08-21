# gazetteer-matcher

An MVP, constraint-driven intent recognizer for Home Assistant voice commands.
It is deliberately **not** a statistical intent classifier. It tags lexical
spans, generates possible semantic frames, validates those frames against
`OHF-Voice/intents` `intents.yaml`, and rejects interpretations that leave
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
- Validate against the real `intents.yaml` slot-combination catalog.
- Prefer lexicographic costs (violations, unexplained content, fuzziness,
  inheritance, etc.) over arbitrary weighted evidence scores.
- Make every intermediate span and candidate frame inspectable.

## Current `intents.yaml` snapshot

The included snapshot has:

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

The bundled `intents.yaml` is unmodified upstream data and retains its CC BY
4.0 licensing; see `THIRD_PARTY_NOTICES.md` and
`OHF-Voice-intents-LICENSE.md`.

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

## Quick start

```python
from gazetteer_matcher import GazetteerMatcher

matcher = GazetteerMatcher()

result = matcher.interpret("flick on the kichen lights")
assert result.accepted

for frame in result.frames:
    print(frame.intent, frame.combination, frame.slots)
```

With the sample `home.yaml`, this yields approximately:

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
open, and close actions. `it` requires one named entity; `them` may also reuse
an area, floor, or whole-home selector. The current implementation rejects
multiple previous target references, pronouns mixed with an explicit target,
and any action whose existing intent/domain constraints do not support the
target.

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
python scripts/run_english_coverage.py
```

Run one intent, or repeat the option to select several:

```bash
python scripts/run_english_coverage.py --intent HassTurnOn
python scripts/run_english_coverage.py \
  --intent HassTurnOn --intent HassTurnOff
```

Measure only the existing test sentences that the lean
`speech_to_phrase: true` HassIL template subset recognizes with the expected
intent and slot combination:

```bash
python scripts/run_english_coverage.py --speech-to-phrase
```

To reproduce coverage across every non-wildcard sentence, including the lean
HassIL cohort, use:

```bash
python scripts/run_english_coverage.py --all-sentences
```

This mode uses the local HassIL checkout at `~/opt/hassil`; override it with
`--hassil-dir` when needed. It can be combined with one or more `--intent`
filters.

### Rejection tests

Negative examples live in `tests/rejections/en.yaml`, separate from positive
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

The runner excludes combinations declaring `wildcard_slots`, prints coverage
by intent and categorized failure samples, and exits successfully even when
sentences are uncovered. Use `--json` for machine-readable output or
`--tests-dir` to select a different checkout.

Override any data file:

```bash
gazetteer-match match 'turn on the office lamp' \
  --home my-home.yaml \
  --vocabulary my-vocabulary.yaml \
  --intents path/to/OHF-Voice/intents/intents.yaml
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

### `data/home.yaml`

The sample dynamic gazetteer. Replace this with a generated file from a Home
Assistant instance:

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
`name`, `area`, and `floor` candidates only where useful.

### `data/intents.yaml`

A snapshot of the upstream OHF-Voice slot-combination catalog. The matcher
loads this dynamically instead of duplicating combinations in Python.
Combinations with `wildcard_slots` are intentionally ignored.

## Number words with Unicode RBNF

`unicode-rbnf` is a formatter, not a number-word parser. The MVP reverses it:

1. Ask `RbnfEngine` for cardinal/ordinal spellings up to configured maxima.
2. Normalize those spellings using the same tokenizer as utterances.
3. Insert the spellings into a trie.
4. Longest-match the trie against input tokens.

This makes CLDR/RBNF the number-word vocabulary source without embedding a
second English number lexicon in Python.

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

The generic validator uses `intents.yaml` for:

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

## Files

```text
gazetteer-matcher/
├── pyproject.toml
├── README.md
├── LICENSE
├── THIRD_PARTY_NOTICES.md
├── OHF-Voice-intents-LICENSE.md
├── examples/
│   └── notebook_demo.py
├── src/gazetteer_matcher/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── debug.py
│   ├── fuzzy.py
│   ├── matcher.py
│   ├── models.py
│   ├── numbers.py
│   ├── schemas.py
│   ├── tagger.py
│   └── data/
│       ├── home.yaml
│       ├── intents.yaml
│       └── vocabulary.yaml
└── tests/
    ├── test_fuzzy.py
    └── test_matcher.py
```

## Deliberate MVP limitations

- English is the provided vocabulary; the architecture is locale-oriented but
  action/domain/etc. vocabularies need to be supplied per language.
- No dependency parser and no general modifier/relational-description parser.
- No dialogue state yet; this project focuses on single-turn recognition.
- Wildcard combinations are excluded as requested.
- Residual slots (`message`, `search_query`, `conversation_command`) are
  heuristic and intentionally conservative.
- Fuzzy matching is quadratic-ish pure Python and aimed at the small candidate
  sets expected after HA domain/area constraints, not million-entry search.
- This is not production-safe device control without substantially broader
  corpus testing, especially around polarity and destructive actions.
