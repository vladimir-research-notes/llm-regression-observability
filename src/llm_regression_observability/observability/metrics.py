"""Suite-level metric names, their reducers, and what kind of signal each one is.

Milestone 5 emits per-case :class:`~..schemas.MetricResult`s. This module says
how a *suite* number is derived from them, and — the part that matters — what
that number is allowed to mean.

**A reducer is named, not assumed.** ``mean``, ``sum``, ``p50``, ``p95``,
``ratio``. "Error rate" and "mean error count" are different questions about the
same column, so :data:`SUITE_METRIC_SPECS` states which one each suite metric
asks. ``ratio`` is the fraction of cases whose value is **non-zero**, which is
what makes ``app_error_count → error_rate`` mean "how many cases errored" rather
than "how many errors, averaged". ``p50`` / ``p95`` reuse the type-7 percentile
already pinned in :mod:`..evaluators.latency`, so a percentile means one thing in
this repository.

**Direction is inherited, never restated.** Every reducer here is monotone in the
values it reduces, so a suite metric is worse in the same direction its per-case
metric is worse. :attr:`MetricSpec.direction` reads
:data:`~..evaluators.base.METRIC_DIRECTION` rather than duplicating it, and a
second table that could silently disagree with the first never exists.

**Signal class is the point of the milestone.** Each suite metric is one of:

``application``
    A score for the system under test. These are the numbers a regression is
    allowed to be about.
``harness``
    A property of the *observation*, not of the application:
    ``trace_completeness``, ``infra_error_rate``, ``infra_error_total``. A move
    here means the telemetry or the infrastructure degraded. Reporting it beside
    a success rate would invite exactly the false regression this project exists
    to avoid, so :mod:`.aggregation` keeps it in a separate map.
``placeholder``
    Deterministic stand-ins that are computed because the pipeline shape is real,
    and are claimed for nothing (``semantic_similarity_stub``, explanation
    quality). They are in :data:`~..evaluators.base.NEVER_FLAG_METRICS`.

The three classes partition the suite metrics; :data:`SIGNALS` is the vocabulary.
A metric that no case emitted produces no suite metric at all — an absent metric
is *not applicable*, never zero (``docs/METRICS.md``).
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..evaluators.base import (
    METRIC_DIRECTION,
    PLACEHOLDER_METRICS,
    VALUE_DECIMALS,
    rounded,
)
from ..evaluators.cost import COST_DECIMALS
from ..evaluators.latency import percentile

__all__ = [
    "APPLICATION",
    "HARNESS",
    "PLACEHOLDER",
    "SIGNALS",
    "HARNESS_CASE_METRICS",
    "Reducer",
    "REDUCERS",
    "mean",
    "total",
    "p50",
    "p95",
    "nonzero_ratio",
    "MetricSpec",
    "SUITE_METRIC_SPECS",
    "SUITE_METRICS",
    "spec_for",
    "specs_for_metric",
    "signal_of",
]

# --------------------------------------------------------------------------- #
# Signal classes
# --------------------------------------------------------------------------- #

APPLICATION = "application"
HARNESS = "harness"
PLACEHOLDER = "placeholder"

#: The closed vocabulary of signal classes.
SIGNALS: tuple[str, ...] = (APPLICATION, HARNESS, PLACEHOLDER)

#: Per-case metrics that describe the *observation*, not the application.
HARNESS_CASE_METRICS: frozenset[str] = frozenset(
    {"trace_completeness", "infra_error_count"}
)


def signal_of(metric: str) -> str:
    """Which signal class a **per-case** metric belongs to."""
    if metric in PLACEHOLDER_METRICS:
        return PLACEHOLDER
    if metric in HARNESS_CASE_METRICS:
        return HARNESS
    return APPLICATION


# --------------------------------------------------------------------------- #
# Reducers
# --------------------------------------------------------------------------- #

#: A reducer folds one metric's per-case values into one suite number.
Reducer = Callable[[Sequence[float]], float]


def mean(values: Sequence[float]) -> float:
    """Arithmetic mean; ``0.0`` on an empty sample."""
    return statistics.fmean(values) if values else 0.0


def total(values: Sequence[float]) -> float:
    """Sum; ``0.0`` on an empty sample. (Named ``sum`` in :data:`REDUCERS`.)"""
    return float(sum(values))


def p50(values: Sequence[float]) -> float:
    """Median, by the type-7 definition pinned in :mod:`..evaluators.latency`."""
    return percentile(values, 0.50)


def p95(values: Sequence[float]) -> float:
    """95th percentile, same definition."""
    return percentile(values, 0.95)


def nonzero_ratio(values: Sequence[float]) -> float:
    """Fraction of cases with a **non-zero** value; ``0.0`` on an empty sample.

    Distinct from :func:`mean` on a counting metric: two errors on one case make
    ``mean`` say ``0.25`` over eight cases and ``ratio`` say ``0.125``. A *rate*
    is about cases, not events.
    """
    if not values:
        return 0.0
    return sum(1 for value in values if value != 0.0) / len(values)


#: Every reducer, by the name a :class:`MetricSpec` refers to it by.
REDUCERS: dict[str, Reducer] = {
    "mean": mean,
    "sum": total,
    "p50": p50,
    "p95": p95,
    "ratio": nonzero_ratio,
}


# --------------------------------------------------------------------------- #
# The suite metric table
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MetricSpec:
    """One suite metric: which per-case metric it reduces, how, and what it is."""

    metric: str
    suite_metric: str
    reducer: str
    signal: str = APPLICATION
    decimals: int = VALUE_DECIMALS

    @property
    def direction(self) -> str:
        """Inherited from the per-case metric — reducers here are monotone."""
        return METRIC_DIRECTION[self.metric]

    def reduce(self, values: Sequence[float]) -> float:
        """Fold ``values`` and round once, at the end."""
        return rounded(REDUCERS[self.reducer](values), self.decimals)


#: The suite metrics, in report order. Several specs may share a source metric
#: (``latency_ms`` answers three questions); no two share a ``suite_metric``.
SUITE_METRIC_SPECS: tuple[MetricSpec, ...] = (
    # Final answer
    MetricSpec("task_success", "success_rate", "mean"),
    MetricSpec("exact_match", "exact_match_rate", "mean"),
    # Structured output
    MetricSpec("json_parse_success", "json_parse_rate", "mean"),
    MetricSpec("json_schema_validity", "json_validity_rate", "mean"),
    MetricSpec("field_diff_count", "field_diff_total", "sum"),
    # Tool calls. ``tool_arg_diff`` and ``risk_label_delta`` are pairwise metrics
    # (baseline against candidate); a single run never emits them, so no suite
    # metric reduces them — the comparison engine owns that dimension.
    MetricSpec("allowed_tool_ratio", "allowed_tool_rate", "mean"),
    MetricSpec("tool_sequence_similarity", "tool_sequence_similarity_mean", "mean"),
    MetricSpec("missing_tools", "missing_tool_total", "sum"),
    MetricSpec("extra_tools", "extra_tool_total", "sum"),
    # Evidence
    MetricSpec("citation_presence", "citation_presence_rate", "mean"),
    MetricSpec("citation_correctness", "citation_correctness_rate", "mean"),
    MetricSpec("evidence_coverage", "evidence_coverage_mean", "mean"),
    MetricSpec("unsupported_claim_count", "unsupported_claim_total", "sum"),
    # Refusal
    MetricSpec("refusal_match", "refusal_match_rate", "mean"),
    MetricSpec("risk_label_match", "risk_label_match_rate", "mean"),
    # Latency / cost
    MetricSpec("latency_ms", "latency_p50", "p50"),
    MetricSpec("latency_ms", "latency_p95", "p95"),
    MetricSpec("latency_ms", "latency_total_ms", "sum"),
    MetricSpec("cost_estimate_usd", "cost_total_usd", "sum", decimals=COST_DECIMALS),
    # Errors and retries
    MetricSpec("app_error_count", "error_rate", "ratio"),
    MetricSpec("app_error_count", "app_error_total", "sum"),
    MetricSpec("retry_count", "retry_total", "sum"),
    MetricSpec("tool_error_rate", "tool_error_rate_mean", "mean"),
    # Harness signals — never application scores.
    MetricSpec("trace_completeness", "trace_completeness", "mean", signal=HARNESS),
    MetricSpec("infra_error_count", "infra_error_rate", "ratio", signal=HARNESS),
    MetricSpec("infra_error_count", "infra_error_total", "sum", signal=HARNESS),
    # Placeholders — computed, claimed for nothing.
    MetricSpec(
        "semantic_similarity_stub",
        "semantic_similarity_stub_mean",
        "mean",
        signal=PLACEHOLDER,
    ),
    MetricSpec(
        "explanation_quality_placeholder",
        "explanation_quality_mean",
        "mean",
        signal=PLACEHOLDER,
    ),
)

#: Suite metric name -> spec.
SUITE_METRICS: dict[str, MetricSpec] = {
    spec.suite_metric: spec for spec in SUITE_METRIC_SPECS
}


def spec_for(suite_metric: str) -> MetricSpec:
    """Look up one suite metric's spec."""
    try:
        return SUITE_METRICS[suite_metric]
    except KeyError:  # pragma: no cover - defensive
        raise KeyError(f"unknown suite metric {suite_metric!r}") from None


def specs_for_metric(metric: str) -> tuple[MetricSpec, ...]:
    """Every suite metric derived from the per-case metric ``metric``."""
    return tuple(spec for spec in SUITE_METRIC_SPECS if spec.metric == metric)
