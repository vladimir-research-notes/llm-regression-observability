"""Command-line interface for llm-regression-observability (``llmreg``).

Milestone 0 exposes two subcommands so the packaging and entry points can be
exercised end to end:

* ``version``   — print the package version.
* ``check-env`` — a lightweight, offline check that the interpreter and the
  required standard-library modules are present. This is intentionally smaller
  than ``scripts/00_check_environment.py`` (the full host preflight); it exists
  so the CLI itself is testable and self-contained.

Milestone 2 adds ``validate-fixtures``; Milestone 4 adds ``run-suite``; Milestone
6 adds ``compare``; Milestone 7 adds ``report``; Milestone 8 adds ``aggregate``;
Milestone 9 adds ``scan-secrets``; Milestone 10 adds ``import-trace`` and
``export-run``; Milestone 11 adds ``estimate-cost``. This module stays
dependency-free and calls no network, clock, or randomness in any scored path.

**No subcommand here can spend money.** ``estimate-cost`` is a calculator, and
``run-suite --adapter api-stub`` builds the API adapter with its default,
opted-out guard (``executor.build_adapter``), which refuses before it reaches a
cache, a transport, or a provider. Opting in is a programmatic act; see
``docs/API_ADAPTER.md``.
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

# Mirrored from ``reports.FORMATS`` for the same reason. ``both`` expands to the
# two real formats. ``tests/test_reports.py`` asserts they stay in sync.
REPORT_FORMAT_CHOICES: tuple[str, ...] = ("markdown", "html", "both")
DEFAULT_REPORT_OUT = "results/reports/regression_report.md"
DEFAULT_REPORT_TOP = 10


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


def _cmd_scan_secrets(args: argparse.Namespace) -> int:
    """Repository hygiene: does any committed file hold a credential-like string?"""
    # Imported lazily so ``version`` / ``check-env`` do not pay for it.
    from .datasets import repo_root
    from .traces.redaction import ALLOW_MARKER, CERTAIN_RULES, scan_tree

    root = Path(args.path) if args.path else repo_root()
    if not root.is_dir():
        print(f"error            : {root} is not a directory")
        print("result           : FAIL")
        return 1

    findings = scan_tree(root)
    print(f"root             : {root}")
    print(f"rules            : {len(CERTAIN_RULES)} certain-tier credential patterns")
    print(f"allow marker     : a line containing '{ALLOW_MARKER}' is skipped")
    if findings:
        print(f"findings         : {len(findings)}")
        for finding in findings:
            print(f"  - {finding.render()}")
        print("result           : FAIL")
        return 1
    print("findings         : 0 (no credential-like pattern in a committed file)")
    print("result           : PASS")
    return 0


#: ``estimate-cost`` prices a suite it never runs. Kept here rather than on the
#: adapter's default because a CLI default that silently bounds output tokens at
#: some number is a CLI that quietly changes what it quoted you.
DEFAULT_MAX_OUTPUT_TOKENS = 1024


def _cmd_estimate_cost(args: argparse.Namespace) -> int:
    """What would running this suite against a priced model cost? No call, ever.

    This is a calculator, not the adapter: it opts in to nothing, opens no cache,
    and cannot reach a provider. The ceiling it reports is the one the guarded
    adapter would enforce, computed by the same estimator
    (``pricing.PriceList``), so a number printed here and a number a guard
    refuses on cannot disagree.
    """
    # Imported lazily so the cheap subcommands do not pay for the loaders.
    from .datasets import load_eval_suite
    from .pricing import PriceList, PricingError, load_price_list

    try:
        suite = load_eval_suite(Path(args.suite))
    except Exception as exc:  # noqa: BLE001 - any load failure is a clean FAIL
        print(f"error            : {args.suite}: cannot load suite ({exc})")
        print("result           : FAIL")
        return 1

    try:
        pricing = (
            load_price_list(args.pricing)
            if args.pricing
            else PriceList.synthetic(args.model)
        )
        estimates = [
            pricing.estimate_case(
                args.model, case, max_output_tokens=args.max_output_tokens
            )
            for case in suite.cases
        ]
    except PricingError as exc:
        print(f"error            : {exc}")
        print("result           : FAIL")
        return 1

    input_tokens = sum(e.input_tokens for e in estimates)
    output_bound = sum(e.output_tokens for e in estimates)
    total = round(sum(e.cost_usd for e in estimates), 8)
    within = total <= args.max_cost_usd

    verified = "verified" if pricing.verified else "UNVERIFIED"
    print(f"suite            : {suite.suite_id} ({len(suite.cases)} cases)")
    print(f"model            : {args.model}")
    print(f"pricing          : {pricing.source} ({verified})")
    if not pricing.verified:
        print(
            "                   estimates only; the API adapter refuses to spend "
            "against prices nobody has checked"
        )
    print(f"input tokens     : {input_tokens} (estimated, ~4 chars/token)")
    print(f"output tokens    : {output_bound} (upper bound: max_output_tokens)")
    print(f"cost upper bound : ${total:.6f}")
    print(f"max-cost ceiling : ${args.max_cost_usd:.6f}")
    print(f"within ceiling   : {'yes' if within else 'no'}")
    print("api calls        : 0 (this command cannot make one)")
    print(f"result           : {'PASS' if within else 'FAIL'}")
    return 0 if within else 1


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
    print(
        f"redaction        : {meta.redacted_case_count} record(s) masked, "
        f"{meta.case_count - meta.redacted_case_count} clean "
        "(a flagged record refuses the run)"
    )
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


def _cmd_report(args: argparse.Namespace) -> int:
    # Imported lazily so the cheap subcommands do not pay for the report stack.
    from .reports import FORMATS, ReportError, generate_report
    from .schemas import SchemaError

    formats = FORMATS if args.format == "both" else (args.format,)
    out = None if args.dry_run else Path(args.out)
    try:
        result = generate_report(
            args.comparison, out, formats=formats, top=args.top or None
        )
    except (ReportError, SchemaError, OSError) as exc:
        print(f"error            : {exc}")
        print("result           : FAIL")
        return 1

    report = result.report
    print(f"comparison id    : {report.comparison_id}")
    print(f"suite            : {report.suite_id} ({report.suite_path})")
    print(
        f"baseline         : {report.baseline_run_id} "
        f"({report.baseline_version}, {report.baseline_adapter})"
    )
    print(
        f"candidate        : {report.candidate_run_id} "
        f"({report.candidate_version}, {report.candidate_adapter})"
    )
    print(f"verdict          : {report.verdict} — {report.headline}")
    print(f"cases paired     : {report.case_count}")
    print(
        f"flags            : {report.flag_count} "
        f"({report.critical_count} critical, {report.warn_count} warn)"
    )
    print(f"clusters         : {len(report.clusters)}")
    # The gate already ran inside ``generate_report``; reaching here proves it.
    print("redaction        : clean (rendered report holds no credential-like string)")
    if not result.written:
        print("artifacts        : none written (--dry-run)")
    else:
        for fmt in formats:
            print(f"{f'report ({fmt})':<17}: {result.paths[fmt]}")
    print("result           : PASS")
    return 0


#: ``aggregate --out``: where ``aggregate.json`` lands. ``spans.jsonl`` is always
#: written next to ``run.jsonl`` — the spans belong to the run, not to a report.
DEFAULT_AGGREGATE_OUT = "results/processed"


def _cmd_aggregate(args: argparse.Namespace) -> int:
    # Imported lazily so the cheap subcommands do not pay for the evaluators.
    from .evaluators import EvaluatorError
    from .observability import ObservabilityError, aggregate_run_dir
    from .schemas import SchemaError
    from .traces.redaction import RedactionError

    run_dir = Path(args.run)
    out_root = None if args.dry_run else Path(args.out)
    try:
        result = aggregate_run_dir(run_dir, out_root)
    except (
        ObservabilityError,
        EvaluatorError,
        RedactionError,
        SchemaError,
        OSError,
    ) as exc:
        print(f"error            : {exc}")
        print("result           : FAIL")
        return 1

    agg, spans = result.aggregate, result.aggregate.spans
    print(f"run id           : {agg.run_id}")
    print(f"suite            : {agg.suite_id}")
    print(f"adapter          : {agg.adapter} ({agg.app_version})")
    print(f"cases            : {agg.case_count}")
    print(f"metrics          : {agg.metric_count} per-case results")
    print(
        f"spans            : {spans.span_count} over {spans.trace_count} traces, "
        f"depth {spans.max_depth} ({spans.error_span_count} error)"
    )
    for name in sorted(spans.spans_by_name):
        errored = spans.error_spans_by_name.get(name, 0)
        suffix = f" ({errored} error)" if errored else ""
        print(f"  {name:24} {spans.spans_by_name[name]}{suffix}")
    print(
        f"recorded duration: {spans.recorded_duration_ms:.1f} ms "
        "(request spans; child widths are derived, not measured)"
    )
    print()
    print("application metrics")
    for name in agg.application_metrics:
        coverage = agg.metric_coverage.get(name, 0)
        print(
            f"  {name:32} {agg.application_metrics[name]:>12.6f} "
            f"[{coverage}/{agg.case_count} cases]"
        )
    print()
    print("harness metrics (observation quality — never application scores)")
    for name in agg.harness_metrics:
        print(f"  {name:32} {agg.harness_metrics[name]:>12.6f}")
    if agg.placeholder_metrics:
        print()
        print("placeholder metrics (computed, claimed for nothing)")
        for name in agg.placeholder_metrics:
            print(f"  {name:32} {agg.placeholder_metrics[name]:>12.6f}")
    print()
    print(
        f"infra-attributed : {len(agg.infra_attributed_case_ids)} case(s)"
        + (
            f" ({', '.join(agg.infra_attributed_case_ids)})"
            if agg.infra_attributed_case_ids
            else ""
        )
    )
    print(
        f"incomplete traces: {len(agg.incomplete_trace_case_ids)} case(s)"
        + (
            f" ({', '.join(agg.incomplete_trace_case_ids)})"
            if agg.incomplete_trace_case_ids
            else ""
        )
    )
    print(f"sha256(spans)    : {agg.spans_sha256}")
    print(f"sha256(metrics)  : {agg.metrics_sha256}")
    if result.aggregate_path is None:
        print("artifacts        : none written (--dry-run)")
    else:
        print(f"spans            : {result.spans_path}")
        print(f"aggregate        : {result.aggregate_path}")
    print("result           : PASS")
    return 0


#: ``import-trace`` / ``export-run`` defaults (Milestone 10).
DEFAULT_IMPORT_OUT = "results/runs"
DEFAULT_EXPORT_OUT = "results/external"

#: ``--source auto`` means "the envelope names it". A bare source document does not,
#: so it must be named explicitly — sniffing a shape is guessing where guessing costs
#: the most (``datasets/importers/registry.py``).
AUTO_SOURCE = "auto"

#: Mirrored from ``datasets.importers.SOURCES`` so ``build_parser()`` stays
#: import-light: ``llmreg version`` must not pay for the importer registry.
#: ``tests/test_integration_contracts.py`` asserts the two stay in sync.
IMPORT_SOURCE_CHOICES: tuple[str, ...] = (
    "agent_control_bench",
    "bioagentbench",
    "bioscientist_os",
    "claude_api",
    "code_agent_runtime",
    "llmreg",
)


def _cmd_import_trace(args: argparse.Namespace) -> int:
    # Imported lazily so the cheap subcommands do not pay for the executor.
    from .datasets.importers import ImportContext, TraceImportError, import_run
    from .executor import CostModel
    from .schemas import SchemaError

    context = ImportContext(
        app_version=args.app_version,
        prompt_version=args.prompt_version or "",
        tool_version=args.tool_version or "",
        model_id=args.model_id or "",
        timestamp=args.timestamp or "",
        run_id=args.run_id or f"{args.app_version}",
        strict=not args.lenient,
    )
    out_root = None if args.dry_run else Path(args.out)
    try:
        result = import_run(
            Path(args.input),
            Path(args.suite),
            source=None if args.source == AUTO_SOURCE else args.source,
            context=context,
            out_root=out_root,
            cost_model=CostModel(
                input_usd_per_1k=args.cost_input_per_1k,
                output_usd_per_1k=args.cost_output_per_1k,
            ),
            fixtures_path=Path(args.fixtures) if args.fixtures else None,
        )
    except (TraceImportError, SchemaError, OSError) as exc:
        print(f"error            : {exc}")
        print("result           : FAIL")
        return 1

    meta, run = result.meta, result.run.meta
    print(f"document         : {meta.document}")
    print(
        f"source           : {meta.source} "
        f"({meta.source_schema} {meta.source_version})"
    )
    if meta.contract_version:
        print(f"contract         : {meta.contract_version} (envelope)")
        print(f"record schema    : {meta.record_schema}")
    else:
        print("contract         : (bare source document, no envelope)")
    print(f"mode             : {'strict' if meta.strict else 'lenient'}")
    print(f"run id           : {run.run_id}")
    print(f"suite            : {run.suite_id} ({run.suite_path})")
    print(f"adapter          : {run.adapter}")
    print(f"records          : {meta.case_count} ({', '.join(meta.imported_case_ids)})")
    print(
        f"cases from source: {len(meta.cases_from_source)}"
        + ("" if meta.cases_from_source else " (the source carries no task definition)")
    )
    print(
        f"tokens           : {run.input_tokens_total} in, "
        f"{run.output_tokens_total} out "
        f"({run.tokens_estimated_cases} estimated by executor)"
    )
    print(
        f"cost estimate    : ${run.cost_total_usd:.6f} "
        f"({run.cost_estimated_cases} estimated by executor)"
    )
    print(f"latency total    : {run.latency_ms_total:.1f} ms (recorded, not measured)")
    print(
        f"errors           : {run.error_count} "
        f"(app {run.app_error_count}, infra {run.infra_error_count})"
    )
    print(
        f"redaction        : {run.redacted_case_count} record(s) masked "
        "(a flagged record refuses the import)"
    )
    print(
        f"absent fields    : {len(meta.absent_fields)}"
        + (f" ({', '.join(meta.absent_fields)})" if meta.absent_fields else "")
    )
    print(
        f"ignored fields   : {len(meta.ignored_fields)}"
        + (f" ({', '.join(meta.ignored_fields)})" if meta.ignored_fields else "")
    )
    print(
        f"unmapped fields  : {len(meta.unknown_fields)}"
        + (f" ({', '.join(meta.unknown_fields)})" if meta.unknown_fields else "")
    )
    print(f"warnings         : {len(meta.warnings)}")
    for warning in meta.warnings:
        print(f"  - {warning}")
    print(f"sha256(run.jsonl): {run.records_sha256}")
    if result.run_dir is None:
        print("artifacts        : none written (--dry-run)")
    else:
        print(f"run records      : {result.run.run_path}")
        print(f"run metadata     : {result.run.meta_path}")
        print(f"import metadata  : {result.import_meta_path}")
    print("result           : PASS")
    return 0


def _cmd_export_run(args: argparse.Namespace) -> int:
    # Imported lazily so the cheap subcommands do not pay for the loaders.
    from .comparison.baseline import resolve_suite_path
    from .datasets import export_bundle, load_eval_suite, write_bundle
    from .executor import RUN_JSONL, RUN_META_JSON, RunMeta
    from .schemas import RunRecord, SchemaError
    from .traces.storage import read_json, read_jsonl

    run_dir = Path(args.run)
    try:
        meta: RunMeta = read_json(run_dir / RUN_META_JSON, RunMeta)
        records = read_jsonl(run_dir / RUN_JSONL, RunRecord)
    except (SchemaError, OSError) as exc:
        print(f"error            : {run_dir}: not a run directory ({exc})")
        print("result           : FAIL")
        return 1

    cases = []
    if not args.no_cases:
        try:
            cases = load_eval_suite(resolve_suite_path(meta)).cases
        except Exception as exc:  # noqa: BLE001 - suite resolution is best effort
            print(
                f"note             : suite not resolvable ({exc}); "
                "exporting records only"
            )

    # A recorded input, never the clock: the run's own timestamp unless overridden.
    exported_at = args.exported_at or meta.timestamp
    bundle = export_bundle(
        records,
        exported_at=exported_at,
        suite_id=meta.suite_id,
        app_version=meta.app_version,
        cases=cases or None,
        notes=[
            f"exported from run {meta.run_id!r} (adapter {meta.adapter!r})",
            "all fixtures and traces in this repository are synthetic",
        ],
    )
    default_out = Path(DEFAULT_EXPORT_OUT) / f"{meta.run_id}.json"
    out = Path(args.out) if args.out else default_out

    print(f"run id           : {meta.run_id}")
    print(f"suite            : {meta.suite_id}")
    print(f"contract         : {bundle.contract_version}")
    print(f"source           : {bundle.source} ({bundle.source_schema})")
    print(f"exported at      : {bundle.exported_at} (recorded input, not the clock)")
    print(f"records          : {len(records)}")
    print(f"cases            : {len(cases)}")
    print(f"record schema    : {bundle.record_schema}")
    print(f"sha256(payload)  : {bundle.payload_sha256}")
    if args.dry_run:
        print("artifacts        : none written (--dry-run)")
    else:
        write_bundle(out, bundle)
        print(f"bundle           : {out}")
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

    p_scan = sub.add_parser(
        "scan-secrets",
        help="scan a source tree for accidentally committed credential-like strings",
    )
    p_scan.add_argument(
        "path",
        nargs="?",
        default=None,
        help="directory to scan; default: the repository root",
    )
    p_scan.set_defaults(func=_cmd_scan_secrets)

    p_estimate = sub.add_parser(
        "estimate-cost",
        help="price a suite against a model without running it (no API call)",
        description="Upper-bound the cost of running a suite against a priced "
        "model. Makes no API call and needs no opt-in: it reads the suite text "
        "and a price list and does arithmetic. Exits 1 if the bound exceeds "
        "--max-cost-usd, so it can gate a job before the job can spend.",
    )
    p_estimate.add_argument(
        "--suite",
        required=True,
        help="path to a suite manifest, e.g. evals/suites/smoke.yaml",
    )
    p_estimate.add_argument(
        "--model",
        default="api-model",
        help="model id to price against; must appear in the price list",
    )
    p_estimate.add_argument(
        "--pricing",
        default=None,
        help="price-list JSON (see config/api_pricing.example.json); default: "
        "the synthetic placeholder prices",
    )
    p_estimate.add_argument(
        "--max-output-tokens",
        type=int,
        default=DEFAULT_MAX_OUTPUT_TOKENS,
        help=f"output-token bound each call is priced at (default: "
        f"{DEFAULT_MAX_OUTPUT_TOKENS}). A bound, not a prediction",
    )
    p_estimate.add_argument(
        "--max-cost-usd",
        type=float,
        default=0.0,
        help="ceiling to compare the bound against (default: 0.0 — nothing is "
        "affordable, which is the safe value to forget to set)",
    )
    p_estimate.set_defaults(func=_cmd_estimate_cost)

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

    p_aggregate = sub.add_parser(
        "aggregate",
        help="project a run into spans and reduce it to suite-level metrics",
    )
    p_aggregate.add_argument(
        "--run",
        required=True,
        help="run directory written by `llmreg run-suite`, "
        "e.g. results/runs/smoke-baseline-recorded",
    )
    p_aggregate.add_argument(
        "--out",
        default=DEFAULT_AGGREGATE_OUT,
        help=(
            f"root for aggregate.json (default: {DEFAULT_AGGREGATE_OUT}). "
            "spans.jsonl is always written next to run.jsonl"
        ),
    )
    p_aggregate.add_argument(
        "--dry-run",
        action="store_true",
        help="aggregate and summarize without writing any artifact",
    )
    p_aggregate.set_defaults(func=_cmd_aggregate)

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

    p_report = sub.add_parser(
        "report",
        help="render a comparison as a Markdown / self-contained HTML report",
    )
    p_report.add_argument(
        "--comparison",
        required=True,
        help="comparison directory written by `llmreg compare`, "
        "e.g. results/comparisons/smoke-baseline-vs-candidate",
    )
    p_report.add_argument(
        "--out",
        default=DEFAULT_REPORT_OUT,
        help=(
            f"output file (or directory); default: {DEFAULT_REPORT_OUT}. "
            "The suffix is set from --format"
        ),
    )
    p_report.add_argument(
        "--format",
        default="markdown",
        choices=REPORT_FORMAT_CHOICES,
        help="output format (default: markdown; 'both' writes .md and .html)",
    )
    p_report.add_argument(
        "--top",
        type=int,
        default=DEFAULT_REPORT_TOP,
        help=(
            f"rows in the worst-regressions table (default: {DEFAULT_REPORT_TOP}; "
            "0 shows every flag)"
        ),
    )
    p_report.add_argument(
        "--dry-run",
        action="store_true",
        help="render and redaction-check without writing any artifact",
    )
    p_report.set_defaults(func=_cmd_report)

    p_import = sub.add_parser(
        "import-trace",
        help="import an external trace against a suite and write "
        "results/runs/<run_id>/",
    )
    p_import.add_argument(
        "--input",
        required=True,
        help="trace document: a sealed llmreg bundle, or a bare source document",
    )
    p_import.add_argument(
        "--suite",
        required=True,
        help="suite the trace answers, e.g. evals/suites/external.yaml; every case "
        "it names must appear in the document",
    )
    p_import.add_argument(
        "--source",
        default=AUTO_SOURCE,
        choices=(AUTO_SOURCE, *IMPORT_SOURCE_CHOICES),
        help="which system emitted the document (default: auto — read it off the "
        "bundle envelope; a bare document must name its source)",
    )
    p_import.add_argument(
        "--app-version",
        default="external",
        help="label for the side under test (default: external)",
    )
    p_import.add_argument(
        "--out", default=DEFAULT_IMPORT_OUT, help="output root directory"
    )
    p_import.add_argument(
        "--run-id", default=None, help="run id / output directory name"
    )
    p_import.add_argument(
        "--model-id", default=None, help="override the model id the source recorded"
    )
    p_import.add_argument(
        "--prompt-version",
        default=None,
        help="prompt version the source does not carry (default: 'unknown')",
    )
    p_import.add_argument(
        "--tool-version",
        default=None,
        help="override the tool version the source's own field maps to",
    )
    p_import.add_argument(
        "--timestamp",
        default=None,
        help="recorded ISO-8601 timestamp, overriding the source's (never the clock)",
    )
    p_import.add_argument(
        "--cost-input-per-1k",
        type=float,
        default=DEFAULT_COST_INPUT_PER_1K,
        help=f"price a source that reports none (default: {DEFAULT_COST_INPUT_PER_1K})",
    )
    p_import.add_argument(
        "--cost-output-per-1k",
        type=float,
        default=DEFAULT_COST_OUTPUT_PER_1K,
        help="price a source that reports none "
        f"(default: {DEFAULT_COST_OUTPUT_PER_1K})",
    )
    p_import.add_argument(
        "--fixtures", default=None, help="fixtures root (default: the repo fixtures)"
    )
    p_import.add_argument(
        "--lenient",
        action="store_true",
        help="warn instead of refusing on an unmapped source field, an unknown schema "
        "version, or a record with no recorded duration",
    )
    p_import.add_argument(
        "--dry-run",
        action="store_true",
        help="import and summarize without writing any artifact",
    )
    p_import.set_defaults(func=_cmd_import_trace)

    p_export = sub.add_parser(
        "export-run",
        help="wrap a run's records (and its cases) in a versioned, sealed trace bundle",
    )
    p_export.add_argument(
        "--run",
        required=True,
        help="run directory written by `llmreg run-suite` or `llmreg import-trace`",
    )
    p_export.add_argument(
        "--out",
        default=None,
        help=f"output file (default: {DEFAULT_EXPORT_OUT}/<run_id>.json)",
    )
    p_export.add_argument(
        "--exported-at",
        default=None,
        help="recorded ISO-8601 export timestamp (default: the run's own timestamp). "
        "Never the wall clock — two exports of one run must be byte-identical",
    )
    p_export.add_argument(
        "--no-cases",
        action="store_true",
        help="export records only; the consumer is expected to have the suite",
    )
    p_export.add_argument(
        "--dry-run",
        action="store_true",
        help="build and summarize the bundle without writing it",
    )
    p_export.set_defaults(func=_cmd_export_run)

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
