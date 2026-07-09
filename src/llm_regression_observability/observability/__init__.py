"""Observability: a run as nested spans, and a run as suite-level metrics.

Three modules, one idea — *make the harness visible next to the application, and
never mixed into it*.

:mod:`.spans`
    Projects a recorded :class:`~..schemas.RunRecord` into an
    OpenTelemetry-shaped span tree (request → model call / tool calls /
    retrievals / evaluators) with no OpenTelemetry dependency. Spans are derived
    from the trace, so they are reproducible; the one duration the trace records
    is labeled ``recorded`` and every other interval is labeled ``derived`` or
    ``none``. Spans carry identifiers and counts, never free text.
:mod:`.metrics`
    Names the suite metrics, the reducer each one uses (``mean``, ``sum``,
    ``p50``, ``p95``, ``ratio``), and its **signal class**: ``application``,
    ``harness``, or ``placeholder``.
:mod:`.aggregation`
    Reduces per-case ``MetricResult``s and spans into a :class:`RunAggregate`,
    keeping the three signal classes in separate maps, and writes
    ``results/runs/<run_id>/spans.jsonl`` and
    ``results/processed/<run_id>/aggregate.json``.

This is what makes the application-vs-harness distinction operational.
``trace_completeness`` and ``infra_error_rate`` are properties of the
*observation*: when they move, the telemetry or the infrastructure degraded, and
a report that averaged them into a success rate would turn a broken exporter into
a false regression. They live in ``harness_metrics``, on purpose, structurally.

No clock, no RNG, no network: aggregating the same run twice yields byte-identical
artifacts.
"""

from __future__ import annotations

from . import aggregation, metrics, spans
from .aggregation import (
    AGGREGATE_JSON,
    AggregateResult,
    ReducedMetrics,
    RunAggregate,
    SpanSummary,
    aggregate_run,
    aggregate_run_dir,
    reduce_metrics,
    summarize_spans,
    write_aggregate,
)
from .metrics import (
    APPLICATION,
    HARNESS,
    PLACEHOLDER,
    REDUCERS,
    SIGNALS,
    SUITE_METRIC_SPECS,
    SUITE_METRICS,
    MetricSpec,
    Reducer,
    signal_of,
    spec_for,
    specs_for_metric,
)
from .spans import (
    SPAN_NAMES,
    SPANS_JSONL,
    TIMING_DERIVED,
    TIMING_NONE,
    TIMING_RECORDED,
    TIMING_SOURCES,
    ObservabilityError,
    Span,
    build_case_spans,
    build_run_spans,
    read_spans,
    span_depths,
    span_id_for,
    step_bounds,
    trace_id_for,
    validate_spans,
    write_spans,
)

__all__ = [
    # Modules
    "spans",
    "metrics",
    "aggregation",
    # Spans
    "ObservabilityError",
    "Span",
    "SPAN_NAMES",
    "SPANS_JSONL",
    "TIMING_SOURCES",
    "TIMING_RECORDED",
    "TIMING_DERIVED",
    "TIMING_NONE",
    "trace_id_for",
    "span_id_for",
    "step_bounds",
    "build_case_spans",
    "build_run_spans",
    "validate_spans",
    "span_depths",
    "write_spans",
    "read_spans",
    # Metrics
    "APPLICATION",
    "HARNESS",
    "PLACEHOLDER",
    "SIGNALS",
    "Reducer",
    "REDUCERS",
    "MetricSpec",
    "SUITE_METRIC_SPECS",
    "SUITE_METRICS",
    "spec_for",
    "specs_for_metric",
    "signal_of",
    # Aggregation
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
