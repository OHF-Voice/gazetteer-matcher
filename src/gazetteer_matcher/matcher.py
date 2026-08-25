from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import product
from typing import Any, Iterable, Sequence

from .config import ConfigSource, MatcherConfig, load_home, normalize_tokens
from .models import (
    FrameCandidate,
    Interpretation,
    SegmentDebug,
    SlotOption,
    Span,
    TargetReference,
    TargetScope,
    Token,
)
from .response_keys import ResponseKeys, load_response_keys
from .responses import RejectionResponder
from .schemas import IntentCatalog, IntentCombination
from .tagger import SpanTagger


@dataclass(frozen=True)
class _ResolvedContext:
    area: str | None = None
    floor: str | None = None


_DIRECT_SLOT_TAGS = {
    "name": "name",
    "area": "area",
    "floor": "floor",
    "domain": "domain",
    "device_class": "device_class",
    "state": "state",
    "color": "color",
    "media_class": "media_class",
}

_PERCENT_SLOTS = {"position", "brightness", "volume_level", "percentage"}
_DURATION_SLOT_TO_TAG = {
    "hours": "duration_hours",
    "minutes": "duration_minutes",
    "seconds": "duration_seconds",
    "start_hours": "duration_hours",
    "start_minutes": "duration_minutes",
    "start_seconds": "duration_seconds",
}
_SHAREABLE_TAGS = {
    "name",
    "area",
    "floor",
    "domain",
    "device_class",
    "state",
    "color",
    "media_class",
    "percent_value",
    "temperature_value",
    "duration_hours",
    "duration_minutes",
    "duration_seconds",
}


_DISPLAY_COLLECTIONS = {"name": "entities", "area": "areas", "floor": "floors"}
"""Slots holding a home id, and the collection to read the spoken name from."""


