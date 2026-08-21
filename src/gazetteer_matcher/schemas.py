from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IntentCombination:
    intent: str
    name: str
    slots: tuple[str, ...]
    wildcard_slots: tuple[str, ...]
    context_area: bool | None
    inferred_domains: frozenset[str]
    name_domains: frozenset[str]
    importance: str | None
    example: Any

    @property
    def is_wildcard(self) -> bool:
        return bool(self.wildcard_slots)


def _flatten_grouped_values(value: Any) -> frozenset[str]:
    if not value:
        return frozenset()
    if isinstance(value, list):
        return frozenset(str(item) for item in value)
    if isinstance(value, dict):
        result: set[str] = set()
        for items in value.values():
            if isinstance(items, list):
                result.update(str(item) for item in items)
        return frozenset(result)
    return frozenset({str(value)})


class IntentCatalog:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.by_intent: dict[str, list[IntentCombination]] = {}
        self.all: list[IntentCombination] = []
        for intent, spec in raw.items():
            for name, combo in (spec.get("slot_combinations") or {}).items():
                item = IntentCombination(
                    intent=intent,
                    name=name,
                    slots=tuple(combo.get("slots") or ()),
                    wildcard_slots=tuple(combo.get("wildcard_slots") or ()),
                    context_area=combo.get("context_area"),
                    inferred_domains=_flatten_grouped_values(combo.get("inferred_domains")),
                    name_domains=_flatten_grouped_values(combo.get("name_domains")),
                    importance=combo.get("importance"),
                    example=combo.get("example"),
                )
                self.by_intent.setdefault(intent, []).append(item)
                self.all.append(item)

    def intent_domain(self, intent: str) -> str | None:
        spec = self.raw.get(intent) or {}
        value = spec.get("domain")
        return str(value) if value else None

    def combinations(self, intent: str, *, include_wildcard: bool = False) -> list[IntentCombination]:
        combos = self.by_intent.get(intent, [])
        if include_wildcard:
            return list(combos)
        return [combo for combo in combos if not combo.is_wildcard]

    def support_summary(self, configured_intents: set[str]) -> dict[str, Any]:
        nonwild = [combo for combo in self.all if not combo.is_wildcard]
        wildcard = [combo for combo in self.all if combo.is_wildcard]
        reachable = [combo for combo in nonwild if combo.intent in configured_intents]
        missing = sorted({combo.intent for combo in nonwild if combo.intent not in configured_intents})
        return {
            "total_combinations": len(self.all),
            "wildcard_combinations_excluded": len(wildcard),
            "non_wildcard_combinations": len(nonwild),
            "non_wildcard_combinations_with_action_mapping": len(reachable),
            "non_wildcard_intents": len({combo.intent for combo in nonwild}),
            "mapped_non_wildcard_intents": len({combo.intent for combo in reachable}),
            "unmapped_intents": missing,
        }
