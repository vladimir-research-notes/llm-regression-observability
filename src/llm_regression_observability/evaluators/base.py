"""Shared plumbing for every scorer: rounding, ratios, and metric metadata.

An *evaluator* is a pure function ``(EvalCase, RunRecord) -> list[MetricResult]``.
It reads a recorded trace and the case that produced it, and emits zero or more
:class:`~..schemas.MetricResult`s. It never reaches for the clock, an RNG, or the
network, and it never mutates its inputs — so the same trace always scores the
same way (``CLAUDE.md``, determinism rule).

Three conventions are fixed here rather than left to each module:

**Values are rounded.** Every metric value passes through :func:`rounded` at
:data:`VALUE_DECIMALS` places. ``2/3`` is ``0.666667``, not
``0.6666666666666666``, so a ``MetricResult`` serializes to identical bytes on
any platform and a threshold comparison in Milestone 6 cannot hinge on the last
bit of a float.

**A metric that cannot be computed is not emitted.** Scoring a case that
declares no ``json_schema`` as ``json_schema_validity == 0`` would invent a
failure. Each evaluator states its precondition and stays silent otherwise;
Milestone 6 treats an absent metric as *not applicable*, not as zero.

**Direction is metadata, not a convention.** :data:`METRIC_DIRECTION` says
whether a metric is better high (``allowed_tool_ratio``), better low
(``latency_ms``), or neither (``infra_error_count``). A regression is a move in
the bad direction, so the comparison engine reads this table instead of
hard-coding a sign per metric. :data:`NEVER_FLAG_METRICS` names the metrics that
must never raise a flag no matter which way they move: the two placeholders, and
``infra_error_count`` (harness noise is surfaced, never blamed on the
application — see ``docs/REGRESSION_DETECTION.md``).
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Sequence
from typing import Any

from ..schemas import MetricResult

__all__ = [
    "EvaluatorError",
    "VALUE_DECIMALS",
    "HIGHER_IS_BETTER",
    "LOWER_IS_BETTER",
    "NEUTRAL",
    "METRIC_DIRECTION",
    "PLACEHOLDER_METRICS",
    "NEVER_FLAG_METRICS",
    "rounded",
    "ratio",
    "metric",
    "canonical_json",
    "parse_json",
    "sequence_ratio",
]


class EvaluatorError(ValueError):
    """Raised when a scorer is handed input it cannot score (e.g. a record whose
    ``case_id`` names no case in the suite)."""


#: All metric values are rounded to this many decimals before they are emitted.
VALUE_DECIMALS = 6

HIGHER_IS_BETTER = "higher_better"
LOWER_IS_BETTER = "lower_better"
NEUTRAL = "neutral"

#: Which way is "worse" for each metric this package can emit. Suite-level names
#: (``latency_p50``, ``error_rate``, …) are included so Milestone 6 can look up a
#: direction for aggregates too. ``tests/test_evaluators.py`` asserts that every
#: metric emitted on the fixtures appears here.
METRIC_DIRECTION: dict[str, str] = {
    # exact.py
    "task_success": HIGHER_IS_BETTER,
    "exact_match": HIGHER_IS_BETTER,
    # json_schema.py
    "json_parse_success": HIGHER_IS_BETTER,
    "json_schema_validity": HIGHER_IS_BETTER,
    "field_diff_count": LOWER_IS_BETTER,
    # tool_calls.py
    "allowed_tool_ratio": HIGHER_IS_BETTER,
    "tool_sequence_similarity": HIGHER_IS_BETTER,
    "tool_arg_diff": LOWER_IS_BETTER,
    "missing_tools": LOWER_IS_BETTER,
    "extra_tools": LOWER_IS_BETTER,
    # evidence.py
    "citation_presence": HIGHER_IS_BETTER,
    "citation_correctness": HIGHER_IS_BETTER,
    "evidence_coverage": HIGHER_IS_BETTER,
    "unsupported_claim_count": LOWER_IS_BETTER,
    # refusal.py
    "refusal_match": HIGHER_IS_BETTER,
    "risk_label_match": HIGHER_IS_BETTER,
    "risk_label_delta": LOWER_IS_BETTER,
    "explanation_quality_placeholder": NEUTRAL,
    # semantic_stub.py
    "semantic_similarity_stub": HIGHER_IS_BETTER,
    # latency.py
    "latency_ms": LOWER_IS_BETTER,
    "latency_p50": LOWER_IS_BETTER,
    "latency_p95": LOWER_IS_BETTER,
    # cost.py
    "cost_estimate_usd": LOWER_IS_BETTER,
    "cost_total_usd": LOWER_IS_BETTER,
    # errors.py
    "app_error_count": LOWER_IS_BETTER,
    "infra_error_count": NEUTRAL,
    "retry_count": LOWER_IS_BETTER,
    "tool_error_rate": LOWER_IS_BETTER,
    "error_rate": LOWER_IS_BETTER,
    "infra_error_rate": NEUTRAL,
    # completeness.py
    "trace_completeness": HIGHER_IS_BETTER,
}

#: Deterministic stand-ins, not learned judges. They are computed and reported so
#: the shape of the pipeline is real, but no claim is made about their validity,
#: and Milestone 6 must not raise a regression from them.
PLACEHOLDER_METRICS: frozenset[str] = frozenset(
    {"semantic_similarity_stub", "explanation_quality_placeholder"}
)

#: Metrics that may never raise a regression flag: the placeholders above, plus
#: the two infra counters (harness noise is surfaced, not blamed on the app).
NEVER_FLAG_METRICS: frozenset[str] = PLACEHOLDER_METRICS | {
    "infra_error_count",
    "infra_error_rate",
}


def rounded(value: float, decimals: int = VALUE_DECIMALS) -> float:
    """Round to ``decimals`` places so results are byte-stable."""
    return round(float(value), decimals)


def ratio(matched: int, total: int, *, empty: float = 1.0) -> float:
    """``matched / total``, rounded; ``empty`` when there is nothing to divide.

    ``empty`` is explicit at every call site because the vacuous case differs by
    metric: a run that called no tools used no *disallowed* tool (``1.0``), and a
    run that called no tools also had no tool *errors* (``0.0``).
    """
    if total == 0:
        return rounded(empty)
    return rounded(matched / total)


def metric(
    case_id: str,
    name: str,
    value: float,
    *,
    passed: bool | None = None,
    detail: dict[str, Any] | None = None,
    decimals: int = VALUE_DECIMALS,
) -> MetricResult:
    """Build a rounded :class:`~..schemas.MetricResult`.

    ``passed`` is the metric's own criterion (``None`` when it has none — a raw
    latency has no pass/fail, only a delta). ``detail`` carries the breakdown a
    report needs to explain *why* the number is what it is. ``decimals`` widens
    the default rounding for a metric whose units are finer than ``1e-6`` —
    ``cost_estimate_usd`` is the only one.
    """
    return MetricResult(
        case_id=case_id,
        metric=name,
        value=rounded(value, decimals),
        passed=passed,
        detail=detail or {},
    )


def canonical_json(value: Any) -> str:
    """Compact, sorted-key JSON — so dict order never changes a diff or a score."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def parse_json(text: str) -> tuple[bool, Any, str | None]:
    """Parse ``text`` as JSON. Returns ``(ok, value, error_message)``.

    An empty or whitespace-only string is a parse *failure*, not an empty object:
    a model that returned nothing did not return valid JSON.
    """
    if not text or not text.strip():
        return False, None, "empty output"
    try:
        return True, json.loads(text), None
    except (json.JSONDecodeError, ValueError) as exc:
        return False, None, str(exc)


def sequence_ratio(a: Sequence[Any], b: Sequence[Any]) -> float:
    """:class:`difflib.SequenceMatcher` similarity of two sequences, rounded.

    ``2 * M / T`` where ``M`` is the total size of the matching blocks and ``T``
    the combined length. Order-sensitive: ``[x, y]`` vs ``[y, x]`` scores ``0.5``,
    not ``1.0``. Two empty sequences score ``1.0`` (nothing expected, nothing
    missing).
    """
    if not a and not b:
        return 1.0
    return rounded(difflib.SequenceMatcher(None, list(a), list(b)).ratio())
