"""Cost scoring: the per-case estimate, and the suite total.

``cost_estimate_usd`` is an **estimate, never a bill**. It is produced upstream by
the run executor from a transparent char-based token estimator (``tokens ≈
chars / 4``) times a synthetic price list (``executor.SYNTHETIC_COST_MODEL``:
$0.003 / $0.015 per 1k input / output tokens — plausible in order of magnitude,
not any vendor's pricing). Nothing in this repository calls a paid API.

This module reads the number rather than recomputing it. Re-pricing is the
executor's job (``llmreg run-suite --recompute-cost``), and it belongs there for
a reason: comparing a baseline priced under one model against a candidate priced
under another would report a `cost_regression` that is an artifact of two price
lists, not of token growth. Scoring a recorded value keeps the estimator in one
place and keeps the evaluator honest about what it is reading.

The comparison engine flags ``cost_regression`` on the **suite total**, not on a
single case: per-case cost is small enough that rounding dominates, and a change
that shifts work between cases is not a regression. :func:`cost_stats` produces
that total, alongside the token counts it came from.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..schemas import EvalCase, MetricResult, RunRecord
from .base import metric

__all__ = ["COST_DECIMALS", "CostStats", "cost_stats", "evaluate"]

#: Cost is rounded to this many decimals, matching ``executor.COST_DECIMALS``, so
#: a total scored here equals the total the run artifact reports.
COST_DECIMALS = 8


@dataclass(frozen=True)
class CostStats:
    """Suite-level cost and token summary over a run's records."""

    count: int
    cost_total_usd: float
    input_tokens_total: int
    output_tokens_total: int

    def to_dict(self) -> dict[str, float | int]:
        return {
            "count": self.count,
            "cost_total_usd": self.cost_total_usd,
            "input_tokens_total": self.input_tokens_total,
            "output_tokens_total": self.output_tokens_total,
        }


def cost_stats(records: Sequence[RunRecord]) -> CostStats:
    """Total estimated cost and total tokens across ``records``."""
    return CostStats(
        count=len(records),
        cost_total_usd=round(
            sum(record.cost_estimate_usd for record in records), COST_DECIMALS
        ),
        input_tokens_total=sum(record.input_tokens_estimate for record in records),
        output_tokens_total=sum(record.output_tokens_estimate for record in records),
    )


def evaluate(case: EvalCase, record: RunRecord) -> list[MetricResult]:
    """Emit the recorded per-case cost estimate. It has a delta, not a pass/fail."""
    return [
        metric(
            record.case_id,
            "cost_estimate_usd",
            record.cost_estimate_usd,
            passed=None,
            detail={
                "estimate": True,
                "input_tokens": record.input_tokens_estimate,
                "output_tokens": record.output_tokens_estimate,
            },
            # Sub-cent costs would vanish under the default six-decimal rounding.
            decimals=COST_DECIMALS,
        )
    ]
