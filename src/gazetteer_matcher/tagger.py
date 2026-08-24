from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .config import MatcherConfig, phrase_tokens
from .fuzzy import extract
from .models import Span, Token
from .numbers import NumberWordTrie


@dataclass(frozen=True)
class PhraseEntry:
    tokens: tuple[str, ...]
    tag: str
    value: Any
    meta: dict[str, Any]
    source: str = "exact"


class SpanTagger:
    def __init__(self, config: MatcherConfig) -> None:
        self.config = config
        vocab = config.vocabulary
        number_cfg = vocab.get("numbers", {})
        self.number_trie = NumberWordTrie.shared(
            str(vocab["language"]),
            max_cardinal=int(number_cfg.get("max_cardinal", 10000)),
            max_ordinal=int(number_cfg.get("max_ordinal", 100)),
            joiners=list(number_cfg.get("joiners") or []),
        )
        self.entries: list[PhraseEntry] = []
        self._first_token: dict[str, list[PhraseEntry]] = {}
        self.action_phrase_to_keys: dict[str, list[str]] = {}
        self.fuzzy_choices: dict[str, dict[str, list[tuple[Any, dict[str, Any]]]]] = {
            "area": {},
            "floor": {},
            "name": {},
        }
        self._build_entries()

    def _add(
        self,
        phrase: str,
        tag: str,
        value: Any,
        *,
        meta: dict[str, Any] | None = None,
        source: str = "exact",
        fuzzy: bool = False,
    ) -> None:
        tokens = phrase_tokens(str(phrase))
        if not tokens:
            return
        entry = PhraseEntry(
            tokens=tokens, tag=tag, value=value, meta=meta or {}, source=source
        )
        self.entries.append(entry)
        self._first_token.setdefault(tokens[0], []).append(entry)
        if fuzzy and tag in self.fuzzy_choices:
            normalized = " ".join(tokens)
            self.fuzzy_choices[tag].setdefault(normalized, []).append(
                (value, meta or {})
            )

    def _build_entries(self) -> None:
        vocab = self.config.vocabulary

        for phrase in vocab.get("skip_phrases") or []:
            self._add(phrase, "skip", None)

        anaphora = vocab.get("anaphora") or {}
        for number, phrases in (anaphora.get("pronouns") or {}).items():
            for phrase in phrases or []:
                self._add(phrase, "anaphor", number)
        for phrase in anaphora.get("modifiers") or []:
            self._add(phrase, "anaphora_modifier", phrase)

        for number, phrases in (vocab.get("coordination_references") or {}).items():
            for phrase in phrases or []:
                self._add(phrase, "coordination_reference", number)

        for intent, spec in (vocab.get("response_hints") or {}).items():
            for response_key, phrases in (spec.get("phrases") or {}).items():
                for phrase in phrases or []:
                    self._add(
                        phrase,
                        "response_key",
                        response_key,
                        meta={"intent": intent},
                    )

        for phrase, kind in (vocab.get("conjunctions") or {}).items():
            self._add(phrase, "conjunction", kind)

        for relation, phrases in (vocab.get("relations") or {}).items():
            for phrase in phrases or []:
                self._add(phrase, "relation", relation)

        for cue, phrases in (vocab.get("cues") or {}).items():
            for phrase in phrases or []:
                self._add(phrase, "cue", cue)

        for marker, phrases in (vocab.get("slot_markers") or {}).items():
            for phrase in phrases or []:
                self._add(phrase, "slot_marker", marker)

        for unit, phrases in (vocab.get("units") or {}).items():
            for phrase in phrases or []:
                self._add(phrase, "unit", unit)

        for domain, phrases in (vocab.get("domains") or {}).items():
            for phrase in phrases or []:
                self._add(phrase, "domain", domain)

        for device_class, spec in (vocab.get("device_classes") or {}).items():
            meta: dict[str, Any] = {"domains": list(spec.get("domains") or [])}
            for phrase in spec.get("phrases") or []:
                self._add(phrase, "device_class", device_class, meta=meta)

        for state, phrases in (vocab.get("states") or {}).items():
            for phrase in phrases or []:
                self._add(phrase, "state", state)

        for color, phrases in (vocab.get("colors") or {}).items():
            for phrase in phrases or []:
                self._add(phrase, "color", color)

        for media_class, phrases in (vocab.get("media_classes") or {}).items():
            for phrase in phrases or []:
                self._add(phrase, "media_class", media_class)

        for value, phrases in (vocab.get("percent_values") or {}).items():
            for phrase in phrases or []:
                self._add(phrase, "percent_value", int(value))

        for value, phrases in (vocab.get("temperature_values") or {}).items():
            for phrase in phrases or []:
                self._add(phrase, "temperature_value", int(value))

        for area_id, spec in (self.config.home.get("areas") or {}).items():
            phrases = [spec.get("name")] + list(spec.get("aliases") or [])
            meta = {"floor": spec.get("floor"), "name": spec.get("name")}
            for phrase in phrases:
                if phrase:
                    self._add(phrase, "area", area_id, meta=meta, fuzzy=True)

        for floor_id, spec in (self.config.home.get("floors") or {}).items():
            phrases = [spec.get("name")] + list(spec.get("aliases") or [])
            meta = {"name": spec.get("name")}
            for phrase in phrases:
                if phrase:
                    self._add(phrase, "floor", floor_id, meta=meta, fuzzy=True)

        for entity_id, spec in (self.config.home.get("entities") or {}).items():
            phrases = [spec.get("name")] + list(spec.get("aliases") or [])
            entity_area = spec.get("area")
            area_spec = (self.config.home.get("areas") or {}).get(entity_area) or {}
            meta = {
                "entity_id": entity_id,
                "name": spec.get("name"),
                "domain": spec.get("domain") or entity_id.split(".", 1)[0],
                "device_class": spec.get("device_class"),
                "area": entity_area,
                "floor": spec.get("floor") or area_spec.get("floor"),
            }
            for phrase in phrases:
                if phrase:
                    self._add(phrase, "name", entity_id, meta=meta, fuzzy=True)
            area_phrases = [area_spec.get("name")] + list(
                area_spec.get("aliases") or []
            )
            # Derive reordered forms from the canonical name only.  An alias
            # such as "lounge lights" may intentionally be broader than the
            # entity name and must not turn "lights in the living room" into
            # a reference to one specific light entity.
            for phrase in self._area_qualified_name_phrases(
                [spec.get("name")], area_phrases
            ):
                self._add(
                    phrase,
                    "name",
                    entity_id,
                    meta=meta,
                    source="area_qualified_name",
                )

        for action_key, spec in (vocab.get("actions") or {}).items():
            for phrase in spec.get("phrases") or []:
                self._add(phrase, "action", action_key, meta={"action": action_key})
                normalized = " ".join(phrase_tokens(phrase))
                self.action_phrase_to_keys.setdefault(normalized, []).append(action_key)

        # Prefer longer exact phrases at the same token start.
        for entries in self._first_token.values():
            entries.sort(key=lambda entry: -len(entry.tokens))

    @staticmethod
    def _area_qualified_name_phrases(
        entity_phrases: Iterable[str | None], area_phrases: Iterable[str | None]
    ) -> set[str]:
        """Reorder an entity's leading area into a spoken area qualifier.

        An entity named ``Living Room Ceiling Lights`` may naturally be
        addressed as ``ceiling lights in the living room``.  Only names whose
        leading words exactly identify the entity's configured area are
        expanded, so a generic alias is never assigned to an entity in another
        area.
        """
        normalized_areas = {
            phrase_tokens(str(phrase)) for phrase in area_phrases if phrase
        }
        result: set[str] = set()
        for entity_phrase in entity_phrases:
            if not entity_phrase:
                continue
            entity_words = phrase_tokens(str(entity_phrase))
            for leading_area in normalized_areas:
                if (
                    len(entity_words) <= len(leading_area)
                    or entity_words[: len(leading_area)] != leading_area
                ):
                    continue
                target_words = entity_words[len(leading_area) :]
                for spoken_area in normalized_areas:
                    result.add(" ".join((*target_words, "in", *spoken_area)))
                    result.add(" ".join((*target_words, "in", "the", *spoken_area)))
        return result

    @staticmethod
    def _span_text(tokens: list[Token], start: int, end: int) -> str:
        return " ".join(token.raw for token in tokens[start:end])

    def exact_spans(self, tokens: list[Token]) -> list[Span]:
        result: list[Span] = []
        token_texts = [token.text for token in tokens]
        for start, token in enumerate(token_texts):
            for entry in self._first_token.get(token, []):
                end = start + len(entry.tokens)
                if end <= len(tokens) and tuple(token_texts[start:end]) == entry.tokens:
                    result.append(
                        Span(
                            start=start,
                            end=end,
                            tag=entry.tag,
                            value=entry.value,
                            text=self._span_text(tokens, start, end),
                            source=entry.source,
                            meta=dict(entry.meta),
                        )
                    )
        result.extend(self._interstitial_skip_action_spans(tokens, result))
        result.extend(self._interstitial_target_action_spans(tokens, result))
        result.extend(self._separable_action_spans(tokens))
        return result

    def _interstitial_skip_action_spans(
        self, tokens: list[Token], exact_spans: list[Span]
    ) -> list[Span]:
        """Match opted-in action phrases across grammatical skip tokens.

        Skips are permitted only between phrase tokens. The resulting span
        records only the action phrase indexes as consumed; ordinary skip-span
        handling remains responsible for the interstitial tokens.
        """
        skippable_indexes: set[int] = set()
        for span in exact_spans:
            if span.tag == "skip":
                skippable_indexes.update(span.token_indexes())
        if not skippable_indexes:
            return []

        words = [token.text for token in tokens]
        result: list[Span] = []
        for action_key, spec in (self.config.vocabulary.get("actions") or {}).items():
            if not spec.get("allow_interstitial_skips"):
                continue
            max_skipped = int(spec.get("max_interstitial_skip_tokens", 1))
            if max_skipped < 1:
                continue

            for phrase in spec.get("phrases") or []:
                phrase_words = phrase_tokens(str(phrase))
                if len(phrase_words) < 2:
                    continue
                for start, word in enumerate(words):
                    if word != phrase_words[0]:
                        continue

                    consumed_indexes = [start]
                    cursor = start + 1
                    used_skip = False
                    for expected_word in phrase_words[1:]:
                        skipped = 0
                        while (
                            cursor < len(words)
                            and words[cursor] != expected_word
                            and cursor in skippable_indexes
                            and skipped < max_skipped
                        ):
                            cursor += 1
                            skipped += 1
                            used_skip = True
                        if cursor >= len(words) or words[cursor] != expected_word:
                            break
                        consumed_indexes.append(cursor)
                        cursor += 1
                    else:
                        if not used_skip:
                            continue
                        result.append(
                            Span(
                                start=start,
                                end=consumed_indexes[-1] + 1,
                                tag="action",
                                value=action_key,
                                text=self._span_text(
                                    tokens, start, consumed_indexes[-1] + 1
                                ),
                                source="interstitial_skip",
                                meta={
                                    "action": action_key,
                                    "matched": str(phrase),
                                    "consumed_indexes": consumed_indexes,
                                },
                            )
                        )
        return result

    def _interstitial_target_action_spans(
        self, tokens: list[Token], exact_spans: list[Span]
    ) -> list[Span]:
        """Match opted-in action phrases around an exact entity name.

        The target's indexes are deliberately excluded from the action span's
        consumed indexes, allowing ``return Rover to base`` to retain Rover as
        the target of the configured ``return to base`` action.
        """
        names_by_start: dict[int, list[Span]] = {}
        for span in exact_spans:
            if span.tag == "name" and not span.source.startswith("fuzzy"):
                names_by_start.setdefault(span.start, []).append(span)
        if not names_by_start:
            return []
        for spans in names_by_start.values():
            spans.sort(key=lambda span: span.end - span.start, reverse=True)

        words = [token.text for token in tokens]
        result: list[Span] = []
        for action_key, spec in (self.config.vocabulary.get("actions") or {}).items():
            if not spec.get("allow_interstitial_targets"):
                continue

            for phrase in spec.get("phrases") or []:
                phrase_words = phrase_tokens(str(phrase))
                if len(phrase_words) < 2:
                    continue
                for start, word in enumerate(words):
                    if word != phrase_words[0]:
                        continue

                    consumed_indexes = [start]
                    cursor = start + 1
                    skipped_name: Span | None = None
                    for expected_word in phrase_words[1:]:
                        if cursor < len(words) and words[cursor] != expected_word:
                            candidates = names_by_start.get(cursor, [])
                            if skipped_name is None and candidates:
                                skipped_name = candidates[0]
                                cursor = skipped_name.end
                        if cursor >= len(words) or words[cursor] != expected_word:
                            break
                        consumed_indexes.append(cursor)
                        cursor += 1
                    else:
                        if skipped_name is None:
                            continue
                        allowed_domains = set(spec.get("allowed_domains") or [])
                        if (
                            allowed_domains
                            and skipped_name.meta.get("domain") not in allowed_domains
                        ):
                            continue
                        result.append(
                            Span(
                                start=start,
                                end=consumed_indexes[-1] + 1,
                                tag="action",
                                value=action_key,
                                text=self._span_text(
                                    tokens, start, consumed_indexes[-1] + 1
                                ),
                                source="interstitial_target",
                                meta={
                                    "action": action_key,
                                    "matched": str(phrase),
                                    "consumed_indexes": consumed_indexes,
                                },
                            )
                        )
        return result

    def _separable_action_spans(self, tokens: list[Token]) -> list[Span]:
        result: list[Span] = []
        words = [token.text for token in tokens]
        for action_key, spec in (self.config.vocabulary.get("actions") or {}).items():
            separable = spec.get("separable")
            if not separable:
                continue
            verbs = set(separable.get("verbs") or [])
            particles = set(separable.get("particles") or [])
            max_gap = int(separable.get("max_gap_tokens", 8))
            for start, word in enumerate(words):
                if word not in verbs:
                    continue
                for particle_index in range(
                    start + 1, min(len(words), start + max_gap + 2)
                ):
                    if words[particle_index] not in particles:
                        continue
                    result.append(
                        Span(
                            start=start,
                            end=particle_index + 1,
                            tag="action",
                            value=action_key,
                            text=self._span_text(tokens, start, particle_index + 1),
                            source="separable",
                            meta={
                                "action": action_key,
                                "consumed_indexes": [start, particle_index],
                            },
                        )
                    )
        return result

    @staticmethod
    def _overlap(a: Span, b: Span) -> bool:
        return a.start < b.end and b.start < a.end

    @staticmethod
    def _name_elision_score(query: str, choice: str) -> float | None:
        """Score a shortened name that omits complete interior words.

        Anchoring both ends keeps generic suffixes such as ``office lights``
        from matching every longer entity name that happens to contain them.
        """
        query_tokens = phrase_tokens(query)
        choice_tokens = phrase_tokens(choice)
        if (
            len(query_tokens) < 2
            or len(query_tokens) >= len(choice_tokens)
            or query_tokens[0] != choice_tokens[0]
            or query_tokens[-1] != choice_tokens[-1]
        ):
            return None

        query_index = 0
        for token in choice_tokens:
            if token == query_tokens[query_index]:
                query_index += 1
                if query_index == len(query_tokens):
                    break
        if query_index != len(query_tokens):
            return None

        omitted = len(choice_tokens) - len(query_tokens)
        return 1.0 - omitted / (2 * len(choice_tokens))

    def fuzzy_action_spans(self, tokens: list[Token], exact: list[Span]) -> list[Span]:
        cfg = self.config.vocabulary.get("fuzzy", {})
        cutoff = float(cfg.get("action_cutoff", 0.82))
        margin = float(cfg.get("action_ambiguity_margin", 0.06))
        algorithm = str(cfg.get("algorithm", "damerau"))
        phrases = list(self.action_phrase_to_keys)
        if not phrases:
            return []
        phrase_lengths = {phrase: len(phrase_tokens(phrase)) for phrase in phrases}
        max_tokens = max(phrase_lengths.values()) + int(cfg.get("max_extra_tokens", 1))
        exact_actions = [span for span in exact if span.tag == "action"]
        exact_names = [
            span
            for span in exact
            if span.tag == "name" and not span.source.startswith("fuzzy")
        ]
        result: list[Span] = []
        action_specs = self.config.vocabulary.get("actions") or {}

        for start in range(len(tokens)):
            for end in range(start + 1, min(len(tokens), start + max_tokens) + 1):
                window = Span(start, end, "", None, "")
                if any(
                    self._overlap(window, action)
                    and action.start == start
                    and action.end == end
                    for action in exact_actions
                ):
                    continue
                if any(self._overlap(window, name) for name in exact_names):
                    continue
                query = self._span_text(tokens, start, end)
                query_len = end - start
                choices = [
                    phrase
                    for phrase in phrases
                    if abs(phrase_lengths[phrase] - query_len) <= 1
                ]
                if not choices:
                    continue
                matches = extract(
                    query,
                    choices,
                    cutoff=cutoff,
                    limit=4,
                    algorithm=algorithm,
                )
                semantic: list[tuple[str, float, str]] = []
                for match in matches:
                    for action_key in self.action_phrase_to_keys[match.choice]:
                        semantic.append((action_key, match.score, match.choice))
                semantic.sort(key=lambda item: -item[1])
                if not semantic:
                    continue

                best_action, best_score, best_phrase = semantic[0]
                ambiguous_opposite = False
                for other_action, other_score, _ in semantic[1:]:
                    if (
                        other_action == best_action
                        or best_score - other_score >= margin
                    ):
                        continue
                    first = action_specs.get(best_action, {})
                    second = action_specs.get(other_action, {})
                    if (
                        first.get("opposition_group")
                        and first.get("opposition_group")
                        == second.get("opposition_group")
                        and first.get("polarity") != second.get("polarity")
                    ):
                        ambiguous_opposite = True
                        break
                if ambiguous_opposite:
                    continue

                result.append(
                    Span(
                        start=start,
                        end=end,
                        tag="action",
                        value=best_action,
                        text=query,
                        source="fuzzy_action",
                        similarity=best_score,
                        meta={"matched": best_phrase, "action": best_action},
                    )
                )
        return result

    def fuzzy_gazetteer_spans(
        self, tokens: list[Token], spans: list[Span]
    ) -> list[Span]:
        cfg = self.config.vocabulary.get("fuzzy", {})
        algorithm = str(cfg.get("algorithm", "damerau"))
        limit = int(cfg.get("limit_per_window", 3))
        extra = int(cfg.get("max_extra_tokens", 1))
        cutoffs = {
            "name": float(cfg.get("name_cutoff", 0.76)),
            "area": float(cfg.get("area_cutoff", 0.78)),
            "floor": float(cfg.get("floor_cutoff", 0.78)),
        }

        strong_tags = {
            "action",
            "name",
            "area",
            "floor",
            "domain",
            "device_class",
            "state",
            "color",
            "media_class",
            "number",
            "unit",
            "slot_marker",
            "cue",
            "conjunction",
            "relation",
            "skip",
            "anaphor",
            "anaphora_modifier",
            "coordination_reference",
            "response_key",
        }
        covered: set[int] = set()
        blocked: set[int] = set()
        for span in spans:
            if span.tag in strong_tags and not span.source.startswith("fuzzy"):
                indexes = span.meta.get("consumed_indexes") or range(
                    span.start, span.end
                )
                covered.update(indexes)
            if span.tag in {"action", "conjunction"} and not span.source.startswith(
                "fuzzy"
            ):
                indexes = span.meta.get("consumed_indexes") or range(
                    span.start, span.end
                )
                blocked.update(indexes)

        result: list[Span] = []
        for tag, choice_map in self.fuzzy_choices.items():
            if not choice_map:
                continue
            choice_lengths = {
                choice: len(phrase_tokens(choice)) for choice in choice_map
            }
            max_tokens = max(choice_lengths.values()) + extra
            for start in range(len(tokens)):
                for end in range(start + 1, min(len(tokens), start + max_tokens) + 1):
                    indexes = set(range(start, end))
                    if indexes & blocked:
                        continue
                    if indexes <= covered:
                        continue
                    query = self._span_text(tokens, start, end)
                    if len(query.replace(" ", "")) < 3:
                        continue
                    query_len = end - start
                    choices = [
                        choice
                        for choice in choice_map
                        if abs(choice_lengths[choice] - query_len) <= extra
                    ]
                    if not choices:
                        continue
                    matches = extract(
                        query,
                        choices,
                        cutoff=cutoffs[tag],
                        limit=limit,
                        algorithm=algorithm,
                    )
                    match_scores = {match.choice: match.score for match in matches}
                    match_sources = {match.choice: f"fuzzy_{tag}" for match in matches}
                    if tag == "name":
                        for choice in choices:
                            score = self._name_elision_score(query, choice)
                            if (
                                score is not None
                                and score >= cutoffs[tag]
                                and score > match_scores.get(choice, -1.0)
                            ):
                                match_scores[choice] = score
                                match_sources[choice] = "fuzzy_name_elision"

                    ranked_matches = sorted(
                        match_scores.items(),
                        key=lambda item: (-item[1], len(item[0]), item[0]),
                    )[:limit]
                    for choice, score in ranked_matches:
                        for value, meta in choice_map[choice]:
                            result.append(
                                Span(
                                    start=start,
                                    end=end,
                                    tag=tag,
                                    value=value,
                                    text=query,
                                    source=match_sources[choice],
                                    similarity=score,
                                    meta={**meta, "matched": choice},
                                )
                            )
        return result

    def derived_numeric_spans(
        self, tokens: list[Token], spans: list[Span]
    ) -> list[Span]:
        result: list[Span] = []
        numbers = [
            span
            for span in spans
            if span.tag == "number" and span.meta.get("kind") == "cardinal"
        ]
        units = [span for span in spans if span.tag == "unit"]
        markers = [span for span in spans if span.tag == "slot_marker"]

        def nearby_marker(number: Span, marker_name: str, radius: int = 8) -> bool:
            return any(
                marker.value == marker_name
                and marker.end >= max(0, number.start - radius)
                and marker.start <= min(len(tokens), number.end + radius)
                for marker in markers
            )

        for number in numbers:
            adjacent_units = [unit for unit in units if unit.start == number.end]
            for unit in adjacent_units:
                if unit.value == "percent":
                    result.append(
                        Span(
                            number.start,
                            unit.end,
                            "percent_value",
                            number.value,
                            self._span_text(tokens, number.start, unit.end),
                            source="derived_number",
                            meta={"number_span": number, "unit_span": unit},
                        )
                    )
                elif unit.value == "temperature":
                    result.append(
                        Span(
                            number.start,
                            unit.end,
                            "temperature_value",
                            number.value,
                            self._span_text(tokens, number.start, unit.end),
                            source="derived_number",
                            meta={"number_span": number, "unit_span": unit},
                        )
                    )
                elif unit.value in {"hours", "minutes", "seconds"}:
                    result.append(
                        Span(
                            number.start,
                            unit.end,
                            f"duration_{unit.value}",
                            number.value,
                            self._span_text(tokens, number.start, unit.end),
                            source="derived_number",
                            meta={"number_span": number, "unit_span": unit},
                        )
                    )

            if not adjacent_units:
                if any(
                    nearby_marker(number, marker)
                    for marker in (
                        "brightness",
                        "position",
                        "volume_level",
                        "percentage",
                    )
                ):
                    result.append(
                        Span(
                            number.start,
                            number.end,
                            "percent_value",
                            number.value,
                            number.text,
                            source="derived_number_marker",
                        )
                    )
                if nearby_marker(number, "temperature"):
                    result.append(
                        Span(
                            number.start,
                            number.end,
                            "temperature_value",
                            number.value,
                            number.text,
                            source="derived_number_marker",
                        )
                    )

        words = [token.text for token in tokens]
        duration_parts = {
            "hours": ("minutes", 30),
            "minutes": ("seconds", 30),
        }

        # "half an hour" / "half a minute"
        for start, word in enumerate(words):
            if word != "half" or start + 2 >= len(words):
                continue
            if words[start + 1] not in {"a", "an", "one"}:
                continue
            half_unit = next(
                (
                    span
                    for span in units
                    if span.start == start + 2 and span.value in duration_parts
                ),
                None,
            )
            if half_unit is None:
                continue
            smaller_unit, value = duration_parts[str(half_unit.value)]
            result.append(
                Span(
                    start,
                    half_unit.end,
                    f"duration_{smaller_unit}",
                    value,
                    self._span_text(tokens, start, half_unit.end),
                    source="derived_half_duration",
                )
            )

        # "one and a half hours" contributes one hour and thirty minutes.
        for number in numbers:
            cursor = number.end
            if words[cursor : cursor + 3] != ["and", "a", "half"]:
                continue
            mixed_unit = next(
                (
                    span
                    for span in units
                    if span.start == cursor + 3 and span.value in duration_parts
                ),
                None,
            )
            if mixed_unit is None:
                continue
            larger_unit = str(mixed_unit.value)
            smaller_unit, half_value = duration_parts[larger_unit]
            result.extend(
                [
                    Span(
                        number.start,
                        mixed_unit.end,
                        f"duration_{larger_unit}",
                        number.value,
                        self._span_text(tokens, number.start, mixed_unit.end),
                        source="derived_mixed_duration",
                        meta={
                            "consumed_indexes": list(range(number.start, number.end))
                        },
                    ),
                    Span(
                        cursor,
                        mixed_unit.end,
                        f"duration_{smaller_unit}",
                        half_value,
                        self._span_text(tokens, cursor, mixed_unit.end),
                        source="derived_half_duration",
                        meta={"consumed_indexes": list(range(cursor, mixed_unit.end))},
                    ),
                ]
            )
        return result

    @staticmethod
    def _dedupe(spans: Iterable[Span]) -> list[Span]:
        best: dict[tuple[Any, ...], Span] = {}
        for span in spans:
            key = (span.start, span.end, span.tag, repr(span.value))
            previous = best.get(key)
            if previous is None:
                best[key] = span
                continue
            preference = (
                span.source.startswith("fuzzy"),
                span.source != "exact",
                -span.similarity,
            )
            old_preference = (
                previous.source.startswith("fuzzy"),
                previous.source != "exact",
                -previous.similarity,
            )
            if preference < old_preference:
                best[key] = span
        return sorted(
            best.values(),
            key=lambda span: (
                span.start,
                span.end,
                span.tag,
                -span.similarity,
                repr(span.value),
            ),
        )

    def tag(self, tokens: list[Token]) -> list[Span]:
        exact = self.exact_spans(tokens)
        exact_names = [
            span
            for span in exact
            if span.tag == "name" and not span.source.startswith("fuzzy")
        ]
        exact = [
            span
            for span in exact
            if not (
                span.tag == "action"
                and (
                    self.config.vocabulary.get("actions", {})
                    .get(str(span.value), {})
                    .get("suppress_inside_name")
                )
                and any(
                    name.start <= span.start and span.end <= name.end
                    for name in exact_names
                )
            )
        ]
        number_spans = self.number_trie.find(tokens)
        base = exact + number_spans
        fuzzy_actions = self.fuzzy_action_spans(tokens, base)
        base += fuzzy_actions
        fuzzy_names = self.fuzzy_gazetteer_spans(tokens, base)
        base += fuzzy_names
        derived = self.derived_numeric_spans(tokens, base)
        combined = base + derived

        # A bare duration followed by "timer" is a conventional start command.
        # Synthesize this only when no lexical action exists, so it cannot
        # compete with pause/cancel/status commands that also mention a timer.
        if not any(span.tag == "action" for span in combined):
            target_spans = [
                span
                for span in combined
                if span.tag in {"name", "area", "floor", "domain", "device_class"}
            ]
            power_states = [
                span
                for span in combined
                if span.tag == "state"
                and span.value in {"on", "off"}
                and not any(
                    name.start <= span.start and span.end <= name.end
                    for name in exact_names
                )
            ]
            if target_spans and power_states:
                state_span = power_states[0]
                action_key = "turn_on" if state_span.value == "on" else "turn_off"
                combined.append(
                    Span(
                        state_span.start,
                        state_span.end,
                        "action",
                        action_key,
                        state_span.text,
                        source="contextual_action",
                        meta={"action": action_key},
                    )
                )
            has_duration = any(span.tag.startswith("duration_") for span in derived)
            if has_duration:
                for marker in combined:
                    if marker.tag == "slot_marker" and marker.value == "timer":
                        combined.append(
                            Span(
                                marker.start,
                                marker.end,
                                "action",
                                "timer_start",
                                marker.text,
                                source="contextual_action",
                                meta={"action": "timer_start"},
                            )
                        )
            if not has_duration:
                property_spans = [
                    span
                    for span in combined
                    if span.tag in {"percent_value", "temperature_value", "color"}
                ]
                property_targets = [
                    span
                    for span in combined
                    if span.tag in {"name", "area", "floor", "domain"}
                    or (span.tag == "cue" and span.value == "context_here")
                ]
                recognized_indexes: set[int] = set()
                for span in combined:
                    recognized_indexes.update(
                        span.meta.get("consumed_indexes") or range(span.start, span.end)
                    )
                unmatched_words = [
                    token
                    for token in tokens
                    if token.index not in recognized_indexes and token.text != "turn"
                ]
                if property_spans and property_targets and not unmatched_words:
                    turn_token = next(
                        (token for token in tokens if token.text == "turn"),
                        None,
                    )
                    if turn_token is not None:
                        anchor_start = turn_token.index
                        anchor_end = turn_token.index + 1
                        anchor_text = turn_token.raw
                    else:
                        property_anchor = property_spans[0]
                        anchor_start = property_anchor.start
                        anchor_end = property_anchor.end
                        anchor_text = property_anchor.text
                    combined.append(
                        Span(
                            anchor_start,
                            anchor_end,
                            "action",
                            "set",
                            anchor_text,
                            source="contextual_action",
                            meta={"action": "set"},
                        )
                    )
        return self._dedupe(combined)
