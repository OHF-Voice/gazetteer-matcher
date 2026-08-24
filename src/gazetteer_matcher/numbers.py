from __future__ import annotations

from dataclasses import dataclass, field

from unicode_rbnf import FormatPurpose, RbnfEngine

from .config import phrase_tokens
from .models import Span, Token


@dataclass
class TrieNode:
    children: dict[str, "TrieNode"] = field(default_factory=dict)
    values: list[tuple[int, str]] = field(default_factory=list)


class NumberWordTrie:
    """Parse number words by reversing Unicode CLDR RBNF output into a trie."""

    def __init__(
        self,
        language: str,
        *,
        max_cardinal: int,
        max_ordinal: int,
        joiners: list[str] | None = None,
    ) -> None:
        self.language = language
        self.root = TrieNode()
        self.joiners = set(joiners or [])
        engine = RbnfEngine.for_language(language)

        for value in range(max_cardinal + 1):
            result = engine.format_number(value, FormatPurpose.CARDINAL)
            self._add(result.text, value, "cardinal")
            for alternate in result.text_by_ruleset.values():
                self._add(alternate, value, "cardinal")

        for value in range(max_ordinal + 1):
            try:
                result = engine.format_number(value, FormatPurpose.ORDINAL)
            except (KeyError, ValueError):
                break
            self._add(result.text, value, "ordinal")
            for alternate in result.text_by_ruleset.values():
                self._add(alternate, value, "ordinal")

    def _add(self, text: str, value: int, kind: str) -> None:
        tokens = phrase_tokens(text)
        if not tokens:
            return
        node = self.root
        for token in tokens:
            node = node.children.setdefault(token, TrieNode())
        item = (value, kind)
        if item not in node.values:
            node.values.append(item)

    def find(self, tokens: list[Token]) -> list[Span]:
        spans: list[Span] = []

        # Numeric literals are cheap and do not depend on locale RBNF.
        for token in tokens:
            literal = token.text.replace(",", ".")
            try:
                value: int | float = float(literal)
            except ValueError:
                continue
            if value.is_integer():
                value = int(value)
            if token.text[0].isdigit():
                spans.append(
                    Span(
                        start=token.index,
                        end=token.index + 1,
                        tag="number",
                        value=value,
                        text=token.raw,
                        source="number_literal",
                        meta={"kind": "cardinal"},
                    )
                )

        for start in range(len(tokens)):
            node = self.root
            index = start
            best: list[tuple[int, int, str]] = []
            advanced = False
            while index < len(tokens):
                token_text = tokens[index].text
                child = node.children.get(token_text)
                if child is not None:
                    node = child
                    advanced = True
                    index += 1
                    if node.values:
                        best = [(index, value, kind) for value, kind in node.values]
                    continue

                # Permit configured joiners *inside* a valid longer number.
                if advanced and token_text in self.joiners:
                    index += 1
                    continue
                break

            for end, value, kind in best:
                spans.append(
                    Span(
                        start=start,
                        end=end,
                        tag="number",
                        value=value,
                        text=" ".join(token.raw for token in tokens[start:end]),
                        source="unicode_rbnf",
                        meta={"kind": kind},
                    )
                )

        # CLDR only supplies integer cardinal forms. Compose the common spoken
        # decimal form ("twenty point five") from two cardinal spans.
        integer_spans = [span for span in spans if span.meta.get("kind") == "cardinal"]
        by_start: dict[int, list[Span]] = {}
        for span in integer_spans:
            by_start.setdefault(span.start, []).append(span)
        words = [token.text for token in tokens]
        for left in integer_spans:
            point = left.end
            if point >= len(words) or words[point] not in {"point", "dot"}:
                continue
            for right in by_start.get(point + 1, []):
                right_words = "".join(words[point + 1 : right.end])
                if not right_words or not right_words.isdigit():
                    right_words = str(int(right.value))
                decimal = float(f"{int(left.value)}.{right_words}")
                spans.append(
                    Span(
                        start=left.start,
                        end=right.end,
                        tag="number",
                        value=decimal,
                        text=" ".join(
                            token.raw for token in tokens[left.start : right.end]
                        ),
                        source="composed_decimal",
                        meta={"kind": "cardinal"},
                    )
                )

        # Prefer the longest number at a given start/value; nested shorter
        # alternatives remain useful only when they represent a different value.
        unique: dict[tuple[int, int, str, str], Span] = {}
        for span in spans:
            unique[
                (span.start, span.end, repr(span.value), span.meta.get("kind", ""))
            ] = span
        return sorted(
            unique.values(), key=lambda span: (span.start, -span.length, span.value)
        )
