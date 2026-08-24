"""Which response the upstream corpus writes for a recognized shape.

A frame says what to do. Saying something back is a separate question, and the
Home Assistant sentence corpus already answers it: every sentence block carries a
``response`` key alongside the slot combination this package validates frames
against. So the key for a frame is a lookup on (intent, combination), narrowed by
the target's domain where a combination spells several -- ``HassTurnOn/domain_only``
answers "Turned on the lights" for ``light`` and "Turned on the fans" for ``fan``.

Deriving keys from the same corpus the frames are validated against, rather than
naming them in ``vocabulary.yaml``, is what keeps them from drifting apart: a key
this package invented could name a template that no longer exists.

Where the corpus answers one shape more than one way, this says nothing and leaves
it to the lexical hints in ``vocabulary.yaml``. "Are any lights on" and "how many
lights are on" are the same frame, and only the words said separate them.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any, Optional

from home_assistant_intents import get_intents


@dataclass(frozen=True)
class _Key:
    """One sentence block's response key, and the domains it was written for."""

    key: str
    domains: frozenset[str]

    def fits(self, domain: str | None) -> bool:
        return not self.domains or domain is None or domain in self.domains


def _domains(block: Any) -> frozenset[str]:
    """Flatten however a domain list is written: one domain, a list, or tiers."""
    if not block:
        return frozenset()
    if isinstance(block, str):
        return frozenset([block])
    if isinstance(block, list):
        return frozenset(block)
    return frozenset(
        domain
        for value in block.values()
        for domain in (value if isinstance(value, list) else [value])
    )


class ResponseKeys:
    """Every response key the corpus writes, indexed by the shape it answers."""

    def __init__(self, sentences: dict[str, Any]) -> None:
        keys: dict[tuple[str, str], list[_Key]] = {}
        for intent, intent_data in (sentences.get("intents") or {}).items():
            for block in (intent_data or {}).get("data") or []:
                response = block.get("response")
                combination = (block.get("metadata") or {}).get("slot_combination")
                if not response or not combination:
                    continue

                domains = _domains((block.get("slots") or {}).get("domain")) | _domains(
                    (block.get("requires_context") or {}).get("domain")
                )
                keys.setdefault((intent, str(combination)), []).append(
                    _Key(str(response), domains)
                )

        self._keys = {
            shape: tuple(dict.fromkeys(found)) for shape, found in keys.items()
        }

    def __bool__(self) -> bool:
        return bool(self._keys)

    def key_for(
        self, intent: str, combination: str, domain: str | None = None
    ) -> str | None:
        """Return the one key the corpus writes for this shape, if it writes one.

        A key written for a domain beats a generic one, and what is left has to be
        unanimous. A shape answered two ways is one the slots cannot choose between.
        """
        candidates = self._keys.get((intent, combination), ())
        fitting = [key for key in candidates if key.fits(domain)]
        preferred = [key for key in fitting if key.domains] or fitting
        distinct = list(dict.fromkeys(key.key for key in preferred))
        return distinct[0] if len(distinct) == 1 else None


@functools.lru_cache(maxsize=4)
def load_response_keys(language: str) -> ResponseKeys:
    """Return the keys for a language, reading the corpus once.

    A language the package does not ship yields an empty set, which names no keys
    rather than failing: a caller with its own wording does not need these at all.
    """
    sentences: Optional[dict[str, Any]] = get_intents(language)
    return ResponseKeys(sentences or {})
