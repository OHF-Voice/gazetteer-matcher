from __future__ import annotations

import functools
from dataclasses import dataclass, field

from unicode_rbnf import FormatPurpose, RbnfEngine

from .config import phrase_tokens
from .models import Span, Token


@dataclass
class TrieNode:
    children: dict[str, "TrieNode"] = field(default_factory=dict)
    values: list[tuple[int, str]] = field(default_factory=list)


@functools.lru_cache(maxsize=8)
def _shared_trie(
    language: str,
    max_cardinal: int,
    max_ordinal: int,
    joiners: tuple[str, ...],
    negative_signs: tuple[str, ...],
    positive_signs: tuple[str, ...],
) -> NumberWordTrie:
    """Build a trie once per distinct settings tuple. See NumberWordTrie.shared."""
    return NumberWordTrie(
        language,
        max_cardinal=max_cardinal,
        max_ordinal=max_ordinal,
        joiners=list(joiners),
        negative_signs=list(negative_signs),
        positive_signs=list(positive_signs),
    )


class NumberWordTrie:
    """Parse number words by reversing Unicode CLDR RBNF output into a trie.

    Building one asks RBNF to spell every number up to the configured maxima, which
    is by far the most expensive thing in this package and depends only on the
    language and those maxima. Use :meth:`shared` unless a private copy is wanted.
    """

    @classmethod
    def shared(
        cls,
        language: str,
        *,
        max_cardinal: int,
        max_ordinal: int,
        joiners: list[str] | None = None,
        negative_signs: list[str] | None = None,
        positive_signs: list[str] | None = None,
    ) -> NumberWordTrie:
        """Return a cached trie for these settings, building it on first use.

        The trie is read-only once built, so every matcher for a language can share
        one. That matters to callers that rebuild a matcher when their home changes:
        without this, swapping the gazetteer re-spells every number in the language.
        """
        return _shared_trie(
            language,
            max_cardinal,
            max_ordinal,
            tuple(joiners or ()),
            tuple(negative_signs or ()),
            tuple(positive_signs or ()),
        )

    def __init__(
        self,
        language: str,
        *,
        max_cardinal: int,
        max_ordinal: int,
        joiners: list[str] | None = None,
        negative_signs: list[str] | None = None,
        positive_signs: list[str] | None = None,
    ) -> None:
        self.language = language
        self.root = TrieNode()
        self.joiners = set(joiners or [])
        self.signs = {
            **{word.casefold(): -1 for word in negative_signs or []},
            **{word.casefold(): 1 for word in positive_signs or []},
        }
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
            if token.text.lstrip("+-")[0].isdigit():
                spans.append(
                    Span(
                        start=token.index,
                        end=token.index + 1,
                        tag="number",
                        value=value,
                        text=token.raw,
                        source="number_literal",
                        meta={
                            "kind": "cardinal",
                            "explicit_sign": token.text[0] in "+-",
                        },
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

        # Compose a configured spoken sign with every number beginning at the
        # following token. Drop the unsigned alternatives at that start: otherwise
        # an invalid negative duration/percentage could still be accepted as its
        # positive value with the sign left unexplained.
        signed_spans: list[Span] = []
        for span in spans:
            sign_index = span.start - 1
            sign = self.signs.get(tokens[sign_index].text) if sign_index >= 0 else None
            if sign is None:
                signed_spans.append(span)
                continue
            signed_spans.append(
                Span(
                    start=sign_index,
                    end=span.end,
                    tag=span.tag,
                    value=sign * span.value,
                    text=" ".join(token.raw for token in tokens[sign_index : span.end]),
                    source=f"signed_{span.source}",
                    meta={**span.meta, "explicit_sign": True},
                )
            )
        spans = signed_spans

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
