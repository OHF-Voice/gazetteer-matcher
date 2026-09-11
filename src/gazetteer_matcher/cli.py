from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .debug import render_interpretation, render_json, render_spans
from .explain import explain, render
from .matcher import GazetteerMatcher


def _add_paths(parser: argparse.ArgumentParser, *, include_home: bool = True) -> None:
    parser.add_argument("--vocabulary", type=Path, help="Override vocabulary.yaml")
    if include_home:
        parser.add_argument("--home", type=Path, help="Load a home gazetteer")
    parser.add_argument("--responses", type=Path, help="Override responses.yaml")


def _matcher(args: argparse.Namespace) -> GazetteerMatcher:
    return GazetteerMatcher(
        vocabulary_path=getattr(args, "vocabulary", None),
        home_path=getattr(args, "home", None),
        responses_path=getattr(args, "responses", None),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gazetteer-match")
    subparsers = parser.add_subparsers(dest="command", required=True)

    match_parser = subparsers.add_parser("match", help="Interpret an utterance")
    match_parser.add_argument("text")
    match_parser.add_argument(
        "--context-area",
        help="Voice satellite area ID, name, or alias",
    )
    match_parser.add_argument(
        "--context-floor",
        help="Voice satellite floor ID, name, or alias",
    )
    match_parser.add_argument(
        "--debug", action="store_true", help="Show spans and candidate frames"
    )
    match_parser.add_argument(
        "--explain",
        action="store_true",
        help="Say in a few lines how the sentence was read",
    )
    match_parser.add_argument("--json", action="store_true", help="Emit JSON")
    _add_paths(match_parser, include_home=False)
    home_group = match_parser.add_mutually_exclusive_group(required=True)
    home_group.add_argument("--home", type=Path, help="Load a home gazetteer")
    home_group.add_argument(
        "--empty-home",
        action="store_true",
        help="Interpret without entities, areas, or floors",
    )

    spans_parser = subparsers.add_parser("spans", help="Show lexical spans only")
    spans_parser.add_argument("text")
    _add_paths(spans_parser)

    support_parser = subparsers.add_parser(
        "support", help="Report intent-metadata combination coverage"
    )
    _add_paths(support_parser, include_home=False)

    args = parser.parse_args(argv)
    matcher = _matcher(args)

    if args.command == "support":
        print(json.dumps(matcher.support_summary(), indent=2))
        return 0

    try:
        result = matcher.interpret(
            args.text,
            context_area=getattr(args, "context_area", None),
            context_floor=getattr(args, "context_floor", None),
        )
    except ValueError as err:
        parser.error(str(err))
    if args.command == "spans":
        print(render_spans(result.spans))
        return 0

    if args.explain:
        notes = explain(matcher, result)
        if args.json:
            print(json.dumps([asdict(note) for note in notes], indent=2))
        else:
            print(render(notes))
        # A rejection still fails, so --explain can be added to any invocation
        # without changing what a script makes of it.
        return 0 if result.accepted else 2

    if args.json:
        print(render_json(result, include_candidates=args.debug))
    elif args.debug:
        print(render_interpretation(result))
    else:
        if result.accepted:
            print(
                json.dumps(
                    [
                        {
                            "intent": frame.intent,
                            "combination": frame.combination,
                            "slots": frame.slots,
                            "response_key": frame.response_key,
                        }
                        for frame in result.frames
                    ],
                    indent=2,
                )
            )
        else:
            if result.response is None:
                raise RuntimeError("rejected interpretation has no response")
            print(result.response, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
