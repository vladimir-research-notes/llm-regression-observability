"""Command-line interface for llm-regression-observability (``llmreg``).

Milestone 0 exposes two subcommands so the packaging and entry points can be
exercised end to end:

* ``version``   — print the package version.
* ``check-env`` — a lightweight, offline check that the interpreter and the
  required standard-library modules are present. This is intentionally smaller
  than ``scripts/00_check_environment.py`` (the full host preflight); it exists
  so the CLI itself is testable and self-contained.

Milestone 2 adds ``validate-fixtures``; Milestone 4 adds ``run-suite``; Milestone
6 adds ``compare``. A later milestone adds ``report`` (see ``docs/PLAN.md``). This
module stays dependency-free and calls no network, clock, or randomness in any
scored path.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from . import __summary__, __version__

# Standard-library modules the dependency-free core relies on. Kept in sync with
# ``docs/PLAN.md`` §3 and ``scripts/00_check_environment.py``.
REQUIRED_STDLIB: tuple[str, ...] = (
    "dataclasses",
    "json",
    "argparse",
    "sqlite3",
    "difflib",
    "hashlib",
    "pathlib",
    "statistics",
)
MIN_PYTHON: tuple[int, int] = (3, 12)

# Mirrored here so ``build_parser()`` stays import-light: ``llmreg version`` must
# not pay for the adapter/executor modules. ``tests/test_run_executor.py`` asserts
# these stay in sync with ``adapters.ADAPTER_NAMES`` and ``executor.CostModel``.
ADAPTER_CHOICES: tuple[str, ...] = (
    "mock",
    "recorded",
    "scripted-regression",
    "api-stub",
)
DEFAULT_COST_INPUT_PER_1K = 0.003
DEFAULT_COST_OUTPUT_PER_1K = 0.015


def _import_ok(name: str) -> bool:
    """Return True if ``name`` imports; never raises."""
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _cmd_version(_args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def _cmd_check_env(_args: argparse.Namespace) -> int:
    import sys

    py = sys.version_info
    py_ok = py[:2] >= MIN_PYTHON
    print(
        f"python           : {py.major}.{py.minor}.{py.micro} "
        f"({'ok' if py_ok else f'need >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}'})"
    )

    missing = [m for m in REQUIRED_STDLIB if not _import_ok(m)]
    if missing:
        print(f"stdlib modules   : MISSING {', '.join(missing)}")
    else:
        print(f"stdlib modules   : all {len(REQUIRED_STDLIB)} present")

    print("network required : no (offline, CPU-only)")
    print(f"package version  : {__version__}")

    ok = py_ok and not missing
    print(f"result           : {'PASS' if ok else 'FAIL'}")
    if ok:
        print(
            "note             : full host preflight -> "
            "python3 scripts/00_check_environment.py"
        )
    return 0 if ok else 1


def _cmd_validate_fixtures(args: argparse.Namespace) -> int:
    # Imported lazily so ``version`` / ``check-env`` do not pay for it.
    from .datasets.validation import validate_fixtures

    report = validate_fixtures(args.path)
    for suite in report.suites_checked:
        print(f"suite            : {suite} (ok)")
    if not report.suites_checked:
        print("suite            : none found")
    print(
        f"cases            : {report.case_count} "
        f"({report.defect_count} with injected defects, "
        f"{report.control_count} controls)"
    )
    if report.issues:
        print(f"issues           : {len(report.issues)}")
        for issue in report.issues:
            print(f"  - {issue.render()}")
        print("result           : FAIL")
        return 1
    print("secrets scan     : clean (no credential-like patterns)")
    print("result           : PASS")
    return 0


def _cmd_run_suite(args: argparse.Namespace) -> int:
    # Imported lazily so the cheap subcommands do not pay for the adapters.
    from .executor import CostModel, ExecutorError, RunConfig, run_suite
    from .schemas import SchemaError

    config = RunConfig(
        suite_path=Path(args.suite),
        adapter=args.adapter,
        app_version=args.app_version,
        run_id=args.run_id,
        model_id=args.model_id,
        prompt_version=args.prompt_version,
        tool_version=args.tool_version,
        timestamp=args.timestamp,
        cost_model=CostModel(
            input_usd_per_1k=args.cost_input_per_1k,
            output_usd_per_1k=args.cost_output_per_1k,
        ),
        recompute_cost=args.recompute_cost,
        fixtures_path=Path(args.fixtures) if args.fixtures else None,
        strict=not args.continue_on_error,
    )
    out_root = None if args.dry_run else Path(args.out)

    try:
        result = run_suite(config, out_root)
    except (ExecutorError, SchemaError, OSError) as exc:
        print(f"error            : {exc}")
        print("result           : FAIL")
        return 1

    meta = result.meta
    print(f"run id           : {meta.run_id}")
    print(f"suite            : {meta.suite_id} ({meta.suite_path})")
    print(f"adapter          : {meta.adapter}")
    print(f"app version      : {meta.app_version}")
    print(f"model id         : {meta.model_id}")
    print(f"records          : {meta.case_count}")
    print(
        f"tokens           : {meta.input_tokens_total} in, "
        f"{meta.output_tokens_total} out "
        f"({meta.tokens_estimated_cases} estimated by executor)"
    )
    print(
        f"cost estimate    : ${meta.cost_total_usd:.6f} "
        f"({meta.cost_estimated_cases} estimated by executor)"
    )
    print(f"latency total    : {meta.latency_ms_total:.1f} ms (recorded, not measured)")
    print(f"tool calls       : {meta.tool_call_count}")
    print(f"retrieval sources: {meta.retrieval_source_count}")
    print(
        f"errors           : {meta.error_count} "
        f"(app {meta.app_error_count}, infra {meta.infra_error_count})"
    )
    print(f"sha256(run.jsonl): {meta.records_sha256}")
    if result.run_dir is None:
        print("artifacts        : none written (--dry-run)")
    else:
        print(f"run records      : {result.run_path}")
        print(f"run metadata     : {result.meta_path}")
    print("result           : PASS")
    return 0


#: ``compare --fail-on``: the weakest severity that makes the process exit 1.
#: ``none`` reports without failing, which is the default: a comparison is an
#: observation, and only a caller (a CI gate) decides what is unacceptable.
FAIL_ON_CHOICES: tuple[str, ...] = ("none", "warn", "critical")


def _cmd_compare(args: argparse.Namespace) -> int:
    # Imported lazily so the cheap subcommands do not pay for the evaluators.
    from .comparison import ComparisonError, compare_runs
    from .comparison.regression import SEVERITY_ORDER
    from .schemas import SchemaError

    out_root = None if args.dry_run else Path(args.out)
    try:
        result = compare_runs(
            Path(args.baseline),
            Path(args.candidate),
            out_root,
            comparison_id=args.comparison_id,
        )
    except (ComparisonError, SchemaError, OSError) as exc:
        print(f"error            : {exc}")
        print("result           : FAIL")
        return 1

    meta, summary = result.meta, result.summary
    print(f"comparison id    : {meta.comparison_id}")
    print(f"suite            : {meta.suite_id} ({meta.suite_path})")
    print(
        f"baseline         : {meta.baseline_run_id} "
        f"({meta.baseline_version}, {meta.baseline_adapter})"
    )
    print(
        f"candidate        : {meta.candidate_run_id} "
        f"({meta.candidate_version}, {meta.candidate_adapter})"
    )
    print(f"cases paired     : {meta.paired_case_count}")
    unpaired = meta.unpaired_baseline_case_ids + meta.unpaired_candidate_case_ids
    print(
        f"cases unpaired   : {len(unpaired)}"
        + (f" ({', '.join(sorted(unpaired))})" if unpaired else "")
    )
    print(
        f"cases regressed  : {summary.regressed_case_count} "
        f"({meta.flag_count} flags: {meta.critical_count} critical, "
        f"{meta.warn_count} warn)"
    )
    print()
    for record in result.records:
        flags = ", ".join(
            f"{flag.category}/{flag.severity}" for flag in record.regression_flags
        )
        print(f"  {record.case_id:24} {flags or '-'}")
    print()

    for category in sorted(summary.flags_by_category):
        print(f"  {category:32} {summary.flags_by_category[category]}")
    if not summary.flags_by_category:
        print("  (no regression flags)")
    print()
    print(
        f"latency p50/p95  : {meta.baseline_latency_p50:.1f} -> "
        f"{meta.candidate_latency_p50:.1f} ms / {meta.baseline_latency_p95:.1f} -> "
        f"{meta.candidate_latency_p95:.1f} ms "
        f"(delta {summary.latency_p50_delta:+.1f} / {summary.latency_p95_delta:+.1f})"
    )
    if meta.cost_comparable:
        print(
            f"cost total       : ${meta.baseline_cost_total_usd:.6f} -> "
            f"${meta.candidate_cost_total_usd:.6f} "
            f"(delta ${summary.cost_total_delta:+.6f}; estimate, not a bill)"
        )
    else:
        print(
            "cost total       : not compared "
            "(the two runs used different cost models)"
        )
    print(
        f"infra-attributed : {len(meta.infra_attributed_case_ids)} case(s) "
        "surfaced, excluded from flags"
    )
    print(f"clusters         : {len(summary.clusters)}")
    for cluster in summary.clusters:
        print(f"  {cluster.cluster_id:52} {cluster.label}")
    for note in meta.comparability_notes:
        print(f"note             : {note}")

    print(f"sha256(records)  : {meta.records_sha256}")
    if result.out_dir is None:
        print("artifacts        : none written (--dry-run)")
    else:
        print(f"comparison       : {result.comparison_path}")
        print(f"summary          : {result.summary_path}")
        print(f"metadata         : {result.meta_path}")

    if args.fail_on != "none":
        floor = SEVERITY_ORDER[args.fail_on]
        breaching = [
            flag
            for record in result.records
            for flag in record.regression_flags
            if SEVERITY_ORDER[flag.severity] >= floor
        ]
        if breaching:
            print(
                f"result           : FAIL ({len(breaching)} flag(s) at or above "
                f"{args.fail_on})"
            )
            return 1
    print("result           : PASS")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the ``llmreg`` argument parser."""
    parser = argparse.ArgumentParser(prog="llmreg", description=__summary__)
    parser.add_argument(
        "--version",
        action="version",
        version=f"llmreg {__version__}",
        help="print the package version and exit",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_version = sub.add_parser("version", help="print the package version")
    p_version.set_defaults(func=_cmd_version)

    p_env = sub.add_parser(
        "check-env",
        help="offline check of the interpreter and required stdlib modules",
    )
    p_env.set_defaults(func=_cmd_check_env)

    p_fixtures = sub.add_parser(
        "validate-fixtures",
        help="validate the synthetic fixture suites (schemas, ground truth, safety)",
    )
    p_fixtures.add_argument(
        "path",
        nargs="?",
        default=None,
        help="path to evals/fixtures (or the repo root); default: repo fixtures",
    )
    p_fixtures.set_defaults(func=_cmd_validate_fixtures)

    p_run = sub.add_parser(
        "run-suite",
        help="run a suite through an adapter and write results/runs/<run_id>/",
    )
    p_run.add_argument(
        "--suite",
        required=True,
        help="path to a suite manifest, e.g. evals/suites/smoke.yaml",
    )
    p_run.add_argument(
        "--adapter",
        default="recorded",
        choices=sorted(ADAPTER_CHOICES),
        help="adapter to drive (default: recorded; api-stub is inert)",
    )
    p_run.add_argument(
        "--app-version",
        default="baseline",
        help="label for the version under test (default: baseline)",
    )
    p_run.add_argument("--out", default="results/runs", help="output root directory")
    p_run.add_argument(
        "--run-id",
        default=None,
        help="run id / output directory (default: <suite>-<app-version>-<adapter>)",
    )
    p_run.add_argument(
        "--model-id", default=None, help="override the model id on each record"
    )
    p_run.add_argument("--prompt-version", default=None, help="override prompt_version")
    p_run.add_argument("--tool-version", default=None, help="override tool_version")
    p_run.add_argument(
        "--timestamp",
        default=None,
        help="recorded ISO-8601 timestamp (an input, never the ambient clock)",
    )
    p_run.add_argument(
        "--cost-input-per-1k",
        type=float,
        default=DEFAULT_COST_INPUT_PER_1K,
        help=f"synthetic input price per 1k tokens "
        f"(default: {DEFAULT_COST_INPUT_PER_1K})",
    )
    p_run.add_argument(
        "--cost-output-per-1k",
        type=float,
        default=DEFAULT_COST_OUTPUT_PER_1K,
        help=f"synthetic output price per 1k tokens "
        f"(default: {DEFAULT_COST_OUTPUT_PER_1K})",
    )
    p_run.add_argument(
        "--recompute-cost",
        action="store_true",
        help="price every record with the cost model, ignoring adapter cost",
    )
    p_run.add_argument(
        "--fixtures", default=None, help="fixtures root (default: the repo fixtures)"
    )
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="execute and summarize without writing any artifact",
    )
    p_run.add_argument(
        "--continue-on-error",
        action="store_true",
        help="record an infra error for a failing case instead of aborting the run",
    )
    p_run.set_defaults(func=_cmd_run_suite)

    p_compare = sub.add_parser(
        "compare",
        help="pair a baseline run against a candidate run and raise regression flags",
    )
    p_compare.add_argument(
        "--baseline",
        required=True,
        help="baseline run directory, e.g. results/runs/smoke-baseline-recorded",
    )
    p_compare.add_argument(
        "--candidate", required=True, help="candidate run directory"
    )
    p_compare.add_argument(
        "--out", default="results/comparisons", help="output root directory"
    )
    p_compare.add_argument(
        "--comparison-id",
        default=None,
        help="output directory name (default: <suite>-<baseline>-vs-<candidate>)",
    )
    p_compare.add_argument(
        "--dry-run",
        action="store_true",
        help="compare and summarize without writing any artifact",
    )
    p_compare.add_argument(
        "--fail-on",
        default="none",
        choices=FAIL_ON_CHOICES,
        help="exit 1 when a flag reaches this severity (default: none — report only)",
    )
    p_compare.set_defaults(func=_cmd_compare)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code (0 on success)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())
