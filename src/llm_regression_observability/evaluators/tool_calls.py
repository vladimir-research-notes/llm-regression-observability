"""Tool-call scoring: allowed-tool usage, sequence drift, argument drift, gaps.

An agent can produce the right answer through the wrong process. These metrics
watch the process:

``allowed_tool_ratio``
    Fraction of calls that named a tool in the case's ``allowed_tools``. A single
    call to a tool outside the list drops this below ``1.0`` and is treated as a
    correctness failure, not a style issue.
``tool_sequence_similarity``
    Order-sensitive similarity between the expected tool-name sequence and the
    observed one. ``[search, lookup]`` against ``[lookup, search]`` scores
    ``0.5``: the same tools, the wrong plan.
``missing_tools`` / ``extra_tools``
    Multiset difference between expected and observed names. Order-insensitive by
    construction, so a reordering shows up in the similarity score and *only*
    there — one defect, one signal.
``tool_arg_diff``
    Normalized edit distance between expected and observed argument dicts, mean
    over matched calls, ``0.0`` when identical. Arguments are compared as
    sorted-key compact JSON so key order never registers as drift.

**What a case must declare.** ``allowed_tool_ratio`` needs ``allowed_tools``;
the sequence and gap metrics need ``expected_properties["expected_tools"]``;
``tool_arg_diff`` needs ``expected_properties["expected_tool_arguments"]``.
Absent those, the metric is not emitted (see :mod:`.base`).

Argument *drift* is more often a comparison than an expectation — a case rarely
pins every argument, but a candidate that quietly weakens a search query has
regressed against its baseline. :func:`tool_arg_diff_between` scores that pair
directly and is what Milestone 6 calls; the per-case metric is for suites that
do pin their arguments.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from ..schemas import EvalCase, MetricResult, RunRecord, ToolCallTrace
from .base import canonical_json, metric, ratio, rounded, sequence_ratio

__all__ = [
    "tool_names",
    "failed_tool_calls",
    "disallowed_tools",
    "sequence_similarity",
    "argument_distance",
    "tool_argument_diff",
    "tool_arg_diff_between",
    "missing_and_extra",
    "evaluate",
]


def tool_names(record: RunRecord) -> list[str]:
    """Observed tool names, in call order."""
    return [call.name for call in record.tool_calls]


def failed_tool_calls(record: RunRecord) -> list[ToolCallTrace]:
    """Calls whose ``status`` is ``failed``."""
    return [call for call in record.tool_calls if call.status == "failed"]


def disallowed_tools(case: EvalCase, record: RunRecord) -> list[str]:
    """Names called that the case does not permit, in call order (duplicates kept)."""
    if case.allowed_tools is None:
        return []
    allowed = set(case.allowed_tools)
    return [name for name in tool_names(record) if name not in allowed]


def sequence_similarity(expected: Sequence[str], observed: Sequence[str]) -> float:
    """Order-sensitive similarity of two tool-name sequences, in ``[0, 1]``."""
    return sequence_ratio(expected, observed)


def missing_and_extra(
    expected: Sequence[str], observed: Sequence[str]
) -> tuple[list[str], list[str]]:
    """Multiset difference: ``(expected-but-absent, present-but-unexpected)``."""
    want, got = Counter(expected), Counter(observed)
    missing = sorted((want - got).elements())
    extra = sorted((got - want).elements())
    return missing, extra


def argument_distance(expected: dict, observed: dict) -> float:
    """Normalized edit distance between two argument dicts, in ``[0, 1]``.

    Both sides are rendered as sorted-key compact JSON, so ``{"a":1,"b":2}`` and
    ``{"b":2,"a":1}`` are distance ``0``. Identical dicts short-circuit to ``0.0``
    rather than relying on the matcher returning exactly ``1.0``.
    """
    left, right = canonical_json(expected), canonical_json(observed)
    if left == right:
        return 0.0
    return rounded(1.0 - sequence_ratio(left, right))


def _by_occurrence(calls: Sequence[ToolCallTrace]) -> dict[tuple[str, int], dict]:
    """Index calls by ``(name, nth call of that name)`` so a repeated tool pairs up."""
    seen: Counter[str] = Counter()
    index: dict[tuple[str, int], dict] = {}
    for call in calls:
        key = (call.name, seen[call.name])
        seen[call.name] += 1
        index[key] = dict(call.arguments)
    return index


def tool_argument_diff(
    expected: Sequence[ToolCallTrace], observed: Sequence[ToolCallTrace]
) -> tuple[float, dict[str, Any]]:
    """Mean :func:`argument_distance` over calls matched by ``(name, occurrence)``.

    Calls present on only one side contribute nothing: they are already counted by
    ``missing_tools`` / ``extra_tools``. With no matched call the distance is
    ``0.0`` — no arguments drifted because no arguments were compared — and
    ``detail["matched_calls"]`` says so.
    """
    want, got = _by_occurrence(expected), _by_occurrence(observed)
    shared = sorted(set(want) & set(got))
    per_call = [
        {
            "tool": name,
            "occurrence": nth,
            "distance": argument_distance(want[(name, nth)], got[(name, nth)]),
        }
        for name, nth in shared
    ]
    total = sum(entry["distance"] for entry in per_call)
    value = rounded(total / len(per_call)) if per_call else 0.0
    detail = {
        "matched_calls": len(per_call),
        "per_call": [entry for entry in per_call if entry["distance"] > 0.0],
    }
    return value, detail


def tool_arg_diff_between(
    baseline: RunRecord, candidate: RunRecord
) -> tuple[float, dict[str, Any]]:
    """Argument drift of ``candidate`` against ``baseline`` — the pairwise form."""
    return tool_argument_diff(baseline.tool_calls, candidate.tool_calls)


def _expected_calls(spec: dict) -> list[ToolCallTrace]:
    """Build comparison stand-ins from ``{tool_name: {arg: value}}``."""
    return [
        ToolCallTrace(name=name, arguments=dict(args), order_index=i)
        for i, (name, args) in enumerate(spec.items())
    ]


def evaluate(case: EvalCase, record: RunRecord) -> list[MetricResult]:
    """Score tool-call behavior for one record."""
    results: list[MetricResult] = []
    observed = tool_names(record)
    properties = case.expected_properties

    if case.allowed_tools is not None:
        bad = disallowed_tools(case, record)
        results.append(
            metric(
                record.case_id,
                "allowed_tool_ratio",
                # No calls means no disallowed call: vacuously perfect.
                ratio(len(observed) - len(bad), len(observed), empty=1.0),
                passed=not bad,
                detail={
                    "call_count": len(observed),
                    "disallowed": sorted(set(bad)),
                    "allowed_tools": list(case.allowed_tools),
                },
            )
        )

    expected = properties.get("expected_tools")
    if isinstance(expected, list):
        expected_names = [str(name) for name in expected]
        similarity = sequence_similarity(expected_names, observed)
        missing, extra = missing_and_extra(expected_names, observed)
        order_matters = bool(properties.get("order_matters", False))
        results.append(
            metric(
                record.case_id,
                "tool_sequence_similarity",
                similarity,
                passed=similarity == 1.0,
                detail={
                    "expected": expected_names,
                    "observed": observed,
                    "order_matters": order_matters,
                },
            )
        )
        results.append(
            metric(
                record.case_id,
                "missing_tools",
                len(missing),
                passed=not missing,
                detail={"tools": missing},
            )
        )
        results.append(
            metric(
                record.case_id,
                "extra_tools",
                len(extra),
                passed=not extra,
                detail={"tools": extra},
            )
        )

    arg_spec = properties.get("expected_tool_arguments")
    if isinstance(arg_spec, dict):
        value, detail = tool_argument_diff(_expected_calls(arg_spec), record.tool_calls)
        results.append(
            metric(
                record.case_id,
                "tool_arg_diff",
                value,
                passed=value == 0.0,
                detail=detail,
            )
        )
    return results
