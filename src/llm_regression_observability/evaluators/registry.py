"""The evaluator registry: every scorer, applied to one case in a fixed order.

:func:`evaluate_case` is the single entry point the rest of the pipeline uses. It
runs every registered evaluator over ``(case, record)`` and returns their
``MetricResult``s **sorted by metric name**, so a scored artifact is byte-stable
regardless of the order the modules happen to be registered in.

Evaluators are silent when a case gives them nothing to score (see :mod:`.base`),
so the metric set is a property of the case, not of the registry: a
``structured`` case emits three JSON metrics and no evidence metrics, and vice
versa. Only the universal ones — ``task_success``, ``latency_ms``,
``cost_estimate_usd``, the four error metrics, ``trace_completeness`` — appear on
every case.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ..schemas import EvalCase, MetricResult, RunRecord
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
from .base import EvaluatorError

__all__ = [
    "CaseEvaluator",
    "EVALUATORS",
    "evaluate_case",
    "evaluate_run",
    "metrics_by_name",
]

#: A scorer: pure, deterministic, ``(case, record) -> metrics``.
CaseEvaluator = Callable[[EvalCase, RunRecord], list[MetricResult]]

#: Every scorer, named. The order here does not affect the output (results are
#: sorted by metric name), only the order in which they run.
EVALUATORS: tuple[tuple[str, CaseEvaluator], ...] = (
    ("exact", exact.evaluate),
    ("json_schema", json_schema.evaluate),
    ("tool_calls", tool_calls.evaluate),
    ("evidence", evidence.evaluate),
    ("refusal", refusal.evaluate),
    ("semantic_stub", semantic_stub.evaluate),
    ("latency", latency.evaluate),
    ("cost", cost.evaluate),
    ("errors", errors.evaluate),
    ("completeness", completeness.evaluate),
)


def evaluate_case(case: EvalCase, record: RunRecord) -> list[MetricResult]:
    """Score one record against its case. Results are sorted by metric name."""
    if case.case_id != record.case_id:
        raise EvaluatorError(
            f"case/record mismatch: case {case.case_id!r} vs record {record.case_id!r}"
        )
    results: list[MetricResult] = []
    for _, evaluator in EVALUATORS:
        results.extend(evaluator(case, record))
    results.sort(key=lambda result: result.metric)
    return results


def evaluate_run(
    cases: Sequence[EvalCase], records: Sequence[RunRecord]
) -> dict[str, list[MetricResult]]:
    """Score every record of a run against its case, keyed by ``case_id``.

    Raises :class:`~.base.EvaluatorError` on a record whose ``case_id`` names no
    case in the suite: an unscored record would silently vanish from every
    aggregate downstream.
    """
    by_id = {case.case_id: case for case in cases}
    scored: dict[str, list[MetricResult]] = {}
    for record in records:
        case = by_id.get(record.case_id)
        if case is None:
            raise EvaluatorError(
                f"record for unknown case_id {record.case_id!r}; "
                f"suite defines {sorted(by_id)}"
            )
        scored[record.case_id] = evaluate_case(case, record)
    return scored


def metrics_by_name(results: Sequence[MetricResult]) -> dict[str, MetricResult]:
    """Index one case's results by metric name, for lookups and assertions."""
    return {result.metric: result for result in results}
