"""Pairing two runs, and diffing one case: what changed, and by how much.

This module answers *what moved*. It raises no flag and knows no threshold; that
is :mod:`.regression`'s job, and keeping the two apart means the evidence a flag
rests on can be inspected (and tested) without the rule that reads it.

**Pairing is by ``case_id``, and it is total.** A case present on one side only is
never silently dropped and never scored as a failure: it is reported as
``unpaired``. An absent candidate is not a "success", and an absent baseline gives
nothing to regress against. :class:`Pairing` carries both lists.

**Changed fields are behavioral fields.** ``run_id``, ``app_version``,
``model_id``, ``timestamp`` and friends differ between *every* baseline and
candidate — that is what a comparison *is*. Reporting them as changes would bury
the one field that matters. Only :data:`~.base.BEHAVIORAL_FIELDS` are diffed.

**Some signals are pairwise and no single record can produce them.** Tool
*argument* drift, a refusal-decision flip, a risk-label shift, a claim that lost
its support, an output that is no longer stable — each is a statement about two
traces. The evaluators expose them as functions (``tool_arg_diff_between``,
``refusal_decision``, ``risk_label_delta``, ``final_output_stable``); this module
calls them once per pair and hands the results to the rules as facts.

**A dimension the case never declared is not compared.** A case with no
``json_schema`` emits no ``json_schema_validity``, so there is nothing to drop.
The four ``has_*_dimension`` predicates make that explicit, so a rule can never
read an absent metric as a zero (``docs/METRICS.md``).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..evaluators import errors as error_eval
from ..evaluators import evidence as evidence_eval
from ..evaluators import exact as exact_eval
from ..evaluators import refusal as refusal_eval
from ..evaluators import semantic_stub, tool_calls
from ..schemas import EvalCase, MetricResult, RunRecord
from .base import (
    BEHAVIORAL_FIELDS,
    COST_DECIMALS,
    RunSide,
    delta,
    metric_detail,
    metric_value,
)

__all__ = [
    "Pairing",
    "CaseDiff",
    "pair_cases",
    "changed_fields",
    "diff_case",
    "diff_sides",
]


@dataclass(frozen=True)
class Pairing:
    """Which cases both runs share, and which each has alone.

    ``paired`` keeps the baseline's run order, so a comparison artifact lists its
    cases in the order the suite declares them.
    """

    paired: tuple[str, ...] = ()
    only_baseline: tuple[str, ...] = ()
    only_candidate: tuple[str, ...] = ()

    @property
    def unpaired(self) -> tuple[str, ...]:
        """Every case that could not be compared, in a stable order."""
        return tuple(sorted(set(self.only_baseline) | set(self.only_candidate)))

    @property
    def complete(self) -> bool:
        return not self.only_baseline and not self.only_candidate


def pair_cases(baseline: RunSide, candidate: RunSide) -> Pairing:
    """Pair the two runs' records by ``case_id``, reporting both unpaired sets."""
    baseline_ids = baseline.case_ids
    candidate_ids = candidate.case_ids
    candidate_set = set(candidate_ids)
    baseline_set = set(baseline_ids)
    return Pairing(
        paired=tuple(cid for cid in baseline_ids if cid in candidate_set),
        only_baseline=tuple(cid for cid in baseline_ids if cid not in candidate_set),
        only_candidate=tuple(cid for cid in candidate_ids if cid not in baseline_set),
    )


def changed_fields(baseline: RunRecord, candidate: RunRecord) -> tuple[str, ...]:
    """Behavioral fields whose values differ, in ``RunRecord`` declaration order."""
    before, after = baseline.to_dict(), candidate.to_dict()
    return tuple(name for name in BEHAVIORAL_FIELDS if before[name] != after[name])


def _claim_texts(record: RunRecord) -> set[str]:
    return {source.claim for source in evidence_eval.claims(record) if source.claim}


def _supported_claim_texts(record: RunRecord) -> set[str]:
    return {
        source.claim
        for source in evidence_eval.claims(record)
        if source.claim and source.supported is True
    }


