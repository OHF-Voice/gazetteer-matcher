"""Constraint-driven Home Assistant gazetteer matcher."""

from .matcher import GazetteerMatcher
from .models import FrameCandidate, Interpretation, Span, TargetReference, Token

__all__ = [
    "GazetteerMatcher",
    "Interpretation",
    "FrameCandidate",
    "Span",
    "TargetReference",
    "Token",
]
__version__ = "0.1.0"
