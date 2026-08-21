from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from .models import FrameCandidate, Interpretation, Span, Token


def _value(value: Any) -> str:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return repr(value)
    return repr(value)


def render_tokens(tokens: list[Token]) -> str:
    lines = ["TOKENS", "idx  text                 chars", "---  -------------------  ---------"]
    for token in tokens:
        lines.append(f"{token.index:>3}  {token.text:<19}  {token.start_char}:{token.end_char}")
    return "\n".join(lines)


def render_spans(spans: list[Span]) -> str:
    lines = [
        "SPANS",
        "range    tag                 source            sim    value / text",
        "-------  ------------------  ----------------  -----  ------------------------------",
    ]
    for span in spans:
        value = _value(span.value)
        matched = span.meta.get("matched")
        suffix = f" matched={matched!r}" if matched else ""
        lines.append(
            f"{span.start:>2}:{span.end:<3}  {span.tag:<18}  {span.source:<16}  "
            f"{span.similarity:>5.2f}  {value} <- {span.text!r}{suffix}"
        )
    return "\n".join(lines)


def render_candidate(candidate: FrameCandidate, tokens: list[Token]) -> str:
    inherited = [slot for slot, option in candidate.slot_options.items() if option.inherited]
    lines = [
        f"{candidate.intent}.{candidate.combination} action={candidate.action!r} cost={candidate.cost}",
        f"  slots={candidate.slots}",
    ]
    if inherited or candidate.inherited_action:
        lines.append(
            f"  inherited_action={candidate.inherited_action} inherited_slots={inherited}"
        )
    if candidate.violations:
        lines.append(f"  violations={candidate.violations}")
    if candidate.unexplained_important_tokens:
        unknown = " ".join(
            tokens[index].raw for index in candidate.unexplained_important_tokens
        )
        lines.append(
            "  unexplained_important="
            f"{candidate.unexplained_important_tokens} text={unknown!r}"
        )
    if candidate.unexplained_unimportant_tokens:
        unknown = " ".join(
            tokens[index].raw for index in candidate.unexplained_unimportant_tokens
        )
        lines.append(
            "  unexplained_unimportant="
            f"{candidate.unexplained_unimportant_tokens} text={unknown!r}"
        )
    return "\n".join(lines)


def render_interpretation(result: Interpretation, *, candidate_limit: int = 12) -> str:
    lines = [render_tokens(result.tokens), "", render_spans(result.spans), ""]
    for index, segment in enumerate(result.segments, 1):
        segment_text = " ".join(
            token.raw for token in result.tokens[segment.start : segment.end]
        )
        lines.append(f"SEGMENT {index} [{segment.start}:{segment.end}] {segment_text!r}")
        lines.append(
            "  actions: "
            + ", ".join(
                f"{span.value}@{span.start}:{span.end}/{span.source}/{span.similarity:.2f}"
                for span in segment.action_candidates
            )
        )
        lines.append("  candidates:")
        for candidate in segment.frame_candidates[:candidate_limit]:
            rendered = render_candidate(candidate, result.tokens).replace("\n", "\n    ")
            lines.append("    " + rendered)
        if len(segment.frame_candidates) > candidate_limit:
            lines.append(
                f"    ... {len(segment.frame_candidates) - candidate_limit} more candidates"
            )
        if segment.chosen:
            lines.append("  CHOSEN:")
            lines.append("    " + render_candidate(segment.chosen, result.tokens).replace("\n", "\n    "))
        elif segment.rejection_reason:
            lines.append(f"  REJECTED: {segment.rejection_reason}")
        lines.append("")

    lines.append(f"ACCEPTED: {result.accepted}")
    if result.ambiguous:
        lines.append("AMBIGUOUS: True")
    if result.reason:
        lines.append(f"REASON: {result.reason}")
    if result.frames:
        lines.append("FRAMES:")
        for frame in result.frames:
            lines.append(f"  - intent: {frame.intent}")
            lines.append(f"    combination: {frame.combination}")
            lines.append(f"    slots: {frame.slots}")
    return "\n".join(lines)


def interpretation_to_dict(result: Interpretation, *, include_candidates: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "text": result.text,
        "accepted": result.accepted,
        "ambiguous": result.ambiguous,
        "reason": result.reason,
        "tokens": [asdict(token) for token in result.tokens],
        "spans": [
            {
                "start": span.start,
                "end": span.end,
                "tag": span.tag,
                "value": span.value,
                "text": span.text,
                "source": span.source,
                "similarity": span.similarity,
                "meta": {
                    key: value
                    for key, value in span.meta.items()
                    if key not in {"number_span", "unit_span"}
                },
            }
            for span in result.spans
        ],
        "frames": [
            {
                "intent": frame.intent,
                "combination": frame.combination,
                "action": frame.action,
                "slots": frame.slots,
                "cost": list(frame.cost),
            }
            for frame in result.frames
        ],
        "targets": [
            {
                "slots": target.slots,
                "scope": target.scope,
            }
            for target in result.targets
        ],
    }
    if include_candidates:
        payload["segments"] = [
            {
                "start": segment.start,
                "end": segment.end,
                "rejection_reason": segment.rejection_reason,
                "chosen": (
                    {
                        "intent": segment.chosen.intent,
                        "combination": segment.chosen.combination,
                        "slots": segment.chosen.slots,
                        "cost": list(segment.chosen.cost),
                    }
                    if segment.chosen
                    else None
                ),
                "candidates": [
                    {
                        "intent": candidate.intent,
                        "combination": candidate.combination,
                        "action": candidate.action,
                        "slots": candidate.slots,
                        "cost": list(candidate.cost),
                        "violations": candidate.violations,
                        "unexplained_tokens": candidate.unexplained_tokens,
                        "unexplained_important_tokens": candidate.unexplained_important_tokens,
                        "unexplained_unimportant_tokens": candidate.unexplained_unimportant_tokens,
                    }
                    for candidate in segment.frame_candidates
                ],
            }
            for segment in result.segments
        ]
    return payload


def render_json(result: Interpretation, *, include_candidates: bool = False) -> str:
    return json.dumps(
        interpretation_to_dict(result, include_candidates=include_candidates),
        indent=2,
        ensure_ascii=False,
        default=str,
    )