class GazetteerMatcher:
    """MVP span tagger + constraint matcher for Home Assistant intents."""

    def __init__(
        self,
        *,
        vocabulary: ConfigSource | None = None,
        home: ConfigSource | None = None,
        intents: dict[str, Any] | None = None,
        responses: ConfigSource | None = None,
        response_keys: ResponseKeys | None = None,
        vocabulary_path: ConfigSource | None = None,
        home_path: ConfigSource | None = None,
        responses_path: ConfigSource | None = None,
    ) -> None:
        self.config = MatcherConfig.load(
            vocabulary=vocabulary,
            home=home,
            intents=intents,
            responses=responses,
            vocabulary_path=vocabulary_path,
            home_path=home_path,
            responses_path=responses_path,
        )
        self.catalog = IntentCatalog(self.config.intents)
        self.global_scope_domains: dict[str, set[str]] = {}
        for combo in self.catalog.all:
            if combo.context_area is False:
                self.global_scope_domains.setdefault(combo.intent, set()).update(
                    combo.inferred_domains
                )
        self._build_home()
        self.actions: dict[str, dict[str, Any]] = (
            self.config.vocabulary.get("actions") or {}
        )
        anaphora = self.config.vocabulary.get("anaphora") or {}
        self.anaphora_actions = set(anaphora.get("actions") or [])
        self.combo_cues: dict[str, dict[str, Any]] = (
            self.config.vocabulary.get("combination_cues") or {}
        )
        self.response_hints: dict[str, dict[str, Any]] = (
            self.config.vocabulary.get("response_hints") or {}
        )
        self.response_keys = (
            response_keys
            if response_keys is not None
            else load_response_keys(str(self.config.vocabulary["language"]))
        )
        acceptance = self.config.vocabulary.get("acceptance") or {}
        legacy_max_unexplained = acceptance.get("max_unexplained_content_tokens")
        self.max_unexplained_important = int(
            acceptance.get(
                "max_unexplained_important_tokens",
                legacy_max_unexplained if legacy_max_unexplained is not None else 1,
            )
        )
        self.max_unexplained_unimportant = int(
            acceptance.get(
                "max_unexplained_unimportant_tokens",
                legacy_max_unexplained if legacy_max_unexplained is not None else 2,
            )
        )

    def set_home(self, home: ConfigSource) -> None:
        """Replace the gazetteer of areas, floors and entities to resolve against.

        Only the span tagger and the rejection wording depend on the home, so this
        rebuilds those two and leaves the vocabulary, intent catalog and number words
        alone. An application whose home changes while it runs should call this rather
        than construct a new matcher, which would re-read the data files.

        Interpretations in flight keep the tagger they started with. Callers that
        interpret from several threads should serialize this against them.
        """
        self.config.home = load_home(home)
        self._build_home()

    def _build_home(self) -> None:
        """(Re)build everything that depends on the home."""
        self.tagger = SpanTagger(self.config)
        self.responder = RejectionResponder(self.config.responses, self.config.home)

    def display_name(self, slot: str, value: Any) -> str:
        """Return what a person would call a resolved slot value.

        Frames carry the ids the caller gave in the home -- ``name`` an entity id,
        ``area`` and ``floor`` theirs -- because those are what an application acts
        on. This is the other direction, for saying back what was acted on.
        Anything that is not a home reference is returned unchanged.
        """
        collection = _DISPLAY_COLLECTIONS.get(slot)
        if collection is None:
            return str(value)
        spec = (self.config.home.get(collection) or {}).get(str(value)) or {}
        return str(spec.get("name") or value)

    def _resolve_home_context(
        self,
        context_area: str | None,
        context_floor: str | None,
    ) -> _ResolvedContext:
        area = self._resolve_home_reference("area", context_area)
        floor = self._resolve_home_reference("floor", context_floor)
        if area is not None:
            area_spec = (self.config.home.get("areas") or {}).get(area) or {}
            area_floor = area_spec.get("floor")
            if floor is None and area_floor:
                floor = str(area_floor)
            elif floor is not None and area_floor and floor != str(area_floor):
                raise ValueError(
                    f"context area {area!r} is not on context floor {floor!r}"
                )
        return _ResolvedContext(area=area, floor=floor)

    def _resolve_home_reference(self, kind: str, value: str | None) -> str | None:
        if value is None:
            return None
        collection_name = "areas" if kind == "area" else "floors"
        collection = self.config.home.get(collection_name) or {}
        raw_value = str(value)
        if raw_value in collection:
            return raw_value
        normalized = " ".join(token.text for token in normalize_tokens(raw_value))
        matches: list[str] = []
        for item_id, spec in collection.items():
            phrases = [spec.get("name"), *(spec.get("aliases") or [])]
            if any(
                phrase
                and " ".join(token.text for token in normalize_tokens(str(phrase)))
                == normalized
                for phrase in phrases
            ):
                matches.append(str(item_id))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"ambiguous context {kind} {value!r}: {matches}")
        raise ValueError(f"unknown context {kind} {value!r}")

    @staticmethod
    def _name_context_rank(span: Span, context: _ResolvedContext) -> int:
        entity_area = span.meta.get("area")
        entity_floor = span.meta.get("floor")
        if context.area is not None:
            if entity_area == context.area:
                return 0
            if context.floor is not None and entity_floor == context.floor:
                return 1
            return 2
        if context.floor is not None:
            return 0 if entity_floor == context.floor else 1
        return 0

    def _duplicate_name_context_rank(
        self,
        span: Span,
        name_spans: Iterable[Span],
        context: _ResolvedContext,
    ) -> int:
        indexes = self._span_indexes(span)
        has_duplicate = any(
            other.value != span.value and self._span_indexes(other) == indexes
            for other in name_spans
        )
        if not has_duplicate:
            return 0
        return self._name_context_rank(span, context)

    def _lexical_duplicate_name_context(
        self,
        name_span: Span,
        local_spans: list[Span],
    ) -> tuple[_ResolvedContext | None, tuple[Span, ...]]:
        """Resolve duplicate name text with an explicit area or floor phrase.

        Some upstream name-only combinations cannot carry a location slot.  A
        phrase such as ``bedroom TV`` must still be able to select one of two
        entities named ``TV`` without adding ``area`` to the resulting frame.
        Only non-overlapping, unambiguous location spans qualify the name.
        """
        name_indexes = self._span_indexes(name_span)
        duplicate = any(
            span.tag == "name"
            and span.value != name_span.value
            and self._span_indexes(span) == name_indexes
            for span in local_spans
        )
        if not duplicate:
            return None, ()

        qualifiers: dict[str, Span] = {}
        for tag in ("area", "floor"):
            candidates = [
                span
                for span in local_spans
                if span.tag == tag and not (self._span_indexes(span) & name_indexes)
            ]
            exact = [span for span in candidates if not span.source.startswith("fuzzy")]
            candidates = exact or candidates
            values = {str(span.value) for span in candidates}
            if len(values) == 1:
                qualifiers[tag] = min(candidates, key=self._span_preference)

        area_span = qualifiers.get("area")
        floor_span = qualifiers.get("floor")
        if area_span is None and floor_span is None:
            return None, ()

        area = str(area_span.value) if area_span is not None else None
        floor = str(floor_span.value) if floor_span is not None else None
        if area is not None:
            area_spec = (self.config.home.get("areas") or {}).get(area) or {}
            area_floor = area_spec.get("floor")
            if floor is not None and area_floor and floor != str(area_floor):
                return None, ()
            if floor is None and area_floor:
                floor = str(area_floor)

        return _ResolvedContext(area=area, floor=floor), tuple(qualifiers.values())

    @staticmethod
    def _span_indexes(span: Span) -> set[int]:
        explicit = span.meta.get("consumed_indexes")
        if explicit is not None:
            return set(int(index) for index in explicit)
        return set(range(span.start, span.end))

    @staticmethod
    def _in_segment(span: Span, segment: tuple[int, int]) -> bool:
        start, end = segment
        indexes = GazetteerMatcher._span_indexes(span)
        return bool(indexes) and indexes <= set(range(start, end))

    @staticmethod
    def _touches_segment(span: Span, segment: tuple[int, int]) -> bool:
        start, end = segment
        return bool(GazetteerMatcher._span_indexes(span) & set(range(start, end)))

    def _select_conjunctions(self, spans: list[Span]) -> list[Span]:
        numeric_phrases = [
            span
            for span in spans
            if span.tag == "number" or span.tag.startswith("duration_")
        ]
        candidates = [span for span in spans if span.tag == "conjunction"]
        # A configured number joiner such as "and" must not split the utterance
        # if it lies inside a recognized number span.
        candidates = [
            span
            for span in candidates
            if not any(
                self._span_indexes(span) <= self._span_indexes(numeric)
                for numeric in numeric_phrases
            )
        ]
        candidates.sort(key=lambda span: (span.start, -span.length))
        chosen: list[Span] = []
        occupied: set[int] = set()
        for span in candidates:
            indexes = self._span_indexes(span)
            if indexes & occupied:
                continue
            chosen.append(span)
            occupied.update(indexes)
        return sorted(chosen, key=lambda span: span.start)

    @staticmethod
    def _segments(token_count: int, conjunctions: list[Span]) -> list[tuple[int, int]]:
        if not conjunctions:
            return [(0, token_count)]
        result: list[tuple[int, int]] = []
        cursor = 0
        for conjunction in conjunctions:
            if cursor < conjunction.start:
                result.append((cursor, conjunction.start))
            cursor = conjunction.end
        if cursor < token_count:
            result.append((cursor, token_count))
        return result or [(0, token_count)]

    def _segment_actions(
        self,
        spans: list[Span],
        segment: tuple[int, int],
        previous_actions: list[Span],
    ) -> tuple[list[Span], bool]:
        local = [
            span
            for span in spans
            if span.tag == "action" and self._touches_segment(span, segment)
        ]
        if local:
            return self._prefer_action_spans(local), False
        if previous_actions:
            # Keep overlapping lexical interpretations at the nearest previous
            # action site; intent metadata will eliminate many of them.
            nearest_start = max(span.start for span in previous_actions)
            inherited = [
                span for span in previous_actions if span.start == nearest_start
            ]
            return self._prefer_action_spans(inherited), True
        return [], False

    @staticmethod
    def _prefer_action_spans(spans: list[Span]) -> list[Span]:
        unique: dict[tuple[str, int, int, str], Span] = {}
        for span in spans:
            key = (str(span.value), span.start, span.end, span.source)
            old = unique.get(key)
            if old is None or span.similarity > old.similarity:
                unique[key] = span
        return sorted(
            unique.values(),
            key=lambda span: (
                span.source.startswith("fuzzy"),
                -len(GazetteerMatcher._span_indexes(span)),
                -span.similarity,
            ),
        )

    def _unique_shareable_spans(self, spans: list[Span]) -> dict[str, Span]:
        by_tag_value: dict[str, dict[str, Span]] = {}
        for span in spans:
            if span.tag not in _SHAREABLE_TAGS:
                continue
            by_value = by_tag_value.setdefault(span.tag, {})
            key = repr(span.value)
            previous = by_value.get(key)
            if previous is None or self._span_preference(span) < self._span_preference(
                previous
            ):
                by_value[key] = span
        result: dict[str, Span] = {}
        for tag, values in by_tag_value.items():
            if len(values) == 1:
                result[tag] = next(iter(values.values()))
        return result

    @staticmethod
    def _span_preference(span: Span) -> tuple[Any, ...]:
        return (
            span.source.startswith("fuzzy"),
            -span.similarity,
            -span.length,
        )

    def _visible_spans_for_segment(
        self,
        spans: list[Span],
        segment: tuple[int, int],
        shared: dict[str, Span],
    ) -> list[Span]:
        local = [span for span in spans if self._in_segment(span, segment)]
        result = list(local)
        local_tags = {span.tag for span in local}
        for tag, span in shared.items():
            if tag in local_tags:
                continue
            # Preserve the original span location; SlotOption.inherited records
            # that this is ellipsis/coordination inheritance.
            result.append(span)
        return result

    @staticmethod
    def _best_spans(spans: Iterable[Span], *, limit: int = 4) -> list[Span]:
        unique: dict[str, Span] = {}
        for span in spans:
            key = repr(span.value)
            old = unique.get(key)
            if old is None or GazetteerMatcher._span_preference(
                span
            ) < GazetteerMatcher._span_preference(old):
                unique[key] = span
        return sorted(unique.values(), key=GazetteerMatcher._span_preference)[:limit]

    def _direct_options(
        self,
        slot: str,
        visible_spans: list[Span],
        segment: tuple[int, int],
        context: _ResolvedContext,
    ) -> list[SlotOption]:
        tag = _DIRECT_SLOT_TAGS[slot]
        matching = [span for span in visible_spans if span.tag == tag]
        if slot not in {"area", "floor"}:
            limit = max(4, len(matching)) if slot == "name" else 4
            spans = self._best_spans(matching, limit=limit)
            if slot == "name":
                spans.sort(
                    key=lambda span: (
                        self._duplicate_name_context_rank(span, matching, context),
                        self._span_preference(span),
                    )
                )
                spans = spans[:4]
            return [
                SlotOption(
                    slot=slot,
                    value=span.value,
                    spans=(span,),
                    source=span.source,
                    similarity=span.similarity,
                    inherited=not self._in_segment(span, segment),
                )
                for span in spans
            ]
        exact_values = {
            repr(span.value) for span in matching if not span.source.startswith("fuzzy")
        }
        unique: dict[tuple[int, int, str], Span] = {}
        for span in matching:
            if span.source.startswith("fuzzy") and repr(span.value) in exact_values:
                continue
            key = (span.start, span.end, repr(span.value))
            old = unique.get(key)
            if old is None or self._span_preference(span) < self._span_preference(old):
                unique[key] = span
        spans = sorted(unique.values(), key=self._span_preference)[:8]
        return [
            SlotOption(
                slot=slot,
                value=span.value,
                spans=(span,),
                source=span.source,
                similarity=span.similarity,
                inherited=not self._in_segment(span, segment),
            )
            for span in spans
        ]

    def _numeric_options(
        self,
        slot: str,
        combo: IntentCombination,
        visible_spans: list[Span],
        segment: tuple[int, int],
        action_spec: dict[str, Any],
    ) -> list[SlotOption]:
        if slot in _PERCENT_SLOTS:
            tag = "percent_value"
        elif slot == "temperature":
            tag = "temperature_value"
        elif slot in _DURATION_SLOT_TO_TAG:
            tag = _DURATION_SLOT_TO_TAG[slot]
        elif slot == "volume_step":
            tag = "percent_value"
        else:
            return []

        numeric_spans: dict[tuple[int, int, str], Span] = {}
        for candidate in visible_spans:
            if candidate.tag != tag and not (
                tag in {"percent_value", "temperature_value"}
                and candidate.tag == "number"
                and candidate.meta.get("kind") == "cardinal"
            ):
                continue
            key = (candidate.start, candidate.end, repr(candidate.value))
            old = numeric_spans.get(key)
            if old is None or self._span_preference(candidate) < self._span_preference(
                old
            ):
                numeric_spans[key] = candidate

        result: list[SlotOption] = []
        for span in sorted(numeric_spans.values(), key=self._span_preference)[:8]:
            if not self._numeric_role_allowed(slot, combo, span, visible_spans):
                continue
            value = span.value
            if slot == "volume_step":
                sign = action_spec.get("numeric_sign")
                if sign is not None:
                    value = int(sign) * value
            result.append(
                SlotOption(
                    slot=slot,
                    value=value,
                    spans=(span,),
                    source=span.source,
                    similarity=span.similarity,
                    inherited=not self._in_segment(span, segment),
                )
            )
        return result

    def _numeric_role_allowed(
        self,
        slot: str,
        combo: IntentCombination,
        span: Span,
        visible_spans: list[Span],
    ) -> bool:
        """Use prepositions to separate timer base duration from adjustment."""
        if slot not in _DURATION_SLOT_TO_TAG:
            return True
        combo_slots = set(combo.slots)
        has_start = any(name.startswith("start_") for name in combo_slots)
        has_adjustment = any(
            name in {"hours", "minutes", "seconds"} for name in combo_slots
        )
        if not (has_start and has_adjustment):
            return True

        relations = sorted(
            (item for item in visible_spans if item.tag == "relation"),
            key=lambda item: item.start,
        )
        preceding = next(
            (item for item in reversed(relations) if item.end <= span.start),
            None,
        )
        following = next(
            (item for item in relations if item.start >= span.end),
            None,
        )
        previous_relation = preceding.value if preceding is not None else None
        next_relation = following.value if following is not None else None
        is_start = slot.startswith("start_")

        if previous_relation == "by":
            return not is_start
        if previous_relation in {"for", "to", "from"}:
            return is_start
        if next_relation == "by":
            return is_start
        if next_relation in {"to", "from"}:
            return not is_start
        return True

    def _inferred_domain_options(
        self,
        combo: IntentCombination,
        visible_spans: list[Span],
        segment: tuple[int, int],
        action_spec: dict[str, Any],
    ) -> list[SlotOption]:
        if not combo.inferred_domains:
            return []
        result: list[SlotOption] = []
        allowed_by_action = set(action_spec.get("allowed_domains") or [])

        # Device classes carry their possible domains in vocabulary.yaml.
        for span in visible_spans:
            if span.tag != "device_class":
                continue
            domains = set(span.meta.get("domains") or []) & set(combo.inferred_domains)
            if allowed_by_action:
                domains &= allowed_by_action
            for domain in domains:
                result.append(
                    SlotOption(
                        slot="domain",
                        value=domain,
                        spans=(span,),
                        source="inferred_device_class",
                        similarity=span.similarity,
                        inherited=not self._in_segment(span, segment),
                        allow_overlap=True,
                    )
                )

        # "doors locked" conventionally refers to lock entities, whereas
        # open/closed door queries refer to covers or binary sensors.
        state_values = {span.value for span in visible_spans if span.tag == "state"}
        door_spans = [
            span
            for span in visible_spans
            if span.tag == "device_class" and span.value == "door"
        ]
        if "lock" in combo.inferred_domains and state_values & {"locked", "unlocked"}:
            for door_span in door_spans:
                result.append(
                    SlotOption(
                        slot="domain",
                        value="lock",
                        spans=(door_span,),
                        source="inferred_lock_state",
                        similarity=door_span.similarity,
                        inherited=not self._in_segment(door_span, segment),
                        allow_overlap=True,
                    )
                )

        # A virtual action such as OPEN can narrow an inferred-domain combo.
        if allowed_by_action:
            for domain in set(combo.inferred_domains) & allowed_by_action:
                result.append(
                    SlotOption(
                        slot="domain",
                        value=domain,
                        spans=(),
                        source="inferred_action_domain",
                        inherited=False,
                        allow_overlap=True,
                    )
                )

        # Property slots are diagnostic enough to permit a single-domain
        # inference from intent metadata (e.g. brightness => light).
        diagnostic_slots = {"brightness", "color", "temperature", "position"}
        diagnostic_tags = {
            "percent_value",
            "temperature_value",
            "color",
            "device_class",
        }
        has_diagnostic = bool(diagnostic_slots & set(combo.slots)) and any(
            span.tag in diagnostic_tags for span in visible_spans
        )
        if len(combo.inferred_domains) == 1 and has_diagnostic:
            domain = next(iter(combo.inferred_domains))
            result.append(
                SlotOption(
                    slot="domain",
                    value=domain,
                    spans=(),
                    source="inferred_intent_schema",
                    allow_overlap=True,
                )
            )

        # Dedupe by value/source.
        unique: dict[tuple[str, str], SlotOption] = {}
        for option in result:
            unique[(str(option.value), option.source)] = option
        return list(unique.values())

    def _residual_option(
        self,
        slot: str,
        tokens: list[Token],
        spans: list[Span],
        action_span: Span,
        segment: tuple[int, int],
    ) -> SlotOption | None:
        if slot not in {"message", "search_query", "conversation_command"}:
            return None
        start, end = segment
        action_indexes = self._span_indexes(action_span)
        action_end = (
            max(
                (index for index in action_indexes if start <= index < end),
                default=start - 1,
            )
            + 1
        )
        indexes = list(range(max(start, action_end), end))
        if not indexes:
            return None

        remove: set[int] = set()
        local = [span for span in spans if self._in_segment(span, segment)]
        if slot == "message":
            # Strip only leading grammatical wrappers; preserve message wording.
            cursor = indexes[0]
            changed = True
            while changed:
                changed = False
                for span in local:
                    if span.tag == "skip" and span.start == cursor:
                        remove.update(self._span_indexes(span))
                        cursor = span.end
                        changed = True
                        break
        elif slot == "search_query":
            removable_tags = {"skip", "area", "floor", "name", "media_class", "domain"}
            for span in local:
                if span.tag in removable_tags:
                    remove.update(self._span_indexes(span))
        else:  # conversation_command
            removable_tags = {
                "skip",
                "duration_hours",
                "duration_minutes",
                "duration_seconds",
                "number",
                "unit",
                "slot_marker",
            }
            for span in local:
                if span.tag in removable_tags:
                    remove.update(self._span_indexes(span))

        remaining = [index for index in indexes if index not in remove]
        if not remaining:
            return None
        text = " ".join(tokens[index].raw for index in remaining)
        span = Span(
            start=min(remaining),
            end=max(remaining) + 1,
            tag=slot,
            value=text,
            text=text,
            source="residual",
            meta={"consumed_indexes": remaining},
        )
        return SlotOption(slot=slot, value=text, spans=(span,), source="residual")

    def _slot_options(
        self,
        slot: str,
        combo: IntentCombination,
        tokens: list[Token],
        visible_spans: list[Span],
        local_spans: list[Span],
        action_span: Span,
        action_spec: dict[str, Any],
        segment: tuple[int, int],
        context: _ResolvedContext,
    ) -> list[SlotOption]:
        fixed = action_spec.get("fixed_slots") or {}
        # Relative volume actions may carry either a lexical direction (up/down)
        # or an explicit signed percentage. Prefer the explicit numeric value.
        if slot == "volume_step":
            numeric = self._numeric_options(
                slot, combo, visible_spans, segment, action_spec
            )
            if numeric:
                return numeric
        if slot in fixed:
            return [SlotOption(slot=slot, value=fixed[slot], source="fixed_action")]

        if slot in _DIRECT_SLOT_TAGS:
            options = self._direct_options(slot, visible_spans, segment, context)
            if slot == "domain":
                options.extend(
                    self._inferred_domain_options(
                        combo, visible_spans, segment, action_spec
                    )
                )
            return options

        numeric = self._numeric_options(
            slot, combo, visible_spans, segment, action_spec
        )
        if numeric:
            return numeric

        # residual = self._residual_option(slot, tokens, local_spans, action_span, segment)
        # if residual is not None:
        #     return [residual]

        return []

    def _anaphoric_selection(
        self,
        combo: IntentCombination,
        target: TargetReference,
        anaphor_span: Span,
    ) -> dict[str, SlotOption] | None:
        """Create one atomic slot bundle from a supplied antecedent.

        Requiring the formal combination slots to exactly match the target
        selector prevents inherited fields from being mixed with lexical or
        current-location fields.
        """
        target_slots = set(target.slots)
        if set(combo.slots) != target_slots:
            return None
        if target.scope == "home" and combo.context_area is not False:
            return None
        if target.scope == "entity" and "name" not in combo.slots:
            return None
        if target.scope == "area" and "area" not in combo.slots:
            return None
        if target.scope == "floor" and "floor" not in combo.slots:
            return None

        span = anaphor_span
        if "name" in target.slots:
            entity_span = self._entity_reference_span(
                anaphor_span, str(target.slots["name"])
            )
            if entity_span is None:
                return None
            span = entity_span

        return {
            slot: SlotOption(
                slot=slot,
                value=target.slots[slot],
                spans=(span,),
                source="anaphora",
                inherited=True,
                allow_overlap=True,
            )
            for slot in combo.slots
        }

    def _entity_reference_span(self, span: Span, entity_id: str) -> Span | None:
        """Attach an inherited entity's home metadata to reference wording."""
        entity = (self.config.home.get("entities") or {}).get(entity_id)
        if entity is None:
            return None
        entity_area = entity.get("area")
        area = (self.config.home.get("areas") or {}).get(entity_area) or {}
        return replace(
            span,
            meta={
                **span.meta,
                "entity_id": entity_id,
                "name": entity.get("name"),
                "domain": entity.get("domain") or entity_id.split(".", 1)[0],
                "device_class": entity.get("device_class"),
                "area": entity_area,
                "floor": entity.get("floor") or area.get("floor"),
            },
        )

    def _coordination_slot_option(
        self,
        slot: str,
        value: Any,
        reference_span: Span,
    ) -> SlotOption | None:
        """Materialize one slot selected by a coordinated possessive."""
        if slot == "name":
            enriched = self._entity_reference_span(reference_span, str(value))
            if enriched is None:
                return None
            reference_span = enriched
        return SlotOption(
            slot=slot,
            value=value,
            spans=(reference_span,),
            source="coordination_reference",
            inherited=True,
            allow_overlap=True,
        )

    @staticmethod
    def _options_overlap(options: Iterable[SlotOption]) -> bool:
        occupied: list[tuple[set[int], bool, str]] = []
        for option in options:
            indexes: set[int] = set()
            for span in option.spans:
                indexes.update(GazetteerMatcher._span_indexes(span))
            if not indexes:
                continue
            for old_indexes, old_allow, old_slot in occupied:
                if indexes & old_indexes and not (option.allow_overlap or old_allow):
                    # The same physical evidence cannot independently fill two
                    # concrete slots (e.g. AREA inside an ENTITY name).
                    if old_slot != option.slot:
                        return True
            occupied.append((indexes, option.allow_overlap, option.slot))
        return False

    def _candidate_violations(
        self,
        intent: str,
        combo: IntentCombination,
        selected: dict[str, SlotOption],
        action_spec: dict[str, Any],
        local_spans: list[Span],
    ) -> list[str]:
        violations: list[str] = []
        if self._options_overlap(selected.values()):
            violations.append("slot evidence overlaps incompatibly")

        marker_values = {
            span.value for span in local_spans if span.tag == "slot_marker"
        }

        # Home Assistant percentage slots are integer percentages. Reject bad
        # structured values even when a competing frame would otherwise ignore
        # them as unexplained text.
        for span in local_spans:
            if span.tag == "percent_value" and (
                not isinstance(span.value, (int, float))
                or not float(span.value).is_integer()
                or not 0 <= span.value <= 100
            ):
                violations.append("percentage must be an integer from 0 to 100")
            if span.tag.startswith("duration_") and (
                not isinstance(span.value, (int, float))
                or not float(span.value).is_integer()
                or span.value < 0
            ):
                violations.append("timer duration must be a non-negative integer")

        temperature = selected.get("temperature")
        if temperature is not None and intent == "HassLightSet":
            value = temperature.value
            if (
                not isinstance(value, (int, float))
                or not float(value).is_integer()
                or value < 0
            ):
                violations.append(
                    "light color temperature must be a non-negative integer"
                )

        duration_values = {
            slot: option.value
            for slot, option in selected.items()
            if slot in _DURATION_SLOT_TO_TAG
        }
        if intent == "HassStartTimer" and duration_values:
            total_seconds = sum(
                duration_values.get(slot, 0) * multiplier
                for slot, multiplier in (
                    ("hours", 3600),
                    ("minutes", 60),
                    ("seconds", 1),
                )
            )
            if total_seconds <= 0:
                violations.append("timer duration must be greater than zero")
        elif intent in {"HassIncreaseTimer", "HassDecreaseTimer"}:
            adjustment_seconds = sum(
                duration_values.get(slot, 0) * multiplier
                for slot, multiplier in (
                    ("hours", 3600),
                    ("minutes", 60),
                    ("seconds", 1),
                )
            )
            if duration_values and adjustment_seconds <= 0:
                violations.append("timer adjustment must be greater than zero")

        for required_slot in action_spec.get("require_slots") or []:
            if required_slot not in selected:
                violations.append(f"missing required slot {required_slot!r}")
        for required_marker in action_spec.get("require_markers") or []:
            if required_marker not in marker_values:
                violations.append(f"missing required marker {required_marker!r}")

        relation_values = {span.value for span in local_spans if span.tag == "relation"}
        if intent == "HassSetVolume" and "by" in relation_values:
            violations.append("absolute volume is incompatible with 'by'")
        if intent == "HassSetVolume" and action_spec.get("directional_volume"):
            if "to" not in relation_values:
                violations.append("directional absolute volume requires 'to'")
        if intent == "HassSetVolumeRelative" and "to" in relation_values:
            violations.append("relative volume is incompatible with 'to'")
        if intent == "HassSetPosition" and action_spec.get("directional_volume"):
            if "to" not in relation_values or "position" not in marker_values:
                violations.append("directional position requires marker and 'to'")

        # An increase/decrease phrase that names both a timer's original
        # duration and its adjustment must select a start_* combination. This
        # prevents the two numeric phrases being flattened into two deltas.
        if intent in {"HassIncreaseTimer", "HassDecreaseTimer"} and not any(
            slot.startswith("start_") for slot in selected
        ):
            durations = sorted(
                (span for span in local_spans if span.tag.startswith("duration_")),
                key=lambda span: span.start,
            )
            if len({(span.start, span.end) for span in durations}) >= 2:
                first_start = min(span.start for span in durations)
                last_end = max(span.end for span in durations)
                relations = {
                    span.value
                    for span in local_spans
                    if span.tag == "relation" and first_start < span.start < last_end
                }
                timer_between = any(
                    span.tag == "slot_marker"
                    and span.value == "timer"
                    and first_start < span.start < last_end
                    for span in local_spans
                )
                if relations & {"to", "from"} or timer_between:
                    violations.append("timer base duration requires start_* slot")
                elif {"for", "by"} <= {
                    span.value for span in local_spans if span.tag == "relation"
                }:
                    violations.append("timer base duration requires start_* slot")

        name_option = selected.get("name")
        name_span = name_option.spans[0] if name_option and name_option.spans else None
        entity_domain = name_span.meta.get("domain") if name_span else None
        if (
            name_option
            and combo.name_domains
            and entity_domain not in combo.name_domains
        ):
            violations.append(
                f"entity domain {entity_domain!r} not allowed by name_domains"
            )

        cue_values = {span.value for span in local_spans if span.tag == "cue"}
        explicit_light = any(
            span.tag == "domain" and span.value == "light" for span in local_spans
        )
        local_light_name = any(
            span.tag == "name" and span.meta.get("domain") == "light"
            for span in local_spans
        )
        if intent == "HassLightSet" and "temperature" in selected:
            if (
                "color_temperature" not in cue_values
                and not explicit_light
                and entity_domain != "light"
            ):
                violations.append("light temperature lacks light-specific evidence")
        if intent == "HassClimateSetTemperature" and (
            explicit_light or local_light_name
        ):
            violations.append("climate temperature conflicts with light evidence")
        if (
            intent == "HassGetState"
            and action_spec.get("temperature_query")
            and entity_domain == "climate"
        ):
            violations.append("climate entity temperature uses climate query intent")

        domain_option = selected.get("domain")
        domain = domain_option.value if domain_option else entity_domain
        if (
            domain_option
            and combo.inferred_domains
            and domain not in combo.inferred_domains
        ):
            violations.append(f"domain {domain!r} not allowed by inferred_domains")

        has_anaphoric_target = any(
            option.source == "anaphora" for option in selected.values()
        )

        # Quantity and geographic scope are independent for device controls.
        # Keep this interpretation local to turn-on/off intents: the same
        # words can be ordinary slot values elsewhere (for example, "is Jane
        # in the home" uses HOME as a person state rather than device scope).
        if intent in {"HassTurnOn", "HassTurnOff"}:
            has_quantifier_all = "quantifier_all" in cue_values
            has_scope_home = "scope_home" in cue_values
            has_context_here = "context_here" in cue_values
            has_location_evidence = any(
                span.tag in {"area", "floor"} for span in local_spans
            )
            has_name_evidence = any(span.tag == "name" for span in local_spans)
            is_global_scope = combo.context_area is False

            if has_scope_home and (
                has_location_evidence or has_name_evidence or has_context_here
            ):
                violations.append("home-wide scope conflicts with a local target")
            elif is_global_scope:
                if has_location_evidence or has_name_evidence or has_context_here:
                    violations.append("global scope conflicts with a local target")
                if not (has_scope_home or has_quantifier_all or has_anaphoric_target):
                    violations.append("global scope requires an all/home-wide cue")
            elif has_scope_home:
                violations.append("home-wide cue requires a global combination")

            # With no narrower scope, an all-quantifier prefers a global
            # combination when this intent/domain actually supports one. If it
            # does not (for example fans), the context-area combination remains
            # valid.
            if (
                combo.context_area is True
                and has_quantifier_all
                and not has_context_here
                and not has_location_evidence
                and not has_name_evidence
                and domain is not None
            ):
                if domain in self.global_scope_domains.get(intent, set()):
                    violations.append(
                        "unscoped all-quantifier prefers supported global scope"
                    )

        state_option = selected.get("state")
        if (
            state_option
            and state_option.value in {"locked", "unlocked"}
            and domain is not None
            and domain != "lock"
        ):
            violations.append("lock state requires lock domain")

        action_domains = set(action_spec.get("allowed_domains") or [])
        local_target_domains = {
            str(span.meta.get("domain"))
            for span in local_spans
            if span.tag == "name" and span.meta.get("domain")
        }
        local_target_domains.update(
            str(span.value) for span in local_spans if span.tag == "domain"
        )
        if (
            action_domains
            and local_target_domains
            and local_target_domains.isdisjoint(action_domains)
        ):
            violations.append("explicit target incompatible with virtual action")
        if action_domains and domain is not None and domain not in action_domains:
            violations.append(f"domain {domain!r} incompatible with virtual action")
        if action_domains and domain is None and name_option:
            violations.append(
                "virtual action requires a known compatible entity domain"
            )

        device_option = selected.get("device_class")
        if device_option and device_option.spans and domain_option:
            possible = set(device_option.spans[0].meta.get("domains") or [])
            if possible and domain_option.value not in possible:
                violations.append("device_class/domain mismatch")

        # Name + area/floor combinations must identify an entity that actually
        # belongs to the explicit location.
        if name_span is not None:
            area = selected.get("area")
            if (
                area
                and name_span.meta.get("area")
                and area.value != name_span.meta.get("area")
            ):
                violations.append("entity is not in the selected area")
            floor = selected.get("floor")
            if (
                floor
                and name_span.meta.get("floor")
                and floor.value != name_span.meta.get("floor")
            ):
                violations.append("entity is not on the selected floor")

        cue_cfg = self.combo_cues.get(f"{intent}.{combo.name}", {})
        for required in cue_cfg.get("require_cues") or []:
            if required not in cue_values:
                violations.append(f"missing required cue {required!r}")
        for forbidden in cue_cfg.get("forbid_cues") or []:
            if forbidden in cue_values:
                violations.append(f"forbidden cue {forbidden!r}")
        return violations

    def _consumed_indexes(
        self,
        intent: str,
        combo: IntentCombination,
        selected: dict[str, SlotOption],
        action_span: Span,
        local_spans: list[Span],
        segment: tuple[int, int],
    ) -> set[int]:
        start, end = segment
        local_range = set(range(start, end))
        consumed = self._span_indexes(action_span) & local_range
        for option in selected.values():
            for span in option.spans:
                consumed.update(self._span_indexes(span) & local_range)

        # Grammatical scaffolding is always ignorable. Cues are consumed only
        # when the chosen combination actually licenses them.
        for span in local_spans:
            if span.tag in {"skip", "coordination_reference"}:
                consumed.update(self._span_indexes(span))

        for span in local_spans:
            if span.tag == "response_key" and span.meta.get("intent") == intent:
                consumed.update(self._span_indexes(span))

        cue_cfg = self.combo_cues.get(f"{intent}.{combo.name}", {})
        allowed_cues = set(cue_cfg.get("require_cues") or [])
        allowed_cues.add("quantifier_all")
        if combo.context_area is False:
            allowed_cues.add("scope_home")
        if combo.context_area:
            allowed_cues.add("context_here")
        for span in local_spans:
            if span.tag == "cue" and span.value in allowed_cues:
                consumed.update(self._span_indexes(span))

        if any(option.source == "anaphora" for option in selected.values()):
            for span in local_spans:
                if span.tag == "anaphora_modifier":
                    consumed.update(self._span_indexes(span))

        # Slot-marker text is syntactic evidence for the slot it names.
        marker_compat = {
            "volume_step": {"volume_level"},
            "floor": {"floor_relation"},
        }
        selected_slots = set(selected)
        for span in local_spans:
            if span.tag != "slot_marker":
                continue
            if span.value in selected_slots or any(
                span.value in marker_compat.get(slot, set()) for slot in selected_slots
            ):
                consumed.update(self._span_indexes(span))

        # Timer is a type marker rather than a formal slot in the timer intent
        # schemas. Once a timer intent is selected it is fully explained.
        if "Timer" in intent:
            for span in local_spans:
                if span.tag == "slot_marker" and span.value == "timer":
                    consumed.update(self._span_indexes(span))

        if intent in {
            "HassMediaNext",
            "HassMediaPrevious",
            "HassMediaPause",
            "HassMediaUnpause",
        }:
            for span in local_spans:
                if span.tag == "media_class":
                    consumed.update(self._span_indexes(span))
        if intent.startswith("HassMedia") and "name" in selected:
            for span in local_spans:
                if span.tag == "relation" and span.value == "on":
                    consumed.update(self._span_indexes(span))

        # Entity gazetteers carry their domain. A matching lexical type next to
        # a selected entity is redundant evidence, not unexplained content.
        name_option = selected.get("name")
        if name_option and name_option.spans:
            entity_domain = name_option.spans[0].meta.get("domain")
            for span in local_spans:
                if span.tag == "domain" and span.value == entity_domain:
                    consumed.update(self._span_indexes(span))

        # The top-level intent domain can consume a lexical domain cue even when
        # that domain is not represented as a formal slot combination.
        intent_domain = self.catalog.intent_domain(intent)
        if intent_domain and intent_domain != "homeassistant":
            for span in local_spans:
                overlaps_specific_target = any(
                    other.tag in {"name", "area", "floor"}
                    and self._span_indexes(other) & self._span_indexes(span)
                    for other in local_spans
                )
                if (
                    span.tag == "domain"
                    and span.value == intent_domain
                    and not overlaps_specific_target
                ):
                    consumed.update(self._span_indexes(span))

        return consumed

    def _frame_from_selection(
        self,
        intent: str,
        combo: IntentCombination,
        selected: dict[str, SlotOption],
        action_key: str,
        action_span: Span,
        action_spec: dict[str, Any],
        tokens: list[Token],
        local_spans: list[Span],
        segment: tuple[int, int],
        inherited_action: bool,
        context: _ResolvedContext,
    ) -> FrameCandidate:
        violations = self._candidate_violations(
            intent, combo, selected, action_spec, local_spans
        )
        consumed = self._consumed_indexes(
            intent, combo, selected, action_span, local_spans, segment
        )
        ranking_context = context
        lexical_qualifiers: tuple[Span, ...] = ()
        selected_name = selected.get("name")
        if selected_name and selected_name.spans:
            lexical_context, lexical_qualifiers = self._lexical_duplicate_name_context(
                selected_name.spans[0], local_spans
            )
            if lexical_context is not None:
                ranking_context = lexical_context
                for qualifier in lexical_qualifiers:
                    consumed.update(self._span_indexes(qualifier))
        start, end = segment
        unexplained = [index for index in range(start, end) if index not in consumed]
        important_indexes: set[int] = set()
        for span in local_spans:
            if span.tag in {"skip", "conjunction"}:
                continue
            important_indexes.update(self._span_indexes(span))
        unexplained_important = [
            index for index in unexplained if index in important_indexes
        ]
        unexplained_unimportant = [
            index for index in unexplained if index not in important_indexes
        ]
        inherited_slots = sum(option.inherited for option in selected.values())
        fuzzy_options = [
            option for option in selected.values() if option.source.startswith("fuzzy")
        ]
        fuzzy_action = int(action_span.source.startswith("fuzzy"))
        fuzzy_count = fuzzy_action + len(fuzzy_options)
        fuzzy_distance = (1.0 - action_span.similarity if fuzzy_action else 0.0) + sum(
            1.0 - option.similarity for option in fuzzy_options
        )
        target_generality = (
            0
            if "name" in selected
            else int(
                any(
                    slot in selected
                    for slot in ("area", "floor", "domain", "device_class")
                )
            )
        )
        context_rank = 0
        if selected_name and selected_name.spans:
            context_rank = self._duplicate_name_context_rank(
                selected_name.spans[0],
                (span for span in local_spans if span.tag == "name"),
                ranking_context,
            )
        cost = (
            len(violations),
            len(unexplained_important),
            len(unexplained_unimportant),
            -len(self._span_indexes(action_span)),
            fuzzy_action,
            len(fuzzy_options),
            inherited_slots + int(inherited_action),
            context_rank,
            target_generality,
            fuzzy_distance,
            -len(consumed),
        )
        slot_values = {slot: option.value for slot, option in selected.items()}
        name_option = selected.get("name")
        if name_option and name_option.spans and "state" in slot_values:
            entity_domain = name_option.spans[0].meta.get("domain")
            normalization = (
                self.config.vocabulary.get("state_normalization") or {}
            ).get(entity_domain) or {}
            slot_values["state"] = normalization.get(
                slot_values["state"], slot_values["state"]
            )

        return FrameCandidate(
            intent=intent,
            combination=combo.name,
            action=action_key,
            slots=slot_values,
            slot_options=selected,
            action_span=action_span,
            segment=segment,
            unexplained_important_tokens=unexplained_important,
            unexplained_unimportant_tokens=unexplained_unimportant,
            inherited_slots=inherited_slots,
            inherited_action=inherited_action,
            fuzzy_count=fuzzy_count,
            fuzzy_distance=fuzzy_distance,
            target_generality=target_generality,
            target_scope=self._selection_target_scope(combo, slot_values),
            violations=violations,
            cost=cost,
            response_key=self._response_key(
                intent, combo.name, local_spans, slot_values
            ),
        )

    def _response_key(
        self,
        intent: str,
        combination: str,
        local_spans: list[Span],
        slots: dict[str, Any],
    ) -> str | None:
        """Return the response key a successful frame should be answered with.

        Lexical hints take precedence over combination defaults because query
        wording such as ``which`` can be meaningful even when a home alias
        makes the selected target more specific than the upstream sentence
        shape normally would be. Both are configured wording, so both come
        before the corpus, which knows the shape but not what was said.
        """
        spec = self.response_hints.get(intent) or {}
        keys = {
            str(span.value)
            for span in local_spans
            if span.tag == "response_key" and span.meta.get("intent") == intent
        }
        if len(keys) == 1:
            return next(iter(keys))
        if len(keys) > 1:
            return None

        default = (spec.get("defaults") or {}).get(combination)
        if default:
            return str(default)

        return self.response_keys.key_for(
            intent, combination, self.target_domain(slots)
        )

    def target_domain(self, slots: dict[str, Any]) -> str | None:
        """Return what a frame's slots act on, or None when that is not one thing.

        A named target's own domain is the more specific answer, so it beats the
        ``domain`` slot; that slot holding several is no answer at all.
        """
        if entity_id := slots.get("name"):
            entity = (self.config.home.get("entities") or {}).get(str(entity_id)) or {}
            if domain := entity.get("domain"):
                return str(domain)
            return str(entity_id).split(".", maxsplit=1)[0]

        domain = slots.get("domain")
        if isinstance(domain, (list, tuple, set)):
            return str(next(iter(domain))) if len(domain) == 1 else None
        return str(domain) if domain else None

    @staticmethod
    def _selection_target_scope(
        combo: IntentCombination,
        slots: dict[str, Any],
    ) -> TargetScope | None:
        if combo.context_area is False:
            return "home"
        if "name" in slots:
            return "entity"
        if "area" in slots:
            return "area"
        if "floor" in slots:
            return "floor"
        return None

    def _generate_frames_for_action(
        self,
        action_span: Span,
        inherited_action: bool,
        tokens: list[Token],
        spans: list[Span],
        visible_spans: list[Span],
        segment: tuple[int, int],
        context: _ResolvedContext,
        anaphor_span: Span | None = None,
        previous_target: TargetReference | None = None,
        coordination_target: dict[str, Any] | None = None,
    ) -> list[FrameCandidate]:
        action_key = str(action_span.value)
        action_spec = self.actions.get(action_key) or {}
        result: list[FrameCandidate] = []
        local_spans = [span for span in spans if self._in_segment(span, segment)]
        coordination_span = next(
            (span for span in local_spans if span.tag == "coordination_reference"),
            None,
        )

        for intent in action_spec.get("intents") or []:
            for combo in self.catalog.combinations(intent):
                if combo.context_area is True and context.area is None:
                    continue
                if (
                    anaphor_span is not None
                    and previous_target is not None
                    and action_key in self.anaphora_actions
                ):
                    inherited = self._anaphoric_selection(
                        combo, previous_target, anaphor_span
                    )
                    if inherited is not None:
                        result.append(
                            self._frame_from_selection(
                                intent,
                                combo,
                                inherited,
                                action_key,
                                action_span,
                                action_spec,
                                tokens,
                                local_spans,
                                segment,
                                inherited_action,
                                context,
                            )
                        )

                option_lists: list[list[SlotOption]] = []
                impossible = False
                for slot in combo.slots:
                    if (
                        coordination_target
                        and coordination_span is not None
                        and slot in coordination_target
                    ):
                        option = self._coordination_slot_option(
                            slot,
                            coordination_target[slot],
                            coordination_span,
                        )
                        options = [option] if option is not None else []
                    else:
                        options = self._slot_options(
                            slot,
                            combo,
                            tokens,
                            visible_spans,
                            local_spans,
                            action_span,
                            action_spec,
                            segment,
                            context,
                        )
                    if not options:
                        impossible = True
                        break
                    option_lists.append(options[:4])
                if impossible:
                    continue

                selections = product(*option_lists) if option_lists else [()]
                for selection in selections:
                    selected = {option.slot: option for option in selection}
                    if (
                        combo.context_area
                        and context.area is not None
                        and "area" not in selected
                    ):
                        selected["area"] = SlotOption(
                            slot="area",
                            value=context.area,
                            source="context_area",
                            inherited=True,
                            allow_overlap=True,
                        )
                    result.append(
                        self._frame_from_selection(
                            intent,
                            combo,
                            selected,
                            action_key,
                            action_span,
                            action_spec,
                            tokens,
                            local_spans,
                            segment,
                            inherited_action,
                            context,
                        )
                    )
        return result

    @staticmethod
    def _dedupe_candidates(candidates: list[FrameCandidate]) -> list[FrameCandidate]:
        best: dict[tuple[Any, ...], FrameCandidate] = {}
        for candidate in candidates:
            key = candidate.semantic_key()
            old = best.get(key)
            if old is None or candidate.cost < old.cost:
                best[key] = candidate
        return sorted(best.values(), key=lambda candidate: candidate.cost)

    def _choose_candidate(
        self, candidates: list[FrameCandidate]
    ) -> tuple[FrameCandidate | None, bool, str | None]:
        candidates = self._dedupe_candidates(candidates)
        viable = [
            candidate
            for candidate in candidates
            if not candidate.violations
            and len(candidate.unexplained_important_tokens)
            <= self.max_unexplained_important
            and len(candidate.unexplained_unimportant_tokens)
            <= self.max_unexplained_unimportant
        ]
        if not viable:
            if not candidates:
                return None, False, "no slot combination could be constructed"
            best = candidates[0]
            if best.violations:
                return None, False, "; ".join(best.violations)
            return None, False, "unexplained content remains"

        best = viable[0]
        tied = [candidate for candidate in viable if candidate.cost == best.cost]
        semantic_keys = {candidate.semantic_key() for candidate in tied}
        if len(semantic_keys) > 1:
            return None, True, "multiple equally good semantic interpretations"
        return best, False, None

    def _rejected_interpretation(
        self,
        *,
        text: str,
        tokens: list[Token],
        spans: list[Span],
        frames: list[FrameCandidate],
        reason: str,
        rejection_code: str,
        segments: list[SegmentDebug],
        ambiguous: bool = False,
    ) -> Interpretation:
        active = segments[-1] if segments else SegmentDebug(0, len(tokens))
        return Interpretation(
            text=text,
            tokens=tokens,
            spans=spans,
            frames=frames,
            accepted=False,
            ambiguous=ambiguous,
            reason=reason,
            rejection_code=rejection_code,
            response=self.responder.render(
                rejection_code,
                spans=spans,
                candidates=active.frame_candidates,
                actions=active.action_candidates,
            ),
            refusal_target=self.responder.target_phrase(spans, active.frame_candidates),
            segments=segments,
        )

    def _candidate_rejection_code(
        self,
        candidates: list[FrameCandidate],
        *,
        ambiguous: bool,
        spans: list[Span],
    ) -> str:
        if ambiguous:
            return "ambiguous"
        if not candidates:
            has_target = any(
                span.tag in {"name", "area", "floor", "domain", "device_class"}
                for span in spans
            )
            return "no_action" if has_target else "missing_target"

        best = candidates[0]
        # Even tolerated residual words make a constraint failure less
        # trustworthy to explain as a precise target/action incompatibility.
        # Prefer the neutral partial-understanding response in that case.
        if any("scope conflicts" in violation for violation in best.violations):
            return "conflicting_scope"
        if not best.unexplained_tokens and any(
            violation == "percentage must be an integer from 0 to 100"
            for violation in best.violations
        ):
            return "invalid_percentage"
        if best.unexplained_tokens:
            return "unexplained"
        if any(
            phrase in violation
            for violation in best.violations
            for phrase in (
                "incompatible with virtual action",
                "not allowed by name_domains",
                "not allowed by inferred_domains",
                "requires a known compatible entity domain",
            )
        ):
            return "unsupported_target_action"
        return "generic"

    @staticmethod
    def _target_context(frame: FrameCandidate) -> dict[str, Any]:
        return {
            slot: frame.slots[slot]
            for slot in ("name", "area", "floor", "domain", "device_class")
            if slot in frame.slots
        }

    @staticmethod
    def _has_local_target_span(spans: list[Span], segment: tuple[int, int]) -> bool:
        target_tags = {"name", "area", "floor", "domain", "device_class"}
        return any(
            span.tag in target_tags and GazetteerMatcher._in_segment(span, segment)
            for span in spans
        )

    @staticmethod
    def _matches_required_target(
        candidate: FrameCandidate, required: dict[str, Any]
    ) -> bool:
        if not required:
            return True
        for slot, value in required.items():
            if candidate.slots.get(slot) != value:
                return False
        return True

    def _resolve_coordination_reference(
        self,
        spans: list[Span],
        segment: tuple[int, int],
        chosen_frames: list[FrameCandidate],
    ) -> tuple[dict[str, Any], str | None, str | None]:
        references = [
            span
            for span in spans
            if span.tag == "coordination_reference" and self._in_segment(span, segment)
        ]
        if not references:
            return {}, None, None
        if len(references) != 1:
            return (
                {},
                "anaphora_multiple_pronouns",
                "only one coordinated target reference is supported",
            )

        reference = references[0]
        if self._has_local_target_span(spans, segment):
            return (
                {},
                "anaphora_explicit_target",
                "a coordinated target reference cannot be combined with an explicit target",
            )
        if not chosen_frames:
            return (
                {},
                "anaphora_missing_target",
                f"no preceding target for {reference.text!r}",
            )

        target = self._target_context(chosen_frames[-1])
        if not target:
            return (
                {},
                "anaphora_missing_target",
                f"no preceding target for {reference.text!r}",
            )
        if reference.value == "singular" and "name" not in target:
            return (
                {},
                "anaphora_singular_group",
                f"{reference.text!r} requires a single named entity target",
            )
        if reference.value == "singular":
            return {"name": target["name"]}, None, None
        return target, None, None

    @staticmethod
    def _normalize_previous_targets(
        previous_targets: Sequence[TargetReference] | None,
    ) -> tuple[TargetReference, ...]:
        targets = tuple(previous_targets or ())
        for target in targets:
            if not isinstance(target, TargetReference):
                raise TypeError("previous_targets must contain TargetReference values")
        # The rest was checked when each target was constructed.
        return targets

    def _resolve_anaphora(
        self,
        spans: list[Span],
        segment: tuple[int, int],
        actions: list[Span],
        previous_targets: tuple[TargetReference, ...],
    ) -> tuple[Span | None, TargetReference | None, str | None, str | None]:
        anaphors = [
            span
            for span in spans
            if span.tag == "anaphor" and self._in_segment(span, segment)
        ]
        if not anaphors:
            return None, None, None, None
        # "It" is also a grammatical subject in queries such as "what time
        # is it?". Anaphora is opt-in by action, so leave the token alone when
        # no supported device action is present.
        if not any(str(action.value) in self.anaphora_actions for action in actions):
            return None, None, None, None
        if len(anaphors) != 1:
            return (
                None,
                None,
                "anaphora_multiple_pronouns",
                "only one follow-up pronoun is supported",
            )
        anaphor = anaphors[0]
        action_indexes = {
            index for action in actions for index in self._span_indexes(action)
        }
        if any(
            span.tag in {"name", "area", "floor", "domain", "device_class"}
            and not set(self._span_indexes(span)).issubset(action_indexes)
            for span in spans
        ):
            return (
                None,
                None,
                "anaphora_explicit_target",
                "a follow-up pronoun cannot be combined with an explicit target",
            )
        if not previous_targets:
            return (
                None,
                None,
                "anaphora_missing_target",
                f"no previous target for {anaphor.text!r}",
            )
        if len(previous_targets) != 1:
            return (
                None,
                None,
                "anaphora_multiple_targets",
                "multiple previous targets are not supported",
            )

        target = previous_targets[0]
        if anaphor.value == "singular" and target.scope != "entity":
            return (
                None,
                None,
                "anaphora_singular_group",
                f"{anaphor.text!r} requires a single named entity target",
            )
        return anaphor, target, None, None

    def interpret(
        self,
        text: str,
        *,
        context_area: str | None = None,
        context_floor: str | None = None,
        previous_targets: Sequence[TargetReference] | None = None,
    ) -> Interpretation:
        """Interpret text with optional location and previous-target context.

        Context values may be home IDs, names, or aliases. An area's configured
        floor is used automatically when ``context_floor`` is omitted. Previous
        targets are considered only when the utterance contains a configured
        follow-up pronoun.
        """
        context = self._resolve_home_context(context_area, context_floor)
        resolved_previous_targets = self._normalize_previous_targets(previous_targets)
        tokens = normalize_tokens(text)
        spans = self.tagger.tag(tokens)
        conjunctions = self._select_conjunctions(spans)
        segments = self._segments(len(tokens), conjunctions)
        shared = self._unique_shareable_spans(spans)
        all_action_spans = [span for span in spans if span.tag == "action"]

        chosen_frames: list[FrameCandidate] = []
        segment_debug: list[SegmentDebug] = []
        previous_actions: list[Span] = []
        ambiguous = False

        for segment in segments:
            actions, inherited_action = self._segment_actions(
                spans, segment, previous_actions
            )
            local_actual_actions = [
                span
                for span in all_action_spans
                if self._touches_segment(span, segment)
            ]
            if local_actual_actions:
                previous_actions = self._prefer_action_spans(local_actual_actions)

            debug = SegmentDebug(
                start=segment[0], end=segment[1], action_candidates=actions
            )
            if not actions:
                debug.rejection_reason = "no action recognized or inherited"
                segment_debug.append(debug)
                return self._rejected_interpretation(
                    text=text,
                    tokens=tokens,
                    spans=spans,
                    frames=[],
                    reason=debug.rejection_reason,
                    rejection_code="no_action",
                    segments=segment_debug,
                )

            (
                anaphor_span,
                previous_target,
                anaphora_code,
                anaphora_error,
            ) = self._resolve_anaphora(
                spans,
                segment,
                actions,
                resolved_previous_targets,
            )
            if anaphora_error is not None:
                debug.rejection_reason = anaphora_error
                segment_debug.append(debug)
                return self._rejected_interpretation(
                    text=text,
                    tokens=tokens,
                    spans=spans,
                    frames=chosen_frames,
                    reason=anaphora_error,
                    rejection_code=anaphora_code or "generic",
                    segments=segment_debug,
                )

            (
                coordination_target,
                coordination_code,
                coordination_error,
            ) = self._resolve_coordination_reference(
                spans,
                segment,
                chosen_frames,
            )
            if coordination_error is not None:
                debug.rejection_reason = coordination_error
                segment_debug.append(debug)
                return self._rejected_interpretation(
                    text=text,
                    tokens=tokens,
                    spans=spans,
                    frames=chosen_frames,
                    reason=coordination_error,
                    rejection_code=coordination_code or "generic",
                    segments=segment_debug,
                )

            visible_spans = self._visible_spans_for_segment(spans, segment, shared)
            required_target = coordination_target
            if (
                not required_target
                and inherited_action
                and chosen_frames
                and not self._has_local_target_span(spans, segment)
            ):
                required_target = self._target_context(chosen_frames[-1])

            candidates: list[FrameCandidate] = []
            for action_span in actions:
                candidates.extend(
                    self._generate_frames_for_action(
                        action_span,
                        inherited_action,
                        tokens,
                        spans,
                        visible_spans,
                        segment,
                        context,
                        anaphor_span,
                        previous_target,
                        coordination_target,
                    )
                )
            if required_target:
                candidates = [
                    candidate
                    for candidate in candidates
                    if self._matches_required_target(candidate, required_target)
                ]
            candidates = self._dedupe_candidates(candidates)
            debug.frame_candidates = candidates
            chosen, segment_ambiguous, reason = self._choose_candidate(candidates)
            has_anaphoric_candidate = any(
                any(
                    option.source == "anaphora"
                    for option in candidate.slot_options.values()
                )
                for candidate in candidates
            )
            rejection_code = self._candidate_rejection_code(
                candidates,
                ambiguous=segment_ambiguous,
                spans=spans,
            )
            if anaphor_span is not None and not has_anaphoric_candidate:
                reason = "previous target is not supported by this action"
                rejection_code = "unsupported_target_action"
            debug.chosen = chosen
            debug.rejection_reason = reason
            segment_debug.append(debug)
            if segment_ambiguous:
                ambiguous = True
            if chosen is None:
                return self._rejected_interpretation(
                    text=text,
                    tokens=tokens,
                    spans=spans,
                    frames=chosen_frames,
                    ambiguous=ambiguous,
                    reason=reason or "command was rejected",
                    rejection_code=rejection_code,
                    segments=segment_debug,
                )
            chosen_frames.append(chosen)

        return Interpretation(
            text=text,
            tokens=tokens,
            spans=spans,
            frames=chosen_frames,
            accepted=True,
            ambiguous=False,
            segments=segment_debug,
        )

    def support_summary(self) -> dict[str, Any]:
        configured_intents = {
            intent
            for spec in self.actions.values()
            for intent in (spec.get("intents") or [])
        }
        summary = self.catalog.support_summary(configured_intents)
        summary["configured_virtual_actions"] = len(self.actions)
        return summary
