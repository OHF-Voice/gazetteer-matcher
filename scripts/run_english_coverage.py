#!/usr/bin/env python3
"""Measure gazetteer-matcher coverage of intent-sentences English tests.

By default, this measures only fallback sentences: cases not correctly handled
by the lean ``speech_to_phrase`` HassIL template subset. This is an
informational runner, so uncovered sentences do not make the process fail.
Files whose slot combination declares ``wildcard_slots`` are excluded.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any, Iterable

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from gazetteer_matcher import GazetteerMatcher  # noqa: E402


DEFAULT_TESTS_DIR = Path.home() / "opt" / "intent-sentences" / "tests"
DEFAULT_HASSIL_DIR = Path.home() / "opt" / "hassil"


@dataclass(frozen=True)
class SentenceCase:
    path: Path
    intent: str
    combination: str
    sentence: str
    expected_slots: dict[str, Any]


@dataclass(frozen=True)
class CaseResult:
    case: SentenceCase
    status: str
    detail: str

    @property
    def covered(self) -> bool:
        return self.status == "covered"


def _slug(value: str) -> str:
    """Mirror intent-sentences' synthesized fixture identifiers."""
    return value.strip().casefold().replace(" ", "_")


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file_obj:
        return yaml.safe_load(file_obj) or {}


def _flatten_grouped_values(value: Any) -> frozenset[str]:
    if not value:
        return frozenset()
    if isinstance(value, list):
        return frozenset(str(item) for item in value)
    if isinstance(value, dict):
        return frozenset(
            str(item)
            for items in value.values()
            if isinstance(items, list)
            for item in items
        )
    return frozenset({str(value)})


def load_cases(
    tests_dir: Path,
    intents: dict[str, Any],
    intent_filter: set[str] | None = None,
) -> tuple[list[SentenceCase], list[dict[str, Any]], int, int]:
    """Load non-wildcard English cases and their self-contained fixtures."""
    english_dir = tests_dir / "en"
    if not english_dir.is_dir():
        raise FileNotFoundError(f"English tests directory not found: {english_dir}")

    cases: list[SentenceCase] = []
    fixture_docs: list[dict[str, Any]] = []
    excluded_files = 0
    excluded_sentences = 0

    for path in sorted(english_dir.glob("*/*.yaml")):
        intent = path.parent.name
        if intent_filter is not None and intent not in intent_filter:
            continue
        combination = path.stem
        combo_info = (
            ((intents.get(intent) or {}).get("slot_combinations") or {}).get(
                combination
            )
        )
        if combo_info is None:
            raise ValueError(
                f"No intents.yaml combination for {intent}.{combination} ({path})"
            )

        test_doc = _load_yaml(path)
        test_groups = test_doc.get("tests") or []
        sentence_count = sum(
            len(group.get("sentences") or []) for group in test_groups
        )
        if combo_info.get("wildcard_slots"):
            excluded_files += 1
            excluded_sentences += sentence_count
            continue

        fixture_docs.append(test_doc)
        inferred_domains = _flatten_grouped_values(
            combo_info.get("inferred_domains")
        )
        for group in test_groups:
            expected_slots = dict(group.get("slots") or {})
            if inferred_domains:
                # The upstream harness accepts any domain licensed by this
                # combination, rather than requiring a particular group.
                expected_slots["domain"] = inferred_domains
            for sentence in group.get("sentences") or []:
                cases.append(
                    SentenceCase(
                        path=path,
                        intent=intent,
                        combination=combination,
                        sentence=str(sentence),
                        expected_slots=expected_slots,
                    )
                )

    return cases, fixture_docs, excluded_files, excluded_sentences