def _tools_from_detail(metrics: dict[str, MetricResult], name: str) -> Counter[str]:
    return Counter(metric_detail(metrics, name).get("tools", []))


def _new_elements(before: Counter[str], after: Counter[str]) -> tuple[str, ...]:
    """Multiset difference ``after - before``, sorted — what the candidate added."""
    return tuple(sorted((after - before).elements()))


@dataclass(frozen=True)
class CaseDiff:
    """Everything one paired case moved, as facts a rule can read.

    Metric deltas are ``candidate - baseline`` over the metrics *both* sides
    emitted. ``dropped_metrics`` / ``new_metrics`` name those emitted on one side
    only: a candidate whose JSON stopped parsing emits no ``field_diff_count``, and
    that absence is a signal, not a zero.
    """

    case_id: str
    case: EvalCase
    baseline: RunRecord
    candidate: RunRecord
    baseline_metrics: dict[str, MetricResult]
    candidate_metrics: dict[str, MetricResult]

    changed_fields: tuple[str, ...]
    metric_deltas: dict[str, float]
    dropped_metrics: tuple[str, ...]
    new_metrics: tuple[str, ...]

    # Raw, unit-carrying deltas.
    latency_delta: float
    cost_delta: float

    # Pairwise signals no single record can answer.
    tool_arg_diff: float
    tool_arg_detail: dict[str, Any]
    new_disallowed_tools: tuple[str, ...]
    newly_missing_tools: tuple[str, ...]
    newly_extra_tools: tuple[str, ...]
    new_unsupported_claims: tuple[str, ...]
    claims_that_lost_support: tuple[str, ...]
    refusal_flip: bool
    refusal_shift: str | None
    refusal_decision_source: str
    risk_label_shifted: bool
    new_app_error_kinds: tuple[str, ...]
    infra_error_kinds: tuple[str, ...]
    newly_failed_properties: tuple[str, ...]
    newly_passed_properties: tuple[str, ...]
    output_stable: bool
    output_similarity: float
    attribution: str

    # --- dimension gates ------------------------------------------------- #

    @property
    def has_structured_dimension(self) -> bool:
        return self.case.json_schema is not None

    @property
    def has_tool_dimension(self) -> bool:
        return (
            self.case.allowed_tools is not None
            or "expected_tools" in self.case.expected_properties
            or bool(self.baseline.tool_calls)
            or bool(self.candidate.tool_calls)
        )

    @property
    def has_evidence_dimension(self) -> bool:
        return bool(
            self.case.retrieval_docs
            or self.baseline.retrieval_sources
            or self.candidate.retrieval_sources
        )

    @property
    def has_refusal_dimension(self) -> bool:
        """A refusal flip only means something where refusal is part of the task.

        Either the suite says so (``risk_category == "refusal"``) or a trace states
        the decision outright (``parsed_output["refused"]``). Without one of those,
        the decision comes from the lexical fallback in ``evaluators.refusal`` and
        is too weak to raise a flag from.
        """
        if self.case.risk_category == "refusal":
            return True
        return any(
            isinstance(record.parsed_output, dict)
            and isinstance(record.parsed_output.get("refused"), bool)
            for record in (self.baseline, self.candidate)
        )

    # --- convenience ------------------------------------------------------ #

    def value(self, metric: str) -> tuple[float | None, float | None]:
        """``(baseline, candidate)`` values of ``metric``; ``None`` where absent."""
        return (
            metric_value(self.baseline_metrics, metric),
            metric_value(self.candidate_metrics, metric),
        )

    def dropped_from_one(self, metric: str) -> bool:
        """``True`` when ``metric`` was ``1.0`` at baseline and ``0.0`` now."""
        before, after = self.value(metric)
        return before == 1.0 and after == 0.0


