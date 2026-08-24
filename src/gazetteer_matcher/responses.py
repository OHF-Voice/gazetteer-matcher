from __future__ import annotations

from typing import Any, Iterable

from .models import FrameCandidate, Span

_TARGET_SLOTS = ("name", "area", "floor", "domain", "device_class")


class RejectionResponder:
    """Render concise user-facing rejection messages from external templates."""

    def __init__(self, responses: dict[str, Any], home: dict[str, Any]) -> None:
        self.responses = responses
        self.home = home
        self.templates = responses.get("templates") or {}
        self.target_templates = responses.get("target_templates") or {}
        if not self.templates.get("generic"):
            raise ValueError("responses must define templates.generic")

    @staticmethod
    def _unique_span(spans: Iterable[Span], tag: str) -> Span | None:
        matching = [span for span in spans if span.tag == tag]
        values = {repr(span.value) for span in matching}
        if len(values) != 1:
            return None
        return min(
            matching,
            key=lambda span: (
                span.source.startswith("fuzzy"),
                -span.similarity,
                -span.length,
            ),
        )

    def _target_fields(
        self,
        spans: list[Span],
        candidates: list[FrameCandidate],
    ) -> dict[str, str]:
        fields: dict[str, str] = {}
        slots: dict[str, Any] = {}
        if candidates:
            slots.update(
                {
                    slot: candidates[0].slots[slot]
                    for slot in _TARGET_SLOTS
                    if slot in candidates[0].slots
                }
            )

        # Prefer unambiguous lexical evidence over an inferred candidate slot.
        for slot in _TARGET_SLOTS:
            span = self._unique_span(spans, slot)
            if span is not None:
                slots[slot] = span.value

        entity: dict[str, Any] | None = None
        if "name" in slots:
            entity_id = str(slots["name"])
            entity = (self.home.get("entities") or {}).get(entity_id) or {}
            name = str(entity.get("name") or entity_id)
            fields["target"] = self._format_target("named", name=name)
            fields["named_target"] = "true"

        domain = str(slots["domain"]) if "domain" in slots else None
        if entity is not None:
            entity_id = str(slots["name"])
            domain = str(entity.get("domain") or entity_id.split(".", 1)[0])

        domain_labels = self.responses.get("domains") or {}
        if domain is not None:
            labels = domain_labels.get(domain) or {}
            if labels.get("singular"):
                fields["target_type"] = str(labels["singular"])

        if "target" not in fields:
            device_class = slots.get("device_class")
            kind = None
            if device_class is not None:
                kind = (self.responses.get("device_classes") or {}).get(
                    str(device_class)
                )
            if kind is None and domain is not None:
                labels = domain_labels.get(domain) or {}
                kind = labels.get("plural")

            area = self._home_label("areas", slots.get("area"))
            floor = self._home_label("floors", slots.get("floor"))
            if kind and area:
                fields["target"] = self._format_target(
                    "area_kind", kind=kind, area=area
                )
            elif kind and floor:
                fields["target"] = self._format_target(
                    "floor_kind", kind=kind, floor=floor
                )
            elif kind:
                fields["target"] = self._format_target("kind", kind=kind)
            elif area:
                fields["target"] = self._format_target("area", area=area)
            elif floor:
                fields["target"] = self._format_target("floor", floor=floor)
        return fields

    def _home_label(self, collection: str, value: Any) -> str | None:
        if value is None:
            return None
        spec = (self.home.get(collection) or {}).get(str(value)) or {}
        return str(spec.get("name") or value)

    def _format_target(self, key: str, **fields: Any) -> str:
        template = self.target_templates.get(key)
        if not template:
            return ""
        try:
            return str(template).format_map(fields)
        except KeyError:
            return ""

    def render(
        self,
        code: str,
        *,
        spans: list[Span],
        candidates: list[FrameCandidate] | None = None,
        actions: list[Span] | None = None,
    ) -> str:
        candidates = candidates or []
        actions = actions or []
        fields = self._target_fields(spans, candidates)

        action_key = candidates[0].action if candidates else None
        if action_key is None and actions:
            action_key = str(actions[0].value)
        if action_key is not None:
            action_labels_key = (
                "action_attempts" if code == "missing_target" else "actions"
            )
            action_label = (self.responses.get(action_labels_key) or {}).get(action_key)
            if action_label:
                fields["action"] = str(action_label)

        pronoun = self._unique_span(spans, "anaphor") or self._unique_span(
            spans, "coordination_reference"
        )
        if pronoun is not None:
            fields["pronoun"] = pronoun.text

        template_key = code
        if code == "no_action":
            if fields.get("named_target") and fields.get("target_type"):
                template_key = "no_action_named_target"
            elif fields.get("target"):
                template_key = "no_action_target"
        elif code == "unsupported_target_action":
            if fields.get("named_target") and fields.get("target_type"):
                template_key = "unsupported_target_action_named"
            elif fields.get("target"):
                template_key = "unsupported_target_action"
            else:
                template_key = "unsupported_action"
        elif code == "unexplained" and fields.get("target"):
            template_key = "unexplained_target"

        generic = str(self.templates["generic"])
        template = str(self.templates.get(template_key) or generic)
        try:
            return template.format_map(fields)
        except KeyError:
            return generic
