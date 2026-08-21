from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Token:
    index: int
    text: str
    raw: str
    start_char: int
    end_char: int


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    tag: str
    value: Any
    text: str
    source: str = "exact"
    similarity: float = 1.0
    meta: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def length(self) -> int:
        return self.end - self.start

    def token_indexes(self) -> range:
        return range(self.start, self.end)


@dataclass(frozen=True)
class SlotOption:
    slot: str
    value: Any
    spans: tuple[Span, ...] = ()
    source: str = "direct"
    similarity: float = 1.0
    inherited: bool = False
    allow_overlap: bool = False


@dataclass
class FrameCandidate:
    intent: str
    combination: str
    action: str
    slots: dict[str, Any]
    slot_options: dict[str, SlotOption]
    action_span: Span
    segment: tuple[int, int]
    unexplained_important_tokens: list[int] = field(default_factory=list)
    unexplained_unimportant_tokens: list[int] = field(default_factory=list)
    inherited_slots: int = 0
    inherited_action: bool = False
    fuzzy_count: int = 0
    fuzzy_distance: float = 0.0
    target_generality: int = 1
    violations: list[str] = field(default_factory=list)
    cost: tuple[Any, ...] = ()

    @property
    def unexplained_tokens(self) -> list[int]:
        """All unexplained token indexes, retained as a compatibility view."""
        return sorted(
            self.unexplained_important_tokens + self.unexplained_unimportant_tokens
        )

    def semantic_key(self) -> tuple[Any, ...]:
        normalized = tuple(sorted((key, repr(value)) for key, value in self.slots.items()))
        return self.intent, normalized


@dataclass
class SegmentDebug:
    start: int
    end: int
    action_candidates: list[Span] = field(default_factory=list)
    frame_candidates: list[FrameCandidate] = field(default_factory=list)
    chosen: FrameCandidate | None = None
    rejection_reason: str | None = None


@dataclass
class Interpretation:
    text: str
    tokens: list[Token]
    spans: list[Span]
    frames: list[FrameCandidate]
    accepted: bool
    ambiguous: bool = False
    reason: str | None = None
    segments: list[SegmentDebug] = field(default_factory=list)
