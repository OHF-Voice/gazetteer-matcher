from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .debug import render_interpretation, render_json, render_spans
from .matcher import GazetteerMatcher


def _add_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--vocabulary", type=Path, help="Override vocabulary.yaml")
    parser.add_argument("--home", type=Path, help="Override home.yaml")
    parser.add_argument("--responses", type=Path, help="Override responses.yaml")


def _matcher(args: argparse.Namespace) -> GazetteerMatcher:
    return GazetteerMatcher(
        vocabulary_path=args.vocabulary,
        home_path=args.home,
        responses_path=args.responses,
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
    match_parser.add_argument("--json", action="store_true", help="Emit JSON")
    _add_paths(match_parser)

    spans_parser = subparsers.add_parser("spans", help="Show lexical spans only")
    spans_parser.add_argument("text")
    _add_paths(spans_parser)

    support_parser = subparsers.add_parser(
        "support", help="Report intent-metadata combination coverage"
    )
    _add_paths(support_parser)

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
