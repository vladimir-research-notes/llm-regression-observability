"""Deterministic scorers: a trace plus its case in, ``MetricResult``s out.

The metric definitions live in ``docs/METRICS.md``; this package is their
implementation. Every scorer is a pure function of ``(EvalCase, RunRecord)`` —
no clock, no RNG, no network, no learned model — so the same trace always yields
the same score, and ``tests/test_evaluators.py`` can assert **exact** expected
values rather than "roughly right".

One module per dimension:

============================  ==========================================
:mod:`.exact`                 ``task_success`` (structural), ``exact_match``
:mod:`.json_schema`           parse success, schema validity, field diff
:mod:`.tool_calls`            allowed-tool ratio, sequence/arg drift, gaps
:mod:`.evidence`              citation presence/correctness, coverage, support
:mod:`.refusal`               refusal match, risk label, explanation placeholder
:mod:`.latency`               per-case latency, p50/p95
:mod:`.cost`                  per-case cost estimate, suite total
:mod:`.errors`                app/infra classification, retries, tool errors
:mod:`.completeness`          trace completeness (a harness signal)
:mod:`.semantic_stub`         lexical similarity — a stand-in, not a judge
============================  ==========================================

:func:`evaluate_case` runs all of them; :func:`evaluate_run` scores a whole run.
A scorer stays silent when the case gives it nothing to score, so an absent
metric means *not applicable*, never *zero*.

Two guard rails matter downstream. :data:`METRIC_DIRECTION` says which way is
worse for each metric, so the comparison engine never hard-codes a sign.
:data:`NEVER_FLAG_METRICS` names the metrics that may not raise a regression at
all: the two placeholders (:mod:`.semantic_stub`, explanation quality) and the
infra counters, because harness noise is surfaced, not blamed on the application.
"""

from __future__ import annotations

from . import (
    completeness,
    cost,
    errors,
    evidence,
    exact,
    json_schema,
    latency,
    refusal,
    semantic_stub,
    tool_calls,
)
from .base import (
    HIGHER_IS_BETTER,
    LOWER_IS_BETTER,
    METRIC_DIRECTION,
    NEUTRAL,
    NEVER_FLAG_METRICS,
    PLACEHOLDER_METRICS,
    VALUE_DECIMALS,
    EvaluatorError,
    canonical_json,
    metric,
    parse_json,
    ratio,
    rounded,
    sequence_ratio,
)
from .completeness import completeness_checks
from .cost import CostStats, cost_stats
from .errors import (
    attribution,
    classify_errors,
    error_rate,
    infra_error_rate,
    retry_count,
    retry_total,
)
from .exact import (
    PAIRWISE_PROPERTIES,
    PROPERTY_CHECKS,
    check_properties,
    final_output_stable,
)
from .json_schema import (
    SUPPORTED_KEYWORDS,
    FieldDiff,
    Violation,
    field_diff,
    is_valid,
    unsupported_keywords,
    validate,
)
from .latency import LatencyStats, latency_stats, percentile
from .refusal import refusal_decision, risk_label, risk_label_delta
from .registry import (
    EVALUATORS,
    CaseEvaluator,
    evaluate_case,
    evaluate_run,
    metrics_by_name,
)
from .semantic_stub import output_similarity, semantic_similarity_stub
from .tool_calls import (
    argument_distance,
    sequence_similarity,
    tool_arg_diff_between,
    tool_argument_diff,
)

__all__ = [
    # Modules
    "exact",
    "json_schema",
    "tool_calls",
    "evidence",
    "refusal",
    "latency",
    "cost",
    "errors",
    "completeness",
    "semantic_stub",
    # Registry
    "EVALUATORS",
    "CaseEvaluator",
    "evaluate_case",
    "evaluate_run",
    "metrics_by_name",
    # Metric metadata
    "METRIC_DIRECTION",
    "PLACEHOLDER_METRICS",
    "NEVER_FLAG_METRICS",
    "HIGHER_IS_BETTER",
    "LOWER_IS_BETTER",
    "NEUTRAL",
    "VALUE_DECIMALS",
    # Shared helpers
    "EvaluatorError",
    "metric",
    "ratio",
    "rounded",
    "parse_json",
    "canonical_json",
    "sequence_ratio",
    # Per-dimension surface
    "PROPERTY_CHECKS",
    "PAIRWISE_PROPERTIES",
    "check_properties",
    "final_output_stable",
    "SUPPORTED_KEYWORDS",
    "Violation",
    "FieldDiff",
    "validate",
    "is_valid",
    "field_diff",
    "unsupported_keywords",
    "sequence_similarity",
    "argument_distance",
    "tool_argument_diff",
    "tool_arg_diff_between",
    "refusal_decision",
    "risk_label",
    "risk_label_delta",
    "semantic_similarity_stub",
    "output_similarity",
    "percentile",
    "LatencyStats",
    "latency_stats",
    "CostStats",
    "cost_stats",
    "attribution",
    "classify_errors",
    "retry_count",
    "retry_total",
    "error_rate",
    "infra_error_rate",
    "completeness_checks",
]
