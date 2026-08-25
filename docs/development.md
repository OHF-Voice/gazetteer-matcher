# Development

## Environment and tests

Install the package with development dependencies and run the complete suite:

```bash
pip install -e '.[dev]'
pytest
```

The suite includes API behavior, matcher constraints, fuzzy scorer equivalence,
number parsing, positive sentence fixtures, and hard rejection fixtures.

## CLI diagnostics

Interpret normally:

```bash
gazetteer-match match 'turn on the kitchen and hallway lights'
```

Supply location context:

```bash
gazetteer-match match 'turn off the lights' \
  --context-area Kitchen \
  --context-floor 'Ground Floor'
```

Inspect tokens, spans, candidates, costs, inheritance, violations, and
unexplained tokens:

```bash
gazetteer-match match 'flick on the kichen lights' --debug
```

Emit the same information as JSON:

```bash
gazetteer-match match 'open the bedroom blinds' --debug --json
```

Other useful commands:

```bash
gazetteer-match spans 'flik the bedroom lights on'
gazetteer-match support
```

The `spans` command shows lexical recognition without frame generation.
`support` summarizes which non-wildcard Home Assistant slot combinations are
reachable through configured actions.

## Positive sentence fixtures

The sample home is `tests/home.yaml`. End-to-end cases are grouped by intent
family under `tests/sentences/`:

```yaml
language: en

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

`tests/test_sentences.py` discovers every YAML file and compares the complete
ordered intent, combination, slot dictionary, and response key.

A case may use one `sentence` or a non-empty `sentences` list. Alternatives in
one list must produce identical frames. Set `requires_fuzzy: true` when the
case specifically exercises typo recovery; the runner then requires at least
one selected fuzzy span.

Multi-turn behavior uses a `series`:

```yaml
series:
  - name: kitchen lights follow-up
    turns:
      - sentence: turn on the kitchen lights
        frames:
          - intent: HassTurnOn
            combination: area_domain
            response_key: lights_area
            slots: {area: kitchen, domain: light}
      - sentence: turn them off
        frames:
          - intent: HassTurnOff
            combination: area_domain
            response_key: lights_area
            slots: {area: kitchen, domain: light}
```

Each accepted turn's exported targets are passed into the next. Context may be
set for the whole series or overridden on an individual turn.

The fixture corpus focuses on useful fallback behavior beyond the built-in
sentence grammar: aliases, alternate word order, terse and scoped queries,
coordination, explicit references, and fuzzy targets.

## Hard rejection fixtures

Negative examples live separately in `tests/rejections.yaml` so positive and
negative coverage cannot hide one another:

```yaml
cases:
  - sentence: turn on seventeen red kitchen lights
    category: contradictory_semantic_evidence
  - sentence: set bedroom brightness to 150 percent
    category: invalid_numeric_value
  - sentence: write a poem about my kitchen lights
    category: downstream_llm
```

The rejection runner asserts that each utterance remains unaccepted and has a
bounded user-facing response. Category labels are reporting metadata, not
matcher input. Internal reason strings are intentionally not fixture contracts.

## Upstream English coverage

`script/run_english_coverage.py` measures strict intent, combination, and slot
coverage against non-wildcard English tests from a local `intent-sentences`
checkout. By default, it removes sentences already handled by the lean
`speech_to_phrase` HassIL subset:

```bash
python script/run_english_coverage.py
```

Filter intents:

```bash
python script/run_english_coverage.py --intent HassTurnOn
python script/run_english_coverage.py \
  --intent HassTurnOn --intent HassTurnOff
```

Measure only the lean HassIL cohort:

```bash
python script/run_english_coverage.py --speech-to-phrase
```

Measure every non-wildcard sentence, including that cohort:

```bash
python script/run_english_coverage.py --all-sentences
```

The all-sentences mode uses the local HassIL checkout at `~/opt/hassil`; use
`--hassil-dir` to override it. `--tests-dir` selects a different
`intent-sentences` checkout and `--json` emits machine-readable output.

Coverage is informational: uncovered sentences are categorized and printed,
but do not make the runner fail. Combinations declaring `wildcard_slots` are
excluded.

## Release checks

Before release, run the tests and configured static tools, build both the wheel
and source distribution, install the wheel into a clean environment, and check
the artifacts with Twine. Native wheels should be exercised on every supported
Python/platform target; source installation must retain the Python fallback
when no compiler is available.
