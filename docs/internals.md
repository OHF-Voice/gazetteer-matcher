# Internals

## Recognition pipeline

The matcher:

1. normalizes an utterance into indexed tokens;
2. tags exact, numeric, derived, and fuzzy semantic spans;
3. splits coordinated segments;
4. generates slot selections for reachable Home Assistant combinations;
5. validates each candidate against upstream metadata and local constraints;
6. chooses one best semantic interpretation or rejects the request.

Frames are validated against the catalog returned by
`home_assistant_intents.get_intent_info()`. The project does not duplicate that
slot schema in its own YAML. Combinations declaring `wildcard_slots` are
intentionally excluded.

## Home Assistant constraints

The generic validator consumes upstream metadata for:

- exact required slots;
- context-area behavior;
- inferred domains;
- allowed name domains;
- wildcard exclusion.

Additional constraints enforce:

- virtual-action domain compatibility;
- entity/area and entity/floor consistency;
- context and lexical-location disambiguation of duplicate names;
- quantity and geographic scope;
- device-class/domain compatibility;
- combination-specific semantic cues;
- single use of concrete lexical evidence across slots;
- bounded numeric values and timer durations;
- independent limits for unexplained semantic and unmatched content.

## Number words with Unicode RBNF

`unicode-rbnf` formats numbers rather than parsing them. The matcher reverses
the generated vocabulary:

1. format cardinal and ordinal values up to configured maxima;
2. tokenize those spellings with the utterance tokenizer;
3. insert the spellings into a trie;
4. longest-match the trie against input tokens.

This uses CLDR/RBNF as the number-word source without maintaining a second
English number lexicon. The generated trie is immutable and cached by language
and number settings, so replacing a home does not respell tens of thousands of
numbers.

Configured joiners such as English `and` may be skipped only inside a valid
longer number. `one hundred and twenty seconds` therefore becomes a single
`120` span rather than two coordinated commands.

Signed literals and configured spoken signs retain their polarity. Structured
slot validation then rejects percentages outside 0–100, fractional percentage
values, negative durations, and zero-length timer starts while allowing valid
negative climate temperatures.

## Fuzzy matching

The Python reference scorer implements:

- Levenshtein distance;
- optimal-string-alignment Damerau-Levenshtein distance;
- normalized similarity;
- token-sort similarity;
- deterministic top-N extraction.

Damerau-Levenshtein is the default because adjacent transpositions are common
in short names and ASR-like input.

When compiled, `_fuzzy_native.cpp` scores reusable normalized choice batches in
C++17. A complete token-length bucket is scored in one Python-to-native call,
the GIL is released during distance calculation, and only top matches are
returned. The Python implementation remains the fallback. Generated Unicode
and edit corpora require both implementations to produce identical scores and
ordering.

Action, entity, area, and floor batches are compiled when the home tagger is
built. Name-elision token tuples are cached as well. Actions use a higher cutoff
than names and locations, and opposing action families require enough margin
that a fuzzy tie cannot choose a polarity.

## Coordination

Configured conjunctions define semantic segments. Unique evidence can be
shared across adjacent segments: an action may carry forward, a domain may
carry backward, or a property clause may refer to a preceding named target.

```text
turn on the kitchen and hallway lights
```

The second segment supplies the unique light domain; the first supplies the
action. The result is two area/domain frames.

```text
turn on the hallway light and set its brightness to 40%
```

The possessive reference materializes the preceding named entity as an
inherited slot for the property frame. A singular possessive is rejected for a
group target.

Only a unique shareable value is inherited. If an action carries forward into
a targetless segment, that segment is constrained to the preceding target.

## Quantity and scope

Quantifiers and locations are independent semantic dimensions. Area, floor,
context-here, and explicit whole-home cues select scope. `all` chooses a global
combination only when the intent/domain supports one; otherwise context can
materialize an area combination. Local and whole-home evidence in the same
request is a constraint violation.

## Candidate selection

Candidates are ordered lexicographically by:

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

An important unexplained token participates in recognized semantic evidence,
such as an action, slot value, number, unit, marker, or cue. Other unmatched
words are unimportant. The two limits are configured independently.

This ordering means fuzzy similarity cannot compensate for a constraint
violation or stronger unexplained semantics. If distinct semantic keys have
the same best cost, the interpretation is rejected as ambiguous.

For example, `turn on seventeen red kitchen lights` recognizes both a number
and a color, but no valid turn-on shape consumes them. The request is rejected
instead of silently discarding the extra instructions.

## Response keys

Response keys are derived from the same `home-assistant-intents` sentence
corpus used for slot validation, narrowed by target domain. Where one shape has
several response variants, lexical hints such as `which` or `how many` take
precedence. A configured fixed-shape default comes next; a genuinely unresolved
response remains `None`.
