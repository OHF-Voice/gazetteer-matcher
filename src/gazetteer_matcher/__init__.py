"""Constraint-driven Home Assistant gazetteer matcher."""

from .explain import Note, explain
from .matcher import GazetteerMatcher
from .models import (
    AreaSpec,
    EntitySpec,
    FloorSpec,
    FrameCandidate,
    Home,
    Interpretation,
    Span,
    TargetReference,
    Token,
    UnbuiltCombination,
)
from .response_keys import ResponseKeys, load_response_keys

__all__ = [
    "AreaSpec",
    "EntitySpec",
    "FloorSpec",
    "FrameCandidate",
    "GazetteerMatcher",
    "Home",
    "Interpretation",
    "Note",
    "ResponseKeys",
    "Span",
    "TargetReference",
    "Token",
    "UnbuiltCombination",
    "explain",
    "load_response_keys",
]
__version__ = "1.2.0"