def build_home(fixtures: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Combine self-contained test fixtures into one matcher gazetteer."""
    floors: dict[str, dict[str, Any]] = {}
    areas: dict[str, dict[str, Any]] = {}
    entities: dict[str, dict[str, Any]] = {}

    for fixture in fixtures:
        for floor in fixture.get("floors") or []:
            floor_id = _slug(str(floor["name"]))
            floors.setdefault(
                floor_id,
                {"name": str(floor["name"]), "aliases": []},
            )

        for area in fixture.get("areas") or []:
            area_id = _slug(str(area["name"]))
            area_spec: dict[str, Any] = {
                "name": str(area["name"]),
                "aliases": [],
            }
            if area.get("floor"):
                area_spec["floor"] = _slug(str(area["floor"]))
            existing = areas.setdefault(area_id, area_spec)
            if area_spec.get("floor") and not existing.get("floor"):
                existing["floor"] = area_spec["floor"]

        for entity in fixture.get("entities") or []:
            domain = str(entity["domain"])
            name = str(entity["name"])
            entity_id = f"{domain}.{_slug(name)}"
            entity_spec: dict[str, Any] = {
                "name": name,
                "aliases": [],
                "domain": domain,
            }
            if entity.get("area"):
                entity_spec["area"] = _slug(str(entity["area"]))
            attributes = entity.get("attributes") or {}
            if attributes.get("device_class"):
                entity_spec["device_class"] = str(attributes["device_class"])
            existing = entities.setdefault(entity_id, entity_spec)
            for key in ("area", "device_class"):
                if entity_spec.get(key) and not existing.get(key):
                    existing[key] = entity_spec[key]

    # Derive entity floors just as a Home Assistant export would.
    for entity in entities.values():
        area = areas.get(str(entity.get("area")))
        if area and area.get("floor"):
            entity["floor"] = area["floor"]

    return {"floors": floors, "areas": areas, "entities": entities}


def _merge_yaml_section(
    target: dict[str, Any], paths: Iterable[Path], section: str
) -> None:
    for path in paths:
        doc = _load_yaml(path)
        target.update(doc.get(section) or {})


def _resolve_domain_context(
    block: dict[str, Any], combo_info: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    slots = dict(block.get("slots") or {})
    requires_context = dict(block.get("requires_context") or {})
    name_domains = block.get("name_domains")
    if name_domains:
        if isinstance(name_domains, str):
            name_domains = combo_info["name_domain_groups"][name_domains]
        requires_context["domain"] = name_domains
    elif inferred_domain := block.get("inferred_domain"):
        slots["domain"] = inferred_domain
    if combo_info.get("context_area"):
        requires_context["area"] = {"slot": True}
    return slots, requires_context


def filter_speech_to_phrase_cases(
    cases: list[SentenceCase],
    intents_schema: dict[str, Any],
    tests_dir: Path,
    hassil_dir: Path,
) -> tuple[list[SentenceCase], int]:
    """Keep cases correctly recognized by the lean HassIL template subset."""
    hassil_root = hassil_dir.expanduser().resolve()
    if str(hassil_root) not in sys.path:
        sys.path.insert(0, str(hassil_root))
    try:
        from hassil import Intents, TextSlotList, recognize_best
    except ImportError as err:
        raise RuntimeError(f"Unable to import HassIL from {hassil_root}") from err

    repo_root = tests_dir.parent
    sentences_dir = repo_root / "sentences" / "en"
    grammar: dict[str, Any] = {
        "language": "en",
        "intents": {},
        "lists": {},
        "expansion_rules": {},
        "skip_words": [],
    }
    common_path = sentences_dir / "_common.yaml"
    if common_path.exists():
        common = _load_yaml(common_path)
        grammar["skip_words"] = common.get("skip_words") or []
        grammar["settings"] = common.get("settings") or {}
    _merge_yaml_section(
        grammar["expansion_rules"],
        sorted((repo_root / "rules" / "en").glob("*.yaml")),
        "expansion_rules",
    )
    _merge_yaml_section(
        grammar["lists"],
        [
            *sorted((repo_root / "lists").glob("*.yaml")),
            *sorted((repo_root / "lists" / "en").glob("*.yaml")),
        ],
        "lists",
    )

    tagged_blocks = 0
    total_tagged_blocks = 0
    case_intents = {case.intent for case in cases}
    for intent_name, intent_info in intents_schema.items():
        data: list[dict[str, Any]] = []
        for combo_name, combo_info in (
            intent_info.get("slot_combinations") or {}
        ).items():
            path = sentences_dir / intent_name / f"{combo_name}.yaml"
            if not path.exists():
                continue
            for block in _load_yaml(path).get("data") or []:
                if not block.get("speech_to_phrase"):
                    continue
                total_tagged_blocks += 1
                if intent_name in case_intents:
                    tagged_blocks += 1
                slots, requires_context = _resolve_domain_context(block, combo_info)
                data.append(
                    {
                        "sentences": block["sentences"],
                        "slots": slots,
                        "requires_context": requires_context,
                        "response": block.get("response"),
                        "metadata": {"slot_combination": combo_name},
                    }
                )
        if data:
            grammar["intents"][intent_name] = {"data": data}

    if not total_tagged_blocks:
        raise RuntimeError(f"No speech_to_phrase blocks found under {sentences_dir}")
    lean_intents = Intents.from_dict(grammar)

    fixture_cache: dict[Path, tuple[dict[str, Any], str]] = {}
    selected: list[SentenceCase] = []
    for case in cases:
        cached = fixture_cache.get(case.path)
        if cached is None:
            test_doc = _load_yaml(case.path)
            slot_lists = {
                "name": TextSlotList.from_tuples(
                    [
                        (
                            str(entity["name"]),
                            str(entity["name"]),
                            {"domain": str(entity["domain"])},
                            {"domain": str(entity["domain"])},
                        )
                        for entity in test_doc.get("entities") or []
                    ],
                    name="name",
                ),
                "area": TextSlotList.from_strings(
                    [str(area["name"]) for area in test_doc.get("areas") or []],
                    name="area",
                ),
                "floor": TextSlotList.from_strings(
                    [str(floor["name"]) for floor in test_doc.get("floors") or []],
                    name="floor",
                ),
            }
            context_areas = [
                str(area["name"])
                for area in test_doc.get("areas") or []
                if area.get("context_area")
            ]
            context_area = context_areas[0] if context_areas else "__context_area__"
            cached = (slot_lists, context_area)
            fixture_cache[case.path] = cached
        slot_lists, context_area = cached
        result = recognize_best(
            case.sentence,
            lean_intents,
            slot_lists=slot_lists,
            intent_context={"area": context_area},
            best_slot_name="name",
        )
        if result is None or result.intent.name != case.intent:
            continue
        if result.intent_metadata is None or (
            result.intent_metadata.get("slot_combination") != case.combination
        ):
            continue
        actual_slots = {
            entity_name: entity.value
            for entity_name, entity in result.entities.items()
        }
        combo_info = intents_schema[case.intent]["slot_combinations"][
            case.combination
        ]
        if combo_info.get("context_area"):
            actual_slots.pop("area", None)
        if _slots_match(actual_slots, case.expected_slots):
            selected.append(case)
    return selected, tagged_blocks


def canonical_expected_slots(
    slots: dict[str, Any], home: dict[str, Any]
) -> dict[str, Any]:
    entity_ids_by_name: dict[str, str] = {
        str(spec["name"]).casefold(): entity_id
        for entity_id, spec in home["entities"].items()
    }
    result = dict(slots)
    if isinstance(result.get("name"), str):
        name = result["name"]
        result["name"] = entity_ids_by_name.get(name.casefold(), name)
    if isinstance(result.get("area"), str):
        result["area"] = _slug(result["area"])
    if isinstance(result.get("floor"), str):
        result["floor"] = _slug(result["floor"])
    return result


def _slot_values_match(actual: Any, expected: Any) -> bool:
    if isinstance(expected, (set, frozenset)):
        return actual in expected
    return actual == expected


def _slots_match(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    return actual.keys() == expected.keys() and all(
        _slot_values_match(actual[name], expected[name]) for name in expected
    )


def run_case(
    matcher: GazetteerMatcher, case: SentenceCase, home: dict[str, Any]
) -> CaseResult:
    expected_slots = canonical_expected_slots(case.expected_slots, home)
    result = matcher.interpret(case.sentence)
    if not result.accepted:
        status = "ambiguous" if result.ambiguous else "rejected"
        return CaseResult(case, status, result.reason or status)
    if len(result.frames) != 1:
        return CaseResult(case, "frame_count", f"got {len(result.frames)} frames")

    frame = result.frames[0]
    if frame.intent != case.intent:
        return CaseResult(case, "wrong_intent", f"got {frame.intent}")
    if frame.combination != case.combination:
        return CaseResult(
            case,
            "wrong_combination",
            f"got {frame.intent}.{frame.combination}",
        )
    if not _slots_match(frame.slots, expected_slots):
        return CaseResult(
            case,
            "wrong_slots",
            f"expected {expected_slots!r}, got {frame.slots!r}",
        )
    return CaseResult(case, "covered", "")


def _percent(covered: int, total: int) -> float:
    return (100.0 * covered / total) if total else 100.0


def make_report(
    results: list[CaseResult], excluded_files: int, excluded_sentences: int
) -> dict[str, Any]:
    by_intent: dict[str, Counter[str]] = defaultdict(Counter)
    by_combination: dict[str, Counter[str]] = defaultdict(Counter)
    statuses: Counter[str] = Counter()
    for result in results:
        statuses[result.status] += 1
        by_intent[result.case.intent][result.status] += 1
        key = f"{result.case.intent}.{result.case.combination}"
        by_combination[key][result.status] += 1

    total = len(results)
    covered = statuses["covered"]

    def rows(counters: dict[str, Counter[str]]) -> list[dict[str, Any]]:
        output = []
        for name, counts in sorted(counters.items()):
            row_total = sum(counts.values())
            row_covered = counts["covered"]
            output.append(
                {
                    "name": name,
                    "covered": row_covered,
                    "total": row_total,
                    "coverage_percent": round(
                        _percent(row_covered, row_total), 2
                    ),
                }
            )
        return output

    return {
        "summary": {
            "covered": covered,
            "total": total,
            "coverage_percent": round(_percent(covered, total), 2),
            "eligible_files": len(by_combination),
            "excluded_wildcard_files": excluded_files,
            "excluded_wildcard_sentences": excluded_sentences,
            "statuses": dict(sorted(statuses.items())),
        },
        "by_intent": rows(by_intent),
        "by_combination": rows(by_combination),
    }


def render_text(
    report: dict[str, Any],
    results: list[CaseResult],
    tests_dir: Path,
    failure_limit: int,
) -> str:
    summary = report["summary"]
    selection = report.get("selection")
    title = "English non-wildcard sentence coverage"
    if selection and selection["mode"] == "fallback":
        title += " (gazetteer fallback cohort)"
    elif selection and selection["mode"] == "speech_to_phrase":
        title += " (HassIL speech-to-phrase cohort)"
    lines = [
        title,
        f"Tests: {tests_dir / 'en'}",
        (
            f"Covered: {summary['covered']}/{summary['total']} "
            f"({summary['coverage_percent']:.2f}%)"
        ),
        (
            f"Files: {summary['eligible_files']} eligible, "
            f"{summary['excluded_wildcard_files']} wildcard excluded "
            f"({summary['excluded_wildcard_sentences']} sentences)"
        ),
        "",
        "Outcomes:",
    ]
    if selection:
        if selection["mode"] == "fallback":
            selection_line = (
                f"Fallback cohort: {selection['selected_sentences']}/"
                f"{selection['eligible_sentences']} eligible sentences; "
                f"excluded {selection['speech_to_phrase_sentences']} handled "
                f"by {selection['tagged_blocks']} lean HassIL blocks"
            )
        else:
            selection_line = (
                f"Lean HassIL cohort: {selection['selected_sentences']}/"
                f"{selection['eligible_sentences']} eligible sentences, "
                f"{selection['tagged_blocks']} tagged blocks"
            )
        lines.insert(
            4,
            selection_line,
        )
    for status, count in summary["statuses"].items():
        lines.append(f"  {status:<18} {count:>5}")

    lines.extend(["", "Coverage by intent:", "  intent                              covered   total    coverage"])
    for row in report["by_intent"]:
        lines.append(
            f"  {row['name']:<35} {row['covered']:>7} "
            f"{row['total']:>7} {row['coverage_percent']:>10.2f}%"
        )

    failures = [result for result in results if not result.covered]
    if failure_limit > 0 and failures:
        shown = failures[:failure_limit]
        lines.extend(["", f"Failure samples (first {len(shown)} of {len(failures)}):"])
        english_dir = tests_dir / "en"
        for result in shown:
            path = result.case.path.relative_to(english_dir)
            lines.append(
                f"  [{result.status}] {path}: {result.case.sentence!r}"
            )
            lines.append(f"    {result.detail}")

    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Measure matcher coverage of non-wildcard English fallback tests."
        )
    )
    parser.add_argument(
        "--tests-dir",
        type=Path,
        default=DEFAULT_TESTS_DIR,
        help="intent-sentences tests directory (default: %(default)s)",
    )
    parser.add_argument(
        "--intents",
        type=Path,
        help="intents.yaml path (default: sibling of --tests-dir)",
    )
    parser.add_argument(
        "--intent",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "only run an intent; repeat for multiple intents "
            "(for example: --intent HassTurnOn --intent HassTurnOff)"
        ),
    )
    parser.add_argument(
        "--show-failures",
        type=int,
        default=20,
        metavar="N",
        help="show the first N uncovered sentences (default: %(default)s)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the coverage report as JSON",
    )
    selection_group = parser.add_mutually_exclusive_group()
    selection_group.add_argument(
        "--speech-to-phrase",
        action="store_true",
        help=(
            "only measure test sentences correctly recognized by the "
            "speech_to_phrase-tagged HassIL template subset"
        ),
    )
    selection_group.add_argument(
        "--all-sentences",
        action="store_true",
        help=(
            "measure all non-wildcard sentences, including those handled by "
            "the speech_to_phrase subset"
        ),
    )
    parser.add_argument(
        "--hassil-dir",
        type=Path,
        default=DEFAULT_HASSIL_DIR,
        help=(
            "local HassIL checkout used to select fallback or "
            "speech-to-phrase cases (default: %(default)s)"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tests_dir = args.tests_dir.expanduser().resolve()
    intents_path = (
        args.intents.expanduser().resolve()
        if args.intents
        else tests_dir.parent / "intents.yaml"
    )
    intents = _load_yaml(intents_path)
    intent_filter = set(args.intent) or None
    if intent_filter is not None:
        unknown_intents = sorted(intent_filter - intents.keys())
        if unknown_intents:
            names = ", ".join(unknown_intents)
            raise SystemExit(f"Unknown intent name(s): {names}")
    cases, fixtures, excluded_files, excluded_sentences = load_cases(
        tests_dir, intents, intent_filter
    )
    eligible_sentence_count = len(cases)
    tagged_blocks = 0
    speech_to_phrase_cases: list[SentenceCase] = []
    selection_mode = "all"
    if not args.all_sentences:
        speech_to_phrase_cases, tagged_blocks = filter_speech_to_phrase_cases(
            cases,
            intents,
            tests_dir,
            args.hassil_dir,
        )
        if args.speech_to_phrase:
            cases = speech_to_phrase_cases
            selection_mode = "speech_to_phrase"
        else:
            speech_to_phrase_case_ids = {
                id(case) for case in speech_to_phrase_cases
            }
            cases = [
                case for case in cases if id(case) not in speech_to_phrase_case_ids
            ]
            selection_mode = "fallback"
        selected_paths = {case.path for case in cases}
        fixtures = [_load_yaml(path) for path in sorted(selected_paths)]
    home = build_home(fixtures)

    with TemporaryDirectory(prefix="gazetteer-coverage-") as temp_dir:
        home_path = Path(temp_dir) / "home.yaml"
        with home_path.open("w", encoding="utf-8") as file_obj:
            yaml.safe_dump(home, file_obj, sort_keys=True)
        matcher = GazetteerMatcher(
            home_path=home_path,
            intents_path=intents_path,
        )
        results = [run_case(matcher, case, home) for case in cases]

    report = make_report(results, excluded_files, excluded_sentences)
    if selection_mode != "all":
        report["selection"] = {
            "mode": selection_mode,
            "selected_sentences": len(cases),
            "eligible_sentences": eligible_sentence_count,
            "speech_to_phrase_sentences": len(speech_to_phrase_cases),
            "tagged_blocks": tagged_blocks,
        }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            render_text(
                report,
                results,
                tests_dir,
                max(0, args.show_failures),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
