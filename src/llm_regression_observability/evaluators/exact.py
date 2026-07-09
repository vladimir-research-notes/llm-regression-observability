"""Final-answer success: structural property checks, and opt-in exact match.

``task_success`` asks whether the output satisfied the properties the case
declared — not whether it matched a golden string. Two correct answers differ
token for token; a scorer that demands equality measures phrasing, not
correctness. So ``expected_properties`` is a small closed vocabulary of
*structural* assertions (:data:`PROPERTY_CHECKS`), each delegating to the
evaluator that owns that dimension: ``json_parses`` to :mod:`.json_schema`,
``expected_tools`` to :mod:`.tool_calls`, ``should_refuse`` to :mod:`.refusal`,
and so on.

``exact_match`` exists, is opt-in, and is used sparingly: a case gets it only by
declaring ``expected_properties["exact_match"]``. None of the six fixture suites
does.

**Three kinds of property.** A case's declaration is not always something one
record can answer:

* **checkable** — decided from ``(case, record)``. These, and only these,
  determine ``task_success``.
* **pairwise** (:data:`PAIRWISE_PROPERTIES`) — a statement about *two* runs.
  ``final_output_stable`` cannot be true or false of a single trace; it is
  answered by :func:`final_output_stable` in Milestone 6.
* **advisory** — anything else the case declares. Prose for a human reader that
  this vocabulary does not encode. It is **not** silently dropped: it is listed
  in ``detail["advisory"]``, and ``tests/test_evaluators.py`` pins the exact set
  the committed fixtures produce, so a new unchecked property cannot appear
  without a test failing.

A case whose properties are all pairwise or advisory has nothing to check. Its
``task_success`` is ``1.0`` with ``passed=None`` and ``detail["vacuous"]``: no
failure was observed, and none was looked for. Saying ``0.0`` would invent a
regression; saying ``passed=True`` would claim an inspection that never happened.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..schemas import EvalCase, MetricResult, RunRecord
from . import evidence, json_schema, refusal, tool_calls
from .base import metric

__all__ = [
    "PROPERTY_CHECKS",
    "PAIRWISE_PROPERTIES",
    "MODIFIER_PROPERTIES",
    "PropertyNotApplicable",
    "PropertyReport",
    "check_properties",
    "final_output_stable",
    "evaluate",
]


class PropertyNotApplicable(Exception):
    """Raised by a check whose precondition the case does not meet (e.g.
    ``no_disallowed_tools`` on a case that declares no ``allowed_tools``). The
    property becomes advisory rather than a failure."""


#: Properties that describe a relationship between two runs, not one record.
PAIRWISE_PROPERTIES: frozenset[str] = frozenset({"final_output_stable"})

#: Properties that tune another check rather than asserting anything themselves.
MODIFIER_PROPERTIES: frozenset[str] = frozenset({"order_matters"})


# --------------------------------------------------------------------------- #
# The checks. Each is ``(case, record, expected) -> bool``.
# --------------------------------------------------------------------------- #


def _exact_match(case: EvalCase, record: RunRecord, expected: Any) -> bool:
    return record.final_output == expected


def _contains(case: EvalCase, record: RunRecord, expected: Any) -> bool:
    needles = expected if isinstance(expected, list) else [expected]
    return all(str(n).lower() in record.final_output.lower() for n in needles)


def _not_contains(case: EvalCase, record: RunRecord, expected: Any) -> bool:
    needles = expected if isinstance(expected, list) else [expected]
    return not any(str(n).lower() in record.final_output.lower() for n in needles)


def _json_parses(case: EvalCase, record: RunRecord, expected: Any) -> bool:
    ok, _, _ = json_schema.parse_final_output(record)
    return ok is bool(expected)


def _json_schema_valid(case: EvalCase, record: RunRecord, expected: Any) -> bool:
    if case.json_schema is None:
        raise PropertyNotApplicable("case declares no json_schema")
    ok, parsed, _ = json_schema.parse_final_output(record)
    valid = ok and json_schema.is_valid(parsed, case.json_schema)
    return valid is bool(expected)


def _expected_tools(case: EvalCase, record: RunRecord, expected: Any) -> bool:
    if not isinstance(expected, list):
        raise PropertyNotApplicable("expected_tools must be a list of tool names")
    observed = tool_calls.tool_names(record)
    wanted = [str(name) for name in expected]
    if case.expected_properties.get("order_matters"):
        return observed == wanted
    missing, extra = tool_calls.missing_and_extra(wanted, observed)
    return not missing and not extra


def _no_disallowed_tools(case: EvalCase, record: RunRecord, expected: Any) -> bool:
    if case.allowed_tools is None:
        raise PropertyNotApplicable("case declares no allowed_tools")
    clean = not tool_calls.disallowed_tools(case, record)
    return clean is bool(expected)


def _expected_query(case: EvalCase, record: RunRecord, expected: Any) -> bool:
    return any(call.arguments.get("query") == expected for call in record.tool_calls)


def _expected_tool_arguments(case: EvalCase, record: RunRecord, expected: Any) -> bool:
    if not isinstance(expected, dict):
        raise PropertyNotApplicable("expected_tool_arguments must be a mapping")
    for name, arguments in expected.items():
        call = next((c for c in record.tool_calls if c.name == name), None)
        if call is None or call.arguments != arguments:
            return False
    return True


def _all_claims_supported(case: EvalCase, record: RunRecord, expected: Any) -> bool:
    clean = not evidence.unsupported_claims(record)
    return clean is bool(expected)


def _required_sources(case: EvalCase, record: RunRecord, expected: Any) -> bool:
    if not isinstance(expected, list):
        raise PropertyNotApplicable("required_sources must be a list of source ids")
    supported = evidence.supported_source_ids(record)
    return all(str(source_id) in supported for source_id in expected)


def _should_refuse(case: EvalCase, record: RunRecord, expected: Any) -> bool:
    refused, _ = refusal.refusal_decision(record)
    return refused is bool(expected)


def _expected_risk_label(case: EvalCase, record: RunRecord, expected: Any) -> bool:
    return refusal.risk_label(record) == expected


def _final_answer(case: EvalCase, record: RunRecord, expected: Any) -> bool:
    return str(expected).lower() in record.final_output.lower()


def _max_tool_errors(case: EvalCase, record: RunRecord, expected: Any) -> bool:
    return len(tool_calls.failed_tool_calls(record)) <= int(expected)


#: The closed vocabulary of structural assertions a case may declare.
PROPERTY_CHECKS: dict[str, Callable[[EvalCase, RunRecord, Any], bool]] = {
    "exact_match": _exact_match,
    "contains": _contains,
    "not_contains": _not_contains,
    "json_parses": _json_parses,
    "json_schema_valid": _json_schema_valid,
    "expected_tools": _expected_tools,
    "no_disallowed_tools": _no_disallowed_tools,
    "expected_query": _expected_query,
    "expected_tool_arguments": _expected_tool_arguments,
    "all_claims_supported": _all_claims_supported,
    "required_sources": _required_sources,
    "should_refuse": _should_refuse,
    "expected_risk_label": _expected_risk_label,
    "final_answer": _final_answer,
    "max_tool_errors": _max_tool_errors,
}


# --------------------------------------------------------------------------- #
# Evaluator
# --------------------------------------------------------------------------- #


class PropertyReport:
    """How each declared property was dispositioned, in a stable order."""

    __slots__ = ("checked", "failed", "pairwise", "advisory")

    def __init__(self) -> None:
        self.checked: dict[str, bool] = {}
        self.failed: list[str] = []
        self.pairwise: list[str] = []
        self.advisory: list[str] = []

    @property
    def vacuous(self) -> bool:
        """No property could be decided from this record alone."""
        return not self.checked

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked": sorted(self.checked),
            "failed": sorted(self.failed),
            "pairwise": sorted(self.pairwise),
            "advisory": sorted(self.advisory),
            "vacuous": self.vacuous,
        }


def check_properties(case: EvalCase, record: RunRecord) -> PropertyReport:
    """Run every declared property through its check, in declaration order."""
    report = PropertyReport()
    for name, expected in case.expected_properties.items():
        if name in MODIFIER_PROPERTIES:
            continue
        if name in PAIRWISE_PROPERTIES:
            report.pairwise.append(name)
            continue
        check = PROPERTY_CHECKS.get(name)
        if check is None:
            report.advisory.append(name)
            continue
        try:
            passed = check(case, record, expected)
        except PropertyNotApplicable:
            report.advisory.append(name)
            continue
        report.checked[name] = passed
        if not passed:
            report.failed.append(name)
    return report


def final_output_stable(baseline: RunRecord, candidate: RunRecord) -> bool:
    """The pairwise ``final_output_stable`` property: did the answer text hold?"""
    return baseline.final_output == candidate.final_output


def evaluate(case: EvalCase, record: RunRecord) -> list[MetricResult]:
    """Emit ``task_success``, plus ``exact_match`` when the case opts in."""
    report = check_properties(case, record)
    success = not report.failed
    results = [
        metric(
            record.case_id,
            "task_success",
            1.0 if success else 0.0,
            # A vacuous check has no verdict: nothing failed, nothing was tested.
            passed=None if report.vacuous else success,
            detail=report.to_dict(),
        )
    ]

    if "exact_match" in case.expected_properties:
        matched = report.checked.get("exact_match", False)
        results.append(
            metric(
                record.case_id,
                "exact_match",
                1.0 if matched else 0.0,
                passed=matched,
                detail={"opt_in": True},
            )
        )
    return results
