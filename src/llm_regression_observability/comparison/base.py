"""Shared plumbing for the comparison engine: one normalized side, and deltas.

A *comparison* pairs two runs of the same suite — a **baseline** (known-good) and
a **candidate** (the change) — and asks, case by case, whether a measurable
behavioral property got worse. Both runs arrive here as a :class:`RunSide`: the
records, the cases that produced them, and every scorer's verdict on each pair.

Three conventions are fixed here rather than repeated downstream.

**A side is scored once.** :meth:`RunSide.from_records` runs the Milestone 5
evaluators over every record at construction, so the rules in :mod:`.regression`
read ``MetricResult``s and never re-derive a score. The metric map is the
interface between the two milestones; the rules never touch a raw trace except
for the handful of genuinely *pairwise* signals (argument drift, a refusal
flip) that no single record can answer.

**A delta is candidate minus baseline.** Nothing here decides whether a positive
delta is bad; direction is metadata (``evaluators.METRIC_DIRECTION``), and the
rule that reads a delta names the direction it cares about.

**A relative change against a zero baseline is not zero.** :func:`relative`
returns ``inf`` when the baseline was ``0`` and the candidate is not, and ``0.0``
when both are — the alternative (dividing by zero, or silently reporting ``0``)
would turn "latency appeared out of nowhere" into "latency did not move".

Deterministic and offline: no clock, no RNG, no network. Deltas are rounded at
:data:`DELTA_DECIMALS` (cost at :data:`COST_DECIMALS`, matching the executor) so
a comparison artifact is byte-identical across runs and a threshold test cannot
hinge on the last bit of a float.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..evaluators import evaluate_case, metrics_by_name
from ..pricing import COST_DECIMALS
from ..schemas import EvalCase, MetricResult, RunRecord

__all__ = [
    "ComparisonError",
    "DELTA_DECIMALS",
    "COST_DECIMALS",
    "BEHAVIORAL_FIELDS",
    "IDENTITY_FIELDS",
    "PROVENANCE_FIELDS",
    "RunSide",
    "delta",
    "relative",
    "metric_value",
    "metric_detail",
]

#: Deltas are rounded to this many decimals, matching ``evaluators.VALUE_DECIMALS``.
DELTA_DECIMALS = 6

# ``COST_DECIMALS`` (cost deltas need finer resolution) is imported from ``..pricing``
# and re-exported here: one estimator, one rounding, no copy to keep in sync.

#: ``RunRecord`` fields that describe *what the application did*. Only these are
#: compared into ``ComparisonRecord.changed_fields``.
BEHAVIORAL_FIELDS: tuple[str, ...] = (
    "input_tokens_estimate",
    "output_tokens_estimate",
    "latency_ms",
    "cost_estimate_usd",
    "final_output",
    "parsed_output",
    "tool_calls",
    "retrieval_sources",
    "errors",
    "redaction_status",
)

#: Fields that identify *the run*. They always differ between a baseline and a
#: candidate and say nothing about behavior, so they are never "changed fields".
IDENTITY_FIELDS: tuple[str, ...] = ("run_id", "case_id", "app_version")

#: Fields that describe *where the behavior came from*. A model or prompt version
#: bump is the thing under test, not evidence of a regression.
PROVENANCE_FIELDS: tuple[str, ...] = (
    "model_id",
    "prompt_version",
    "tool_version",
    "timestamp",
)


class ComparisonError(RuntimeError):
    """Raised when two runs cannot be compared, or a comparison is misconfigured."""


def delta(
    candidate: float, baseline: float, *, decimals: int = DELTA_DECIMALS
) -> float:
    """``candidate - baseline``, rounded. Sign carries no judgment."""
    return round(float(candidate) - float(baseline), decimals)


def relative(candidate: float, baseline: float) -> float:
    """Fractional change of ``candidate`` against ``baseline``.

    ``inf`` when the baseline was ``0`` and the candidate moved off it — a value
    that appeared from nothing has no finite relative change, and reporting
    ``0.0`` would hide it. ``0.0`` when both are ``0``.
    """
    if baseline == 0.0:
        return 0.0 if candidate == 0.0 else float("inf")
    change = (float(candidate) - float(baseline)) / abs(float(baseline))
    return round(change, DELTA_DECIMALS)


def metric_value(metrics: dict[str, MetricResult], name: str) -> float | None:
    """The value of ``name``, or ``None`` when the scorer did not emit it.

    ``None`` means *not applicable*, never zero (``docs/METRICS.md``). Every rule
    in :mod:`.regression` must handle it as "this dimension was not measured".
    """
    result = metrics.get(name)
    return None if result is None else result.value


def metric_detail(metrics: dict[str, MetricResult], name: str) -> dict[str, Any]:
    """The ``detail`` payload of ``name``, or an empty mapping when absent."""
    result = metrics.get(name)
    return {} if result is None else result.detail


@dataclass(frozen=True)
class RunSide:
    """One side of a comparison: a run, its cases, and its scores.

    Constructed by :meth:`from_records` (in-memory, used by the fixture tests) or
    by ``comparison.baseline.load_run_side`` (from ``results/runs/<run_id>/``).
    ``records`` keeps the run's order; ``metrics`` is keyed ``case_id -> metric``.
    """

    run_id: str
    app_version: str
    suite_id: str
    adapter: str
    cases: dict[str, EvalCase]
    records: tuple[RunRecord, ...]
    metrics: dict[str, dict[str, MetricResult]]
    suite_path: str = ""
    thresholds: dict[str, Any] = field(default_factory=dict)
    cost_model: dict[str, float] | None = None

    @classmethod
    def from_records(
        cls,
        *,
        run_id: str,
        app_version: str,
        suite_id: str,
        adapter: str = "(in-memory)",
        cases: dict[str, EvalCase],
        records: list[RunRecord] | tuple[RunRecord, ...],
        suite_path: str = "",
        thresholds: dict[str, Any] | None = None,
        cost_model: dict[str, float] | None = None,
    ) -> RunSide:
        """Score ``records`` against ``cases`` and freeze the result.

        Raises :class:`ComparisonError` on a duplicate ``case_id`` (two records
        for one case make ``(run_id, case_id)`` ambiguous) or on a record whose
        case the suite does not define (it could never be paired or scored).
        """
        ordered = tuple(records)
        seen: set[str] = set()
        scored: dict[str, dict[str, MetricResult]] = {}
        for record in ordered:
            if record.case_id in seen:
                raise ComparisonError(
                    f"run {run_id!r}: duplicate record for case {record.case_id!r}"
                )
            seen.add(record.case_id)
            case = cases.get(record.case_id)
            if case is None:
                raise ComparisonError(
                    f"run {run_id!r}: record for unknown case {record.case_id!r}; "
                    f"suite defines {sorted(cases)}"
                )
            scored[record.case_id] = metrics_by_name(evaluate_case(case, record))
        return cls(
            run_id=run_id,
            app_version=app_version,
            suite_id=suite_id,
            adapter=adapter,
            cases=dict(cases),
            records=ordered,
            metrics=scored,
            suite_path=suite_path,
            thresholds=dict(thresholds or {}),
            cost_model=dict(cost_model) if cost_model is not None else None,
        )

    @property
    def case_ids(self) -> tuple[str, ...]:
        """Case ids in run order."""
        return tuple(record.case_id for record in self.records)

    def record(self, case_id: str) -> RunRecord:
        for record in self.records:
            if record.case_id == case_id:
                return record
        raise ComparisonError(f"run {self.run_id!r}: no record for case {case_id!r}")

    def metrics_for(self, case_id: str) -> dict[str, MetricResult]:
        try:
            return self.metrics[case_id]
        except KeyError:
            raise ComparisonError(
                f"run {self.run_id!r}: no scores for case {case_id!r}"
            ) from None

    def case(self, case_id: str) -> EvalCase:
        try:
            return self.cases[case_id]
        except KeyError:
            raise ComparisonError(
                f"run {self.run_id!r}: suite defines no case {case_id!r}"
            ) from None
