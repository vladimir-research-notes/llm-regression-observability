"""Latency scoring: the per-case number, and the suite-level percentiles.

``latency_ms`` is read straight off the trace. The executor **records** latency;
it never measures it (``docs/LIMITATIONS.md``), because timing the adapter would
put an ambient value into a scored field and two runs would stop being
byte-identical. So this module is arithmetic over recorded inputs, nothing more.

The percentile definition is pinned rather than borrowed. ``statistics.quantiles``
cuts a distribution into equal groups and cannot answer "p95" directly for a
sample of eight; naming a method matters more than which one, because a threshold
in ``docs/REGRESSION_DETECTION.md`` is only meaningful if p95 means one thing.
:func:`percentile` uses **linear interpolation between closest ranks** (the
"type 7" definition, the default in NumPy and R): position ``q * (n - 1)`` in the
sorted sample, interpolating between its neighbours. For ``n == 1`` every
percentile is that value; for an empty sample the result is ``0.0``.

Percentiles feed ``latency_regression``, which is deliberately hard to trip: the
documented threshold demands a relative move (**+25 %**) *and* an absolute one
(**+50 ms**), so a 2 ms → 3 ms jitter never flags. Milestone 6 applies it; this
module only supplies the numbers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..schemas import EvalCase, MetricResult, RunRecord
from .base import metric, rounded

__all__ = ["percentile", "LatencyStats", "latency_stats", "evaluate"]


def percentile(values: Sequence[float], q: float) -> float:
    """The ``q``-quantile (``q`` in ``[0, 1]``) by linear interpolation.

    Deterministic and dependency-free. ``percentile(xs, 0.5)`` is the median.
    """
    if not values:
        return 0.0
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be in [0, 1], got {q}")
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return rounded(ordered[0])
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return rounded(ordered[low] + (ordered[high] - ordered[low]) * fraction)


@dataclass(frozen=True)
class LatencyStats:
    """Suite-level latency summary over a run's records."""

    count: int
    total_ms: float
    mean_ms: float
    p50_ms: float
    p95_ms: float

    def to_dict(self) -> dict[str, float]:
        return {
            "count": self.count,
            "total_ms": self.total_ms,
            "mean_ms": self.mean_ms,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
        }


def latency_stats(records: Sequence[RunRecord]) -> LatencyStats:
    """Total, mean, p50 and p95 of the recorded per-case latencies."""
    values = [record.latency_ms for record in records]
    total = rounded(sum(values))
    mean = rounded(total / len(values)) if values else 0.0
    return LatencyStats(
        count=len(values),
        total_ms=total,
        mean_ms=mean,
        p50_ms=percentile(values, 0.50),
        p95_ms=percentile(values, 0.95),
    )


def evaluate(case: EvalCase, record: RunRecord) -> list[MetricResult]:
    """Emit the recorded per-case latency. It has a delta, not a pass/fail."""
    return [
        metric(
            record.case_id,
            "latency_ms",
            record.latency_ms,
            passed=None,
            detail={"source": "recorded", "measured": False},
        )
    ]
