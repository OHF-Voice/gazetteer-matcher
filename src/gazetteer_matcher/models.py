from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

TargetScope = Literal["entity", "area", "floor", "home"]
_TARGET_SLOTS = ("name", "area", "floor", "domain", "device_class")
_SCOPE_REQUIRES = {"entity": "name", "area": "area", "floor": "floor"}


class AreaSpec(TypedDict, total=False):
    """One area of a home. ``floor`` is the id of the floor it is on."""

    name: str
    aliases: list[str]
    floor: str | None


class FloorSpec(TypedDict, total=False):
    """One floor of a home."""

    name: str
    aliases: list[str]


class EntitySpec(TypedDict, total=False):
    """One entity of a home. ``area`` is an area id; the floor comes from that."""

    name: str
    aliases: list[str]
    domain: str
    area: str | None
    device_class: str


class Home(TypedDict, total=False):
    """The gazetteer a matcher resolves names against, keyed by id throughout.

    The ids are the caller's own -- frames come back carrying them, so whatever a
    caller puts in is what it gets out and can act on.
    """

    areas: dict[str, AreaSpec]
    floors: dict[str, FloorSpec]
    entities: dict[str, EntitySpec]


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


@dataclass(frozen=True)
class TargetReference:
    """Reusable target selector from a previous successful interpretation.

    Validated here rather than when it is handed back to ``interpret``, so a caller
    building one from its own records is told at the point it got it wrong.
    """

    slots: dict[str, Any]
    scope: TargetScope

    def __post_init__(self) -> None:
        slots = set(self.slots)
        if not slots or not slots <= set(_TARGET_SLOTS):
            raise ValueError(
                f"target slots must be a non-empty subset of {list(_TARGET_SLOTS)}, "
                f"got {sorted(slots)}"
            )
        if self.scope not in {"entity", "area", "floor", "home"}:
            raise ValueError(f"unsupported target scope {self.scope!r}")

        required = _SCOPE_REQUIRES.get(self.scope)
        if required is not None and required not in slots:
            raise ValueError(f"{self.scope!r} target requires a {required!r} slot")
        if self.scope == "home" and slots & {"name", "area", "floor"}:
            raise ValueError("home-scoped target cannot carry a local selector")

    @classmethod
    def for_entity(cls, entity_id: str, **slots: Any) -> TargetReference:
        """A target naming one entity."""
        return cls(slots={**slots, "name": entity_id}, scope="entity")

    @classmethod
    def for_area(cls, area_id: str, **slots: Any) -> TargetReference:
        """A target scoped to an area, optionally narrowed by domain/device class."""
        return cls(slots={**slots, "area": area_id}, scope="area")

    @classmethod
    def for_floor(cls, floor_id: str, **slots: Any) -> TargetReference:
        """A target scoped to a floor, optionally narrowed by domain/device class."""
        return cls(slots={**slots, "floor": floor_id}, scope="floor")


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
    target_scope: TargetScope | None = None
    violations: list[str] = field(default_factory=list)
    cost: tuple[Any, ...] = ()
    response_key: str | None = None

    anaphor_target: int | None = None
    """Which supplied antecedent this reading resolved "it"/"them" against.

    Ranking compares only the readings of the first, and matches the rest to the
    winner by this index.
    """

    @property
    def unexplained_tokens(self) -> list[int]:
        """All unexplained token indexes, retained as a compatibility view."""
        return sorted(
            self.unexplained_important_tokens + self.unexplained_unimportant_tokens
        )

    def semantic_key(self) -> tuple[Any, ...]:
        normalized = tuple(
            sorted((key, repr(value)) for key, value in self.slots.items())
        )
        return self.intent, normalized, self.response_key


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
    rejection_code: str | None = None
    response: str | None = None

    refusal_target: str | None = None
    """How ``response`` names what the refusal was aimed at, when it names anything.

    A refusal that resolved something in the home explains itself -- "you're
    targeting the lights in Kitchen, but I don't know what action to take" -- and is
    worth saying. Most do not: noise resolves nothing, and "asdfgh" and "do
    something" both come back as the same generic wording, which a caller with an
    error message of its own should prefer. This is how to tell the two apart
    without reading the lexical spans.
    """

    segments: list[SegmentDebug] = field(default_factory=list)

    @property
    def targets(self) -> tuple[TargetReference, ...]:
        """Targets suitable for an explicit-pronoun follow-up.

        Failed multi-segment interpretations deliberately expose no targets so
        callers do not update conversation state from a partially matched
        command.
        """
        if not self.accepted:
            return ()
        result: list[TargetReference] = []
        for frame in self.frames:
            if frame.target_scope is None:
                continue
            slots = {
                slot: frame.slots[slot] for slot in _TARGET_SLOTS if slot in frame.slots
            }
            if slots:
                target = TargetReference(slots=slots, scope=frame.target_scope)
                if target not in result:
                    result.append(target)
        return tuple(result)