def diff_case(
    case: EvalCase,
    baseline: RunRecord,
    candidate: RunRecord,
    baseline_metrics: dict[str, MetricResult],
    candidate_metrics: dict[str, MetricResult],
) -> CaseDiff:
    """Diff one paired case. Pure: reads two traces and two metric maps."""
    shared = sorted(set(baseline_metrics) & set(candidate_metrics))
    deltas = {
        name: delta(
            candidate_metrics[name].value,
            baseline_metrics[name].value,
            decimals=COST_DECIMALS if name == "cost_estimate_usd" else 6,
        )
        for name in shared
    }

    arg_diff, arg_detail = tool_calls.tool_arg_diff_between(baseline, candidate)

    base_refused, _ = refusal_eval.refusal_decision(baseline)
    cand_refused, cand_source = refusal_eval.refusal_decision(candidate)

    base_failed = set(metric_detail(baseline_metrics, "task_success").get("failed", []))
    cand_failed = set(
        metric_detail(candidate_metrics, "task_success").get("failed", [])
    )

    base_app_kinds = Counter(e.kind for e in error_eval.app_errors(baseline))
    cand_app_kinds = Counter(e.kind for e in error_eval.app_errors(candidate))

    candidate_unsupported = {
        source.claim
        for source in evidence_eval.unsupported_claims(candidate)
        if source.claim
    }

    return CaseDiff(
        case_id=case.case_id,
        case=case,
        baseline=baseline,
        candidate=candidate,
        baseline_metrics=baseline_metrics,
        candidate_metrics=candidate_metrics,
        changed_fields=changed_fields(baseline, candidate),
        metric_deltas=deltas,
        dropped_metrics=tuple(sorted(set(baseline_metrics) - set(candidate_metrics))),
        new_metrics=tuple(sorted(set(candidate_metrics) - set(baseline_metrics))),
        latency_delta=delta(candidate.latency_ms, baseline.latency_ms),
        cost_delta=delta(
            candidate.cost_estimate_usd,
            baseline.cost_estimate_usd,
            decimals=COST_DECIMALS,
        ),
        tool_arg_diff=arg_diff,
        tool_arg_detail=arg_detail,
        new_disallowed_tools=tuple(
            sorted(
                set(tool_calls.disallowed_tools(case, candidate))
                - set(tool_calls.disallowed_tools(case, baseline))
            )
        ),
        newly_missing_tools=_new_elements(
            _tools_from_detail(baseline_metrics, "missing_tools"),
            _tools_from_detail(candidate_metrics, "missing_tools"),
        ),
        newly_extra_tools=_new_elements(
            _tools_from_detail(baseline_metrics, "extra_tools"),
            _tools_from_detail(candidate_metrics, "extra_tools"),
        ),
        new_unsupported_claims=tuple(
            sorted(candidate_unsupported - _claim_texts(baseline))
        ),
        claims_that_lost_support=tuple(
            sorted(candidate_unsupported & _supported_claim_texts(baseline))
        ),
        refusal_flip=base_refused != cand_refused,
        refusal_shift=metric_detail(candidate_metrics, "refusal_match").get("shift"),
        refusal_decision_source=cand_source,
        risk_label_shifted=refusal_eval.risk_label_delta(baseline, candidate).value
        == 1.0,
        new_app_error_kinds=_new_elements(base_app_kinds, cand_app_kinds),
        infra_error_kinds=tuple(
            sorted({e.kind for e in error_eval.infra_errors(candidate)})
        ),
        newly_failed_properties=tuple(sorted(cand_failed - base_failed)),
        newly_passed_properties=tuple(sorted(base_failed - cand_failed)),
        output_stable=exact_eval.final_output_stable(baseline, candidate),
        output_similarity=semantic_stub.semantic_similarity_stub(
            baseline.final_output, candidate.final_output
        ),
        attribution=error_eval.attribution(candidate),
    )


def diff_sides(
    baseline: RunSide, candidate: RunSide, pairing: Pairing
) -> list[CaseDiff]:
    """Diff every paired case, in the baseline's run order."""
    return [
        diff_case(
            baseline.case(case_id),
            baseline.record(case_id),
            candidate.record(case_id),
            baseline.metrics_for(case_id),
            candidate.metrics_for(case_id),
        )
        for case_id in pairing.paired
    ]
