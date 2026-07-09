"""Aggregation: per-case metrics and spans in, one suite summary out.

``aggregate_run()`` reduces a run's :class:`~..schemas.MetricResult`s through
:data:`~.metrics.SUITE_METRIC_SPECS` and folds its spans into a
:class:`SpanSummary`. ``aggregate_run_dir()`` is the same thing over a run
directory, and writes two artifacts:

``results/runs/<run_id>/spans.jsonl``
    The span tree (:mod:`.spans`), validated before it is written.
``results/processed/<run_id>/aggregate.json``
    One :class:`RunAggregate`.

**Why this stage owns ``spans.jsonl``.** Spans are a projection of the recorded
trace, not a live emission, and the projection is only complete once the run has
been scored — an ``evaluator`` span needs a metric. The executor has the trace
and no scores; this stage has both. Rebuilding the spans here is a pure function
of ``run.jsonl`` plus the suite, so nothing is lost by deriving them late and a
great deal is gained: there is exactly one writer, and the file always holds the
whole tree.

**Why the summary has three maps instead of one.** ``application_metrics`` are
scores for the system under test. ``harness_metrics`` — ``trace_completeness``,
``infra_error_rate``, ``infra_error_total``, ``incomplete_trace_rate`` — describe
whether the *observation* can be trusted. ``placeholder_metrics`` are computed and
claimed for nothing. A single flat map would let a broken exporter and a broken
model look alike at a glance, which is the failure mode this project exists to
avoid. The split is structural, so a reader cannot miss it and a downstream
consumer cannot accidentally average across it.

``metric_coverage`` counts how many cases contributed to each suite number, for
the same reason: ``evidence_coverage_mean = 1.0`` over 2 of 8 cases is a fact
about two cases, and a summary that does not say so is inviting a
misreading.

No clock, no RNG, no network. Two aggregations of the same run are byte-identical.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__
from ..datasets import load_eval_suite, repo_root
from ..evaluators import evaluate_run
from ..evaluators.base import rounded
from ..evaluators.errors import attribution
from ..executor import RUN_JSONL, RUN_META_JSON, RunMeta
from ..schemas import EvalCase, MetricResult, RunRecord, Schema
from ..traces.storage import (
    read_json,
    read_jsonl,
    render_jsonl,
    sha256_hex,
    write_json,
)
from .metrics import (
    APPLICATION,
    HARNESS,
    PLACEHOLDER,
    SUITE_METRIC_SPECS,
)
from .spans import (
    SPANS_JSONL,
    ObservabilityError,
    Span,
    build_run_spans,
    span_depths,
    write_spans,
)

__all__ = [
    "AGGREGATE_JSON",
    "SpanSummary",
    "ReducedMetrics",
    "RunAggregate",
    "AggregateResult",
    "reduce_metrics",
    "summarize_spans",
    "aggregate_run",
    "write_aggregate",
    "aggregate_run_dir",
]

AGGREGATE_JSON = "aggregate.json"


# --------------------------------------------------------------------------- #
# Span summary
# --------------------------------------------------------------------------- #


@dataclass(kw_only=True)
class SpanSummary(Schema):
    """What the span tree contains, without restating the tree."""

    span_count: int = 0
    trace_count: int = 0
    max_depth: int = 0
    spans_by_name: dict[str, int] = field(default_factory=dict)
    error_spans_by_name: dict[str, int] = field(default_factory=dict)
    error_span_count: int = 0
    #: Sum of the *recorded* request durations. Derived child widths are never
    #: summed: they carry ordering, not duration (see :mod:`.spans`).
    recorded_duration_ms: float = 0.0


def summarize_spans(spans: Sequence[Span]) -> SpanSummary:
    """Fold a span tree into counts. Deterministic; names in span order."""
    by_name: dict[str, int] = {}
    errors_by_name: dict[str, int] = {}
    recorded = 0.0
    for span in spans:
        by_name[span.name] = by_name.get(span.name, 0) + 1
        if span.status == "error":
            errors_by_name[span.name] = errors_by_name.get(span.name, 0) + 1
        if span.name == "request":
            recorded += span.duration_ms
    return SpanSummary(
        span_count=len(spans),
        trace_count=len({span.trace_id for span in spans}),
        max_depth=max(span_depths(spans).values(), default=0),
        spans_by_name=dict(sorted(by_name.items())),
        error_spans_by_name=dict(sorted(errors_by_name.items())),
        error_span_count=sum(errors_by_name.values()),
        recorded_duration_ms=rounded(recorded),
    )


# --------------------------------------------------------------------------- #
# Metric reduction
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReducedMetrics:
    """The three signal maps plus the per-metric case counts."""

    application: dict[str, float]
    harness: dict[str, float]
    placeholder: dict[str, float]
    coverage: dict[str, int]


def _values_by_metric(
    scored: Mapping[str, Sequence[MetricResult]],
) -> dict[str, list[float]]:
    """Every per-case value, keyed by metric, in case order."""
    columns: dict[str, list[float]] = {}
    for case_id in scored:
        for result in scored[case_id]:
            columns.setdefault(result.metric, []).append(result.value)
    return columns


def reduce_metrics(scored: Mapping[str, Sequence[MetricResult]]) -> ReducedMetrics:
    """Reduce per-case metrics to suite metrics, split by signal class.

    A metric no case emitted yields no suite metric: absent is *not applicable*,
    never zero.
    """
    columns = _values_by_metric(scored)
    maps: dict[str, dict[str, float]] = {
        APPLICATION: {},
        HARNESS: {},
        PLACEHOLDER: {},
    }
    coverage: dict[str, int] = {}
    for spec in SUITE_METRIC_SPECS:
        values = columns.get(spec.metric)
        if not values:
            continue
        maps[spec.signal][spec.suite_metric] = spec.reduce(values)
        coverage[spec.suite_metric] = len(values)
    return ReducedMetrics(
        application=maps[APPLICATION],
        harness=maps[HARNESS],
        placeholder=maps[PLACEHOLDER],
        coverage=coverage,
    )


# --------------------------------------------------------------------------- #
# Run aggregate
# --------------------------------------------------------------------------- #


@dataclass(kw_only=True)
class RunAggregate(Schema):
    """One run, summarized — written to ``results/processed/<id>/aggregate.json``.

    The two case-id lists are the audit trail: ``incomplete_trace_case_ids`` names
    the cases whose *trace* failed a completeness check, and
    ``infra_attributed_case_ids`` names those whose every error was infra-origin.
    Both are harness findings. Neither is a regression, and neither may be
    silently folded into an application score.
    """

    run_id: str
    suite_id: str
    app_version: str
    adapter: str
    case_count: int = 0
    metric_count: int = 0
    application_metrics: dict[str, float] = field(default_factory=dict)
    harness_metrics: dict[str, float] = field(default_factory=dict)
    placeholder_metrics: dict[str, float] = field(default_factory=dict)
    metric_coverage: dict[str, int] = field(default_factory=dict)
    spans: SpanSummary = field(default_factory=SpanSummary)
    incomplete_trace_case_ids: list[str] = field(default_factory=list)
    infra_attributed_case_ids: list[str] = field(default_factory=list)
    metrics_sha256: str = ""
    spans_sha256: str = ""
    llmreg_version: str = __version__


@dataclass
class AggregateResult:
    """The in-memory outcome of an aggregation, plus where it was written."""

    aggregate: RunAggregate
    spans: list[Span]
    metrics: list[MetricResult]
    spans_path: Path | None = None
    aggregate_path: Path | None = None


def _incomplete_trace_case_ids(
    scored: Mapping[str, Sequence[MetricResult]],
) -> list[str]:
    """Cases whose ``trace_completeness`` check failed — a harness finding."""
    return sorted(
        case_id
        for case_id, results in scored.items()
        for result in results
        if result.metric == "trace_completeness" and result.passed is False
    )


def aggregate_run(
    meta: RunMeta,
    records: Sequence[RunRecord],
    scored: Mapping[str, Sequence[MetricResult]],
    spans: Sequence[Span],
) -> RunAggregate:
    """Summarize one scored run. Pure: writes nothing."""
    reduced = reduce_metrics(scored)
    flat = [result for case_id in scored for result in scored[case_id]]
    incomplete = _incomplete_trace_case_ids(scored)

    harness = dict(reduced.harness)
    if records:
        harness["incomplete_trace_rate"] = rounded(len(incomplete) / len(records))

    return RunAggregate(
        run_id=meta.run_id,
        suite_id=meta.suite_id,
        app_version=meta.app_version,
        adapter=meta.adapter,
        case_count=len(records),
        metric_count=len(flat),
        application_metrics=reduced.application,
        harness_metrics=harness,
        placeholder_metrics=reduced.placeholder,
        metric_coverage=reduced.coverage,
        spans=summarize_spans(spans),
        incomplete_trace_case_ids=incomplete,
        infra_attributed_case_ids=sorted(
            record.case_id for record in records if attribution(record) == "infra"
        ),
        metrics_sha256=sha256_hex(render_jsonl(flat)),
        spans_sha256=sha256_hex(render_jsonl(spans)),
    )


# --------------------------------------------------------------------------- #
# Artifacts
# --------------------------------------------------------------------------- #


def write_aggregate(
    result: AggregateResult, run_dir: Path | str, out_root: Path | str
) -> AggregateResult:
    """Write ``spans.jsonl`` into the run directory and the summary into
    ``out_root/<run_id>/``.

    ``metrics.jsonl`` is *not* written here: ``scripts/04_score_runs.py`` owns
    that file. ``metrics_sha256`` digests the same metric stream this stage
    reduced, so the two artifacts can be checked against each other rather than
    two writers racing for one path.
    """
    result.spans_path = write_spans(Path(run_dir) / SPANS_JSONL, result.spans)
    written = sha256_hex(result.spans_path.read_bytes())
    if written != result.aggregate.spans_sha256:  # pragma: no cover - defensive
        raise ObservabilityError(
            f"{result.spans_path}: digest mismatch after write "
            f"({written} != {result.aggregate.spans_sha256})"
        )

    out_dir = Path(out_root) / result.aggregate.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    result.aggregate_path = write_json(out_dir / AGGREGATE_JSON, result.aggregate)
    return result


def _load_cases(meta: RunMeta, root: Path | None) -> list[EvalCase]:
    suite_path = Path(meta.suite_path)
    if not suite_path.is_absolute():
        suite_path = (root or repo_root()) / suite_path
    return load_eval_suite(suite_path).cases


def aggregate_run_dir(
    run_dir: Path | str,
    out_root: Path | str | None = None,
    *,
    root: Path | None = None,
) -> AggregateResult:
    """Load a run directory, score it, project spans, and summarize.

    ``out_root=None`` is the dry-run path: nothing is written, not even
    ``spans.jsonl``.
    """
    run_dir = Path(run_dir)
    meta: RunMeta = read_json(run_dir / RUN_META_JSON, RunMeta)
    records: list[RunRecord] = read_jsonl(run_dir / RUN_JSONL, RunRecord)
    cases = _load_cases(meta, root)

    scored = evaluate_run(cases, records)
    spans = build_run_spans(cases, records, scored)
    flat = [result for case_id in scored for result in scored[case_id]]

    result = AggregateResult(
        aggregate=aggregate_run(meta, records, scored, spans),
        spans=spans,
        metrics=flat,
    )
    if out_root is not None:
        write_aggregate(result, run_dir, out_root)
    return result
