"""Tests for the deterministic evaluators (Milestone 5).

CPU-only and offline. The point of this file is that every scorer has an **exact**
expected value on the committed fixtures, not an approximate one. A metric whose
definition drifts — a percentile method swapped, a vacuous case scored ``0``
instead of ``1``, an unsupported claim counted twice — breaks a literal here,
which is the only way "the scorer works" becomes a checkable fact.

The tables in :data:`EXPECTED` are the ground truth of this milestone. They are
derived from the fixture traces and the definitions in ``docs/METRICS.md``, and
are asserted against both the baseline and the candidate of all six suites, so
each injected defect must move exactly the metric it was designed to move — and
each negative control must move nothing.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

import pytest

from llm_regression_observability import evaluators as ev
from llm_regression_observability.adapters import (
    AdapterError,
    AdapterResponse,
    MockModelAdapter,
)
from llm_regression_observability.datasets import (
    SUITE_NAMES,
    load_suite_fixture,
    resolve_fixture_roots,
)
from llm_regression_observability.evaluators import (
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
from llm_regression_observability.executor import RunConfig, execute_suite
from llm_regression_observability.schemas import (
    ErrorRecord,
    EvalCase,
    EvalSuite,
    RetrievalDoc,
    RetrievalTrace,
    RunRecord,
    ToolCallTrace,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EVALS_ROOT, DATA_ROOT = resolve_fixture_roots(None)

# Metrics that simply echo a recorded trace field; asserted separately so the
# tables below stay about *scoring* rather than about the fixtures' numbers.
_ECHOED = ("latency_ms", "cost_estimate_usd")

# Universal metrics with a clean, all-zero value. Spelled once, spread below.
CLEAN = {
    "app_error_count": 0.0,
    "infra_error_count": 0.0,
    "retry_count": 0.0,
    "tool_error_rate": 0.0,
    "trace_completeness": 1.0,
}


def _sj(task: float, parse: float, valid: float, diff: float | None) -> dict:
    scores = {
        "task_success": task,
        "json_parse_success": parse,
        "json_schema_validity": valid,
        **CLEAN,
    }
    if diff is not None:
        scores["field_diff_count"] = diff
    return scores


def _tu(task: float, allowed: float, seq: float, missing: float, extra: float) -> dict:
    return {
        "task_success": task,
        "allowed_tool_ratio": allowed,
        "tool_sequence_similarity": seq,
        "missing_tools": missing,
        "extra_tools": extra,
        **CLEAN,
    }


def _re(
    task: float, presence: float, correct: float, cover: float, unsup: float
) -> dict:
    return {
        "task_success": task,
        "citation_presence": presence,
        "citation_correctness": correct,
        "evidence_coverage": cover,
        "unsupported_claim_count": unsup,
        **CLEAN,
    }


def _rb(task: float, **rest: float) -> dict:
    return {
        "task_success": task,
        "explanation_quality_placeholder": 0.0,
        **rest,
        **CLEAN,
    }


def _at(task: float, app: float, infra: float, retries: float, tool_err: float) -> dict:
    return {
        "task_success": task,
        "allowed_tool_ratio": 1.0,
        "app_error_count": app,
        "infra_error_count": infra,
        "retry_count": retries,
        "tool_error_rate": tool_err,
        "trace_completeness": 1.0,
    }


#: ``suite -> case_id -> side -> {metric: exact value}``. Every metric a case
#: emits appears here except the two echoed trace fields.
EXPECTED: dict[str, dict[str, dict[str, dict[str, float]]]] = {
    "structured_json": {
        # The candidate's output no longer parses; no field diff is emitted,
        # because a diff against an object that does not exist is a parse failure.
        "sj_invalid_json": {
            "baseline": _sj(1.0, 1.0, 1.0, 0.0),
            "candidate": _sj(0.0, 0.0, 0.0, None),
        },
        "sj_missing_required": {
            "baseline": _sj(1.0, 1.0, 1.0, 0.0),
            "candidate": _sj(0.0, 1.0, 0.0, 1.0),  # 'score' removed
        },
        "sj_wrong_type": {
            "baseline": _sj(1.0, 1.0, 1.0, 0.0),
            "candidate": _sj(0.0, 1.0, 0.0, 1.0),  # 'score' string, not number
        },
        "sj_extra_field": {
            "baseline": _sj(1.0, 1.0, 1.0, 0.0),
            "candidate": _sj(0.0, 1.0, 0.0, 1.0),  # 'confidence' added
        },
        # Control: keys reordered, object unchanged. Nothing may move.
        "sj_control_reorder": {
            "baseline": _sj(1.0, 1.0, 1.0, 0.0),
            "candidate": _sj(1.0, 1.0, 1.0, 0.0),
        },
    },
    "tool_use": {
        # shell_exec is outside allowed_tools: 1 of 2 calls permitted.
        "tu_wrong_tool": {
            "baseline": _tu(1.0, 1.0, 1.0, 0.0, 0.0),
            "candidate": _tu(0.0, 0.5, 0.5, 1.0, 1.0),
        },
        # 'lookup' dropped: 2 of 3 names match -> 2*2/5 = 0.8.
        "tu_missing_tool": {
            "baseline": _tu(1.0, 1.0, 1.0, 0.0, 0.0),
            "candidate": _tu(0.0, 1.0, 0.8, 1.0, 0.0),
        },
        # Same tools, same order, weakened query: only task_success moves. The
        # drift itself is pairwise (see test_tool_arg_diff_between_*).
        "tu_arg_drift": {
            "baseline": _tu(1.0, 1.0, 1.0, 0.0, 0.0),
            "candidate": _tu(0.0, 1.0, 1.0, 0.0, 0.0),
        },
        # Same multiset, wrong order: gaps stay 0, similarity halves.
        "tu_order_change": {
            "baseline": _tu(1.0, 1.0, 1.0, 0.0, 0.0),
            "candidate": _tu(0.0, 1.0, 0.5, 0.0, 0.0),
        },
        "tu_control_stable": {
            "baseline": _tu(1.0, 1.0, 1.0, 0.0, 0.0),
            "candidate": _tu(1.0, 1.0, 1.0, 0.0, 0.0),
        },
    },
    "retrieval_evidence": {
        # A third, unsupported claim: 2 of 3 claims supported.
        "re_unsupported_claim": {
            "baseline": _re(1.0, 1.0, 1.0, 1.0, 0.0),
            "candidate": _re(0.0, 1.0, 1.0, 0.666667, 1.0),
        },
        # doc-b citation dropped; required_sources makes coverage 1 of 2.
        "re_missing_evidence": {
            "baseline": _re(1.0, 1.0, 1.0, 1.0, 0.0),
            "candidate": _re(0.0, 1.0, 1.0, 0.5, 0.0),
        },
        # doc-b exists, so citation_correctness stays 1.0 — but it does not
        # support the colour claim. task_success is vacuous here (the case's only
        # property is advisory), so coverage and the unsupported count carry it.
        "re_wrong_source": {
            "baseline": _re(1.0, 1.0, 1.0, 1.0, 0.0),
            "candidate": _re(1.0, 1.0, 1.0, 0.0, 1.0),
        },
        "re_control_stable": {
            "baseline": _re(1.0, 1.0, 1.0, 1.0, 0.0),
            "candidate": _re(1.0, 1.0, 1.0, 1.0, 0.0),
        },
    },
    "refusal_boundary": {
        "rb_overrefuse": {  # declined a benign question it used to answer
            "baseline": _rb(1.0, refusal_match=1.0),
            "candidate": _rb(0.0, refusal_match=0.0),
        },
        "rb_underrefuse": {  # answered a request it used to decline
            "baseline": _rb(1.0, refusal_match=1.0),
            "candidate": _rb(0.0, refusal_match=0.0),
        },
        "rb_risk_label": {  # label drifted without a refusal flip
            "baseline": _rb(1.0, risk_label_match=1.0),
            "candidate": _rb(0.0, risk_label_match=0.0),
        },
        "rb_control_stable": {
            "baseline": _rb(1.0, refusal_match=1.0),
            "candidate": _rb(1.0, refusal_match=1.0),
        },
    },
    # Every property of these cases is pairwise (final_output_stable), so
    # task_success is vacuously 1.0 on both sides and the regression lives
    # entirely in the echoed latency/cost fields.
    "latency_cost": {
        case_id: {
            "baseline": {"task_success": 1.0, **CLEAN},
            "candidate": {"task_success": 1.0, **CLEAN},
        }
        for case_id in (
            "lc_latency_spike",
            "lc_cost_spike",
            "lc_latency_control",
            "lc_cost_control",
        )
    },
    "agent_trace": {
        # Correct answer, worse process: one retried call, one app error.
        "at_more_retries": {
            "baseline": _at(1.0, 0.0, 0.0, 0.0, 0.0),
            "candidate": _at(0.0, 1.0, 0.0, 1.0, 0.333333),
        },
        "at_failed_tool": {
            "baseline": _at(1.0, 0.0, 0.0, 0.0, 0.0),
            "candidate": _at(0.0, 1.0, 0.0, 0.0, 0.5),
        },
        # The one that matters: a harness timeout degrades the trace, but
        # task_success holds and app_error_count stays 0. Research question 9.
        "at_infra_noise": {
            "baseline": _at(1.0, 0.0, 0.0, 0.0, 0.0),
            "candidate": _at(1.0, 0.0, 1.0, 0.0, 0.5),
        },
        "at_control_stable": {
            "baseline": _at(1.0, 0.0, 0.0, 0.0, 0.0),
            "candidate": _at(1.0, 0.0, 0.0, 0.0, 0.0),
        },
    },
}


@cache
def _fixture(suite: str):
    return load_suite_fixture(suite, EVALS_ROOT, DATA_ROOT)


def _case(suite: str, case_id: str) -> EvalCase:
    return next(c for c in _fixture(suite).cases if c.case_id == case_id)


def _run(suite: str, case_id: str, side: str) -> RunRecord:
    fixture = _fixture(suite)
    records = fixture.baseline if side == "baseline" else fixture.candidate
    return next(r for r in records if r.case_id == case_id)


def _scores(suite: str, case_id: str, side: str) -> dict[str, float]:
    results = ev.evaluate_case(_case(suite, case_id), _run(suite, case_id, side))
    return {r.metric: r.value for r in results if r.metric not in _ECHOED}


def _record(**overrides) -> RunRecord:
    base = {
        "run_id": "r",
        "case_id": "c",
        "app_version": "baseline",
        "model_id": "m",
        "prompt_version": "p",
        "tool_version": "t",
        "timestamp": "2026-01-01T00:00:00Z",
        "input_tokens_estimate": 10,
        "output_tokens_estimate": 5,
        "latency_ms": 100.0,
        "cost_estimate_usd": 0.001,
        "final_output": "synthetic output",
    }
    base.update(overrides)
    return RunRecord(**base)


def _eval_case(**overrides) -> EvalCase:
    base = {"case_id": "c", "version": "1", "prompt": "p", "risk_category": "benign"}
    base.update(overrides)
    return EvalCase(**base)


# --------------------------------------------------------------------------- #
# The exact-score tables: every metric, every case, both sides.
# --------------------------------------------------------------------------- #

_CASE_PARAMS = [
    (suite, case_id, side)
    for suite, cases in EXPECTED.items()
    for case_id in cases
    for side in ("baseline", "candidate")
]


@pytest.mark.parametrize("suite,case_id,side", _CASE_PARAMS)
def test_exact_scores_on_fixture(suite: str, case_id: str, side: str) -> None:
    assert _scores(suite, case_id, side) == EXPECTED[suite][case_id][side]


def test_expected_table_covers_every_fixture_case() -> None:
    for suite in SUITE_NAMES:
        assert set(EXPECTED[suite]) == {c.case_id for c in _fixture(suite).cases}


def test_latency_and_cost_metrics_echo_the_recorded_trace() -> None:
    for suite in SUITE_NAMES:
        for side in ("baseline", "candidate"):
            for case in _fixture(suite).cases:
                record = _run(suite, case.case_id, side)
                scored = ev.metrics_by_name(ev.evaluate_case(case, record))
                assert scored["latency_ms"].value == record.latency_ms
                assert scored["cost_estimate_usd"].value == record.cost_estimate_usd
                # Recorded, not measured (docs/LIMITATIONS.md).
                assert scored["latency_ms"].detail["measured"] is False


def test_every_control_case_scores_identically_on_both_sides() -> None:
    controls = [
        (suite, exp.case_id)
        for suite in SUITE_NAMES
        for exp in _fixture(suite).expectations
        if exp.is_control and exp.expected_attribution == "app"
    ]
    assert len(controls) == 7  # six suites, one control each; latency_cost has two
    for suite, case_id in controls:
        baseline = _scores(suite, case_id, "baseline")
        assert baseline == _scores(suite, case_id, "candidate"), (suite, case_id)


def test_infra_attributed_case_keeps_task_success_and_raises_no_app_error() -> None:
    # The candidate's fetch call failed on an upstream timeout. The application
    # did nothing wrong, and no app-origin metric may say otherwise.
    scores = _scores("agent_trace", "at_infra_noise", "candidate")
    assert scores["task_success"] == 1.0
    assert scores["app_error_count"] == 0.0
    assert scores["infra_error_count"] == 1.0
    record = _run("agent_trace", "at_infra_noise", "candidate")
    assert errors.attribution(record) == "infra"


# --------------------------------------------------------------------------- #
# Suite-level aggregates.
# --------------------------------------------------------------------------- #


def test_latency_percentiles_use_linear_interpolation() -> None:
    assert latency.percentile([10.0], 0.95) == 10.0
    assert latency.percentile([], 0.5) == 0.0
    assert latency.percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    # position = 0.95 * 3 = 2.85 -> 3 + 0.85 * (4 - 3)
    assert latency.percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 3.85


def test_percentile_rejects_a_quantile_outside_the_unit_interval() -> None:
    with pytest.raises(ValueError, match=r"q must be in \[0, 1\]"):
        latency.percentile([1.0], 1.5)


def test_latency_stats_on_the_latency_cost_suite() -> None:
    baseline = latency.latency_stats(_fixture("latency_cost").baseline)
    candidate = latency.latency_stats(_fixture("latency_cost").candidate)
    assert (baseline.p50_ms, baseline.p95_ms) == (150.0, 150.0)
    assert (candidate.p50_ms, candidate.p95_ms) == (165.0, 384.0)
    assert baseline.total_ms == 600.0
    assert candidate.total_ms == 900.0


def test_cost_stats_on_the_latency_cost_suite() -> None:
    baseline = cost.cost_stats(_fixture("latency_cost").baseline)
    candidate = cost.cost_stats(_fixture("latency_cost").candidate)
    assert baseline.cost_total_usd == 0.008
    assert candidate.cost_total_usd == 0.0122
    assert baseline.input_tokens_total == 520
    assert candidate.output_tokens_total == 180


def test_error_rates_separate_app_from_infra_on_the_agent_suite() -> None:
    baseline = _fixture("agent_trace").baseline
    candidate = _fixture("agent_trace").candidate
    assert errors.error_rate(baseline) == 0.0
    assert errors.infra_error_rate(baseline) == 0.0
    # Two of four candidates carry an app error; one carries an infra error.
    assert errors.error_rate(candidate) == 0.5
    assert errors.infra_error_rate(candidate) == 0.25
    assert errors.retry_total(baseline) == 0
    assert errors.retry_total(candidate) == 1


def test_error_rate_of_an_empty_run_is_zero_not_one() -> None:
    assert errors.error_rate([]) == 0.0
    assert errors.infra_error_rate([]) == 0.0


# --------------------------------------------------------------------------- #
# json_schema: the stdlib validator.
# --------------------------------------------------------------------------- #

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "score"],
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
    },
}


def test_validator_accepts_a_conforming_document() -> None:
    assert json_schema.is_valid({"title": "t", "score": 0.5}, _SCHEMA)


@pytest.mark.parametrize(
    "instance,keyword",
    [
        ({"title": "t"}, "required"),
        ({"title": 1, "score": 0.5}, "type"),
        ({"title": "t", "score": 2}, "maximum"),
        ({"title": "t", "score": -1}, "minimum"),
        ({"title": "", "score": 0.5}, "minLength"),
        ({"title": "t", "score": 0.5, "sentiment": "elated"}, "enum"),
        ({"title": "t", "score": 0.5, "extra": 1}, "additionalProperties"),
        ({"title": "t", "score": 0.5, "tags": [1]}, "type"),
        ({"title": "t", "score": 0.5, "tags": ["a", "b", "c"]}, "maxItems"),
    ],
)
def test_validator_reports_the_violated_keyword(instance: dict, keyword: str) -> None:
    violations = json_schema.validate(instance, _SCHEMA)
    assert keyword in {v.keyword for v in violations}


def test_validator_accumulates_every_violation() -> None:
    violations = json_schema.validate({"title": 1, "score": 9, "x": 0}, _SCHEMA)
    assert {v.keyword for v in violations} == {
        "type",
        "maximum",
        "additionalProperties",
    }


def test_validator_locates_a_violation_by_path() -> None:
    (violation,) = json_schema.validate({"title": "t", "score": 5}, _SCHEMA)
    assert violation.path == "$.score"
    assert violation.to_dict()["keyword"] == "maximum"


def test_validator_keeps_bool_distinct_from_int() -> None:
    # True == 1 in Python; it is not a number in JSON.
    assert not json_schema.is_valid({"title": "t", "score": True}, _SCHEMA)
    assert json_schema.is_valid(1, {"enum": [1]})
    assert not json_schema.is_valid(True, {"enum": [1]})


def test_validator_accepts_an_integer_where_a_number_is_declared() -> None:
    assert json_schema.is_valid({"title": "t", "score": 1}, _SCHEMA)
    assert not json_schema.is_valid(1.5, {"type": "integer"})


def test_validator_stops_at_a_wrong_type_rather_than_cascading() -> None:
    # A string has no 'minimum'; reporting one would be noise.
    violations = json_schema.validate("nope", {"type": "number", "minimum": 0})
    assert [v.keyword for v in violations] == ["type"]


def test_validator_supports_pattern_and_const_and_nested_objects() -> None:
    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "pattern": r"^syn-\d+$"},
            "meta": {"type": "object", "properties": {"kind": {"const": "fixture"}}},
        },
    }
    assert json_schema.is_valid({"id": "syn-1", "meta": {"kind": "fixture"}}, schema)
    (violation,) = json_schema.validate({"id": "bad"}, schema)
    assert (violation.path, violation.keyword) == ("$.id", "pattern")
    (nested,) = json_schema.validate({"meta": {"kind": "other"}}, schema)
    assert nested.path == "$.meta.kind"


def test_unsupported_keywords_are_surfaced_not_swallowed() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string", "format": "uri"}},
    }
    assert json_schema.unsupported_keywords(schema) == ["format"]


def test_every_fixture_schema_uses_only_supported_keywords() -> None:
    # If this fails, the validator is silently under-constraining a fixture.
    for suite in SUITE_NAMES:
        for case in _fixture(suite).cases:
            if case.json_schema is not None:
                assert json_schema.unsupported_keywords(case.json_schema) == []


def test_field_diff_names_missing_extra_and_type_changed_fields() -> None:
    diff = json_schema.field_diff({"title": 1, "extra": 2}, _SCHEMA)
    assert diff.missing == ("score",)
    assert diff.type_changed == ("title",)
    assert diff.extra == ("extra",)
    assert diff.count == 3


def test_field_diff_is_not_emitted_when_the_output_did_not_parse() -> None:
    case = _eval_case(json_schema=_SCHEMA)
    scored = ev.metrics_by_name(json_schema.evaluate(case, _record(final_output="{")))
    assert scored["json_parse_success"].value == 0.0
    assert scored["json_schema_validity"].value == 0.0
    assert scored["json_schema_validity"].detail["parse_failed"] is True
    assert "field_diff_count" not in scored


def test_empty_output_is_a_parse_failure_not_an_empty_object() -> None:
    ok, value, error = ev.parse_json("   ")
    assert (ok, value, error) == (False, None, "empty output")


def test_json_metrics_are_silent_for_a_case_with_no_schema() -> None:
    assert json_schema.evaluate(_eval_case(), _record(final_output='{"a": 1}')) == []


def test_parsed_output_mismatch_is_reported() -> None:
    case = _eval_case(json_schema=_SCHEMA)
    record = _record(
        final_output='{"title":"t","score":0.5}', parsed_output={"title": "x"}
    )
    scored = ev.metrics_by_name(json_schema.evaluate(case, record))
    assert scored["json_parse_success"].detail["parsed_output_mismatch"] is True
    # The schema verdict still comes from the raw string, not from parsed_output.
    assert scored["json_schema_validity"].value == 1.0


# --------------------------------------------------------------------------- #
# tool_calls.
# --------------------------------------------------------------------------- #


def test_sequence_similarity_is_order_sensitive() -> None:
    assert tool_calls.sequence_similarity(["a", "b"], ["a", "b"]) == 1.0
    assert tool_calls.sequence_similarity(["a", "b"], ["b", "a"]) == 0.5
    assert tool_calls.sequence_similarity([], []) == 1.0
    assert tool_calls.sequence_similarity(["a"], []) == 0.0


def test_missing_and_extra_are_multiset_differences() -> None:
    missing, extra = tool_calls.missing_and_extra(["a", "a", "b"], ["a", "c"])
    assert missing == ["a", "b"]
    assert extra == ["c"]
    # A reordering is not a gap.
    assert tool_calls.missing_and_extra(["a", "b"], ["b", "a"]) == ([], [])


def test_argument_distance_ignores_key_order_and_is_zero_when_equal() -> None:
    assert tool_calls.argument_distance({"a": 1, "b": 2}, {"b": 2, "a": 1}) == 0.0
    assert tool_calls.argument_distance({}, {}) == 0.0
    assert tool_calls.argument_distance({"q": "abc"}, {"q": "xyz"}) > 0.0


def test_argument_distance_on_the_injected_query_drift() -> None:
    expected = {"query": "synthetic report Q2 revenue"}
    observed = {"query": "q2"}
    assert tool_calls.argument_distance(expected, observed) == 0.509434


def test_tool_arg_diff_between_scores_the_arg_drift_case() -> None:
    baseline = _run("tool_use", "tu_arg_drift", "baseline")
    candidate = _run("tool_use", "tu_arg_drift", "candidate")
    value, detail = tool_calls.tool_arg_diff_between(baseline, candidate)
    # 'search' drifted (0.509434); 'summarize' has identical (empty) args (0.0).
    assert value == 0.254717
    assert detail["matched_calls"] == 2
    assert [entry["tool"] for entry in detail["per_call"]] == ["search"]


def test_tool_arg_diff_between_is_zero_for_a_stable_pair() -> None:
    baseline = _run("tool_use", "tu_control_stable", "baseline")
    candidate = _run("tool_use", "tu_control_stable", "candidate")
    value, _ = tool_calls.tool_arg_diff_between(baseline, candidate)
    assert value == 0.0


def test_tool_arg_diff_metric_is_emitted_when_a_case_pins_its_arguments() -> None:
    case = _eval_case(
        expected_properties={"expected_tool_arguments": {"search": {"query": "abc"}}}
    )
    record = _record(
        tool_calls=[ToolCallTrace(name="search", arguments={"query": "abc"})]
    )
    scored = ev.metrics_by_name(tool_calls.evaluate(case, record))
    assert scored["tool_arg_diff"].value == 0.0
    assert scored["tool_arg_diff"].passed is True


def test_unmatched_calls_do_not_contribute_to_the_argument_distance() -> None:
    # 'extra' exists only on one side; it is counted by extra_tools, not here.
    left = [ToolCallTrace(name="a", arguments={"q": "x"})]
    right = [
        ToolCallTrace(name="a", arguments={"q": "x"}),
        ToolCallTrace(name="extra", arguments={"q": "y"}, order_index=1),
    ]
    value, detail = tool_calls.tool_argument_diff(left, right)
    assert value == 0.0
    assert detail["matched_calls"] == 1


def test_repeated_tool_calls_pair_up_by_occurrence() -> None:
    left = [
        ToolCallTrace(name="s", arguments={"q": "one"}),
        ToolCallTrace(name="s", arguments={"q": "two"}, order_index=1),
    ]
    right = [
        ToolCallTrace(name="s", arguments={"q": "one"}),
        ToolCallTrace(name="s", arguments={"q": "different"}, order_index=1),
    ]
    _, detail = tool_calls.tool_argument_diff(left, right)
    assert detail["matched_calls"] == 2
    assert [entry["occurrence"] for entry in detail["per_call"]] == [1]


def test_allowed_tool_ratio_is_vacuously_one_when_no_tool_was_called() -> None:
    case = _eval_case(allowed_tools=["search"])
    scored = ev.metrics_by_name(tool_calls.evaluate(case, _record()))
    assert scored["allowed_tool_ratio"].value == 1.0
    assert scored["allowed_tool_ratio"].detail["call_count"] == 0


def test_disallowed_tools_are_named_in_the_detail() -> None:
    case = _eval_case(allowed_tools=["search"])
    record = _record(
        tool_calls=[
            ToolCallTrace(name="search"),
            ToolCallTrace(name="shell_exec", order_index=1),
        ]
    )
    scored = ev.metrics_by_name(tool_calls.evaluate(case, record))
    assert scored["allowed_tool_ratio"].value == 0.5
    assert scored["allowed_tool_ratio"].detail["disallowed"] == ["shell_exec"]


# --------------------------------------------------------------------------- #
# evidence.
# --------------------------------------------------------------------------- #

_DOCS = [
    RetrievalDoc(source_id="doc-a", text="A"),
    RetrievalDoc(source_id="doc-b", text="B"),
]


def test_citation_correctness_catches_a_fabricated_source_id() -> None:
    case = _eval_case(risk_category="retrieval", retrieval_docs=_DOCS)
    record = _record(
        retrieval_sources=[
            RetrievalTrace(source_id="doc-a", snippet="A", claim="x", supported=True),
            RetrievalTrace(source_id="doc-z", snippet="?", claim="y", supported=True),
        ]
    )
    scored = ev.metrics_by_name(evidence.evaluate(case, record))
    assert scored["citation_correctness"].value == 0.5
    assert scored["citation_correctness"].detail["unknown_source_ids"] == ["doc-z"]


def test_citation_presence_catches_an_uncited_claim() -> None:
    case = _eval_case(risk_category="retrieval", retrieval_docs=_DOCS)
    record = _record(
        retrieval_sources=[
            RetrievalTrace(source_id="doc-a", snippet="A", claim="x", supported=True),
            RetrievalTrace(source_id="", snippet="", claim="y", supported=False),
        ]
    )
    scored = ev.metrics_by_name(evidence.evaluate(case, record))
    assert scored["citation_presence"].value == 0.5
    assert scored["citation_presence"].detail["uncited_claims"] == ["y"]


def test_an_unmarked_claim_counts_as_unsupported() -> None:
    case = _eval_case(risk_category="retrieval", retrieval_docs=_DOCS)
    record = _record(
        retrieval_sources=[RetrievalTrace(source_id="doc-a", snippet="A", claim="x")]
    )
    scored = ev.metrics_by_name(evidence.evaluate(case, record))
    assert scored["unsupported_claim_count"].value == 1.0
    assert scored["evidence_coverage"].value == 0.0


def test_evidence_coverage_prefers_required_sources_when_declared() -> None:
    candidate = _run("retrieval_evidence", "re_missing_evidence", "candidate")
    case = _case("retrieval_evidence", "re_missing_evidence")
    scored = ev.metrics_by_name(evidence.evaluate(case, candidate))
    detail = scored["evidence_coverage"].detail
    assert detail["basis"] == "required_sources"
    assert detail["uncovered"] == ["doc-b"]


def test_evidence_coverage_falls_back_to_stated_claims() -> None:
    candidate = _run("retrieval_evidence", "re_unsupported_claim", "candidate")
    case = _case("retrieval_evidence", "re_unsupported_claim")
    scored = ev.metrics_by_name(evidence.evaluate(case, candidate))
    detail = scored["evidence_coverage"].detail
    assert detail["basis"] == "stated_claims"
    assert detail["unsupported"] == ["widget is waterproof"]


def test_evidence_metrics_are_silent_for_a_case_with_no_retrieval() -> None:
    assert evidence.evaluate(_eval_case(), _record()) == []


# --------------------------------------------------------------------------- #
# refusal.
# --------------------------------------------------------------------------- #


def test_refusal_decision_prefers_the_traces_own_label() -> None:
    record = _record(final_output="I can't help.", parsed_output={"refused": False})
    assert refusal.refusal_decision(record) == (False, "parsed_output")


def test_refusal_decision_falls_back_to_a_labelled_lexical_guess() -> None:
    declined = _record(final_output="I cannot do that.")
    assert refusal.refusal_decision(declined) == (True, "lexical_fallback")
    answered = _record(final_output="The answer is 4.")
    assert refusal.refusal_decision(answered) == (False, "lexical_fallback")


def test_refusal_match_names_the_direction_of_the_shift() -> None:
    def _shift(should_refuse: bool, refused: bool) -> str | None:
        case = _eval_case(
            risk_category="refusal",
            expected_properties={"should_refuse": should_refuse},
        )
        record = _record(parsed_output={"refused": refused})
        scored = ev.metrics_by_name(refusal.evaluate(case, record))
        return scored["refusal_match"].detail["shift"]

    assert _shift(should_refuse=False, refused=True) == "over_refusal"
    assert _shift(should_refuse=True, refused=False) == "under_refusal"
    assert _shift(should_refuse=True, refused=True) is None


def test_risk_label_delta_is_pairwise() -> None:
    baseline = _run("refusal_boundary", "rb_risk_label", "baseline")
    candidate = _run("refusal_boundary", "rb_risk_label", "candidate")
    result = refusal.risk_label_delta(baseline, candidate)
    assert result.value == 1.0
    assert result.detail == {"baseline": "benign", "candidate": "sensitive"}

    stable = _run("refusal_boundary", "rb_control_stable", "baseline")
    assert refusal.risk_label_delta(stable, stable).value == 0.0


def test_explanation_quality_is_a_declared_placeholder_that_never_flags() -> None:
    case = _case("refusal_boundary", "rb_overrefuse")
    record = _run("refusal_boundary", "rb_overrefuse", "candidate")
    scored = ev.metrics_by_name(refusal.evaluate(case, record))
    placeholder = scored["explanation_quality_placeholder"]
    assert placeholder.value == 0.0
    assert placeholder.passed is None
    assert placeholder.detail["placeholder"] is True
    assert "explanation_quality_placeholder" in ev.NEVER_FLAG_METRICS


# --------------------------------------------------------------------------- #
# exact: structural properties, opt-in exact match.
# --------------------------------------------------------------------------- #


def test_exact_match_is_opt_in_and_no_fixture_case_uses_it() -> None:
    for suite in SUITE_NAMES:
        for case in _fixture(suite).cases:
            assert "exact_match" not in case.expected_properties

    case = _eval_case(expected_properties={"exact_match": "synthetic output"})
    scored = ev.metrics_by_name(exact.evaluate(case, _record()))
    assert scored["exact_match"].value == 1.0
    assert scored["task_success"].value == 1.0

    scored = ev.metrics_by_name(exact.evaluate(case, _record(final_output="different")))
    assert scored["exact_match"].value == 0.0
    assert scored["task_success"].value == 0.0


def test_advisory_and_pairwise_properties_across_every_fixture() -> None:
    # A new unchecked property cannot appear in a fixture without failing here.
    advisory: set[str] = set()
    pairwise: set[str] = set()
    for suite in SUITE_NAMES:
        for case in _fixture(suite).cases:
            for side in ("baseline", "candidate"):
                report = exact.check_properties(case, _run(suite, case.case_id, side))
                advisory.update(report.advisory)
                pairwise.update(report.pairwise)
    assert pairwise == {"final_output_stable"}
    # 'expected_source_for_color' is prose, not a structural assertion. The defect
    # it describes is caught by evidence_coverage / unsupported_claim_count.
    assert advisory == {"expected_source_for_color"}


def test_a_case_with_only_pairwise_properties_scores_vacuously() -> None:
    case = _case("latency_cost", "lc_latency_spike")
    record = _run("latency_cost", "lc_latency_spike", "baseline")
    (result,) = exact.evaluate(case, record)
    assert result.value == 1.0
    assert result.passed is None  # nothing failed, and nothing was tested
    assert result.detail["vacuous"] is True
    assert result.detail["pairwise"] == ["final_output_stable"]


def test_an_inapplicable_property_becomes_advisory_not_a_failure() -> None:
    # no_disallowed_tools without allowed_tools cannot be decided.
    case = _eval_case(expected_properties={"no_disallowed_tools": True})
    report = exact.check_properties(case, _record())
    assert report.advisory == ["no_disallowed_tools"]
    assert report.failed == []


def test_order_matters_switches_expected_tools_to_a_sequence_check() -> None:
    record = _record(
        tool_calls=[
            ToolCallTrace(name="lookup"),
            ToolCallTrace(name="search", order_index=1),
        ]
    )
    loose = _eval_case(expected_properties={"expected_tools": ["search", "lookup"]})
    assert exact.check_properties(loose, record).checked["expected_tools"] is True

    strict = _eval_case(
        expected_properties={
            "expected_tools": ["search", "lookup"],
            "order_matters": True,
        }
    )
    assert exact.check_properties(strict, record).checked["expected_tools"] is False


def test_final_output_stable_is_the_pairwise_helper() -> None:
    stable_pair = (
        _run("latency_cost", "lc_latency_spike", "baseline"),
        _run("latency_cost", "lc_latency_spike", "candidate"),
    )
    assert exact.final_output_stable(*stable_pair) is True
    shifted_pair = (
        _run("refusal_boundary", "rb_overrefuse", "baseline"),
        _run("refusal_boundary", "rb_overrefuse", "candidate"),
    )
    assert exact.final_output_stable(*shifted_pair) is False


def test_contains_and_not_contains_are_case_insensitive_substring_checks() -> None:
    case = _eval_case(
        expected_properties={"contains": ["Synthetic"], "not_contains": ["error"]}
    )
    report = exact.check_properties(case, _record(final_output="synthetic output"))
    assert report.checked == {"contains": True, "not_contains": True}


# --------------------------------------------------------------------------- #
# semantic_stub: declared placeholder.
# --------------------------------------------------------------------------- #


def test_semantic_similarity_is_lexical_and_labelled_a_placeholder() -> None:
    assert semantic_stub.semantic_similarity_stub("a b c", "a b c") == 1.0
    assert semantic_stub.semantic_similarity_stub("", "") == 1.0
    # Punctuation and case never move the score.
    assert semantic_stub.semantic_similarity_stub("A, B!", "a b") == 1.0
    assert "semantic_similarity_stub" in ev.PLACEHOLDER_METRICS
    assert "semantic_similarity_stub" in ev.NEVER_FLAG_METRICS


def test_output_similarity_scores_the_unsupported_claim_pair() -> None:
    baseline = _run("retrieval_evidence", "re_unsupported_claim", "baseline")
    candidate = _run("retrieval_evidence", "re_unsupported_claim", "candidate")
    result = semantic_stub.output_similarity(baseline, candidate)
    assert result.value == 0.818182
    assert result.passed is None
    assert result.detail["placeholder"] is True


def test_semantic_stub_is_silent_without_a_reference_answer() -> None:
    assert semantic_stub.evaluate(_eval_case(), _record()) == []
    case = _eval_case(expected_properties={"reference_answer": "synthetic output"})
    (result,) = semantic_stub.evaluate(case, _record())
    assert result.value == 1.0


# --------------------------------------------------------------------------- #
# errors: classification and retries.
# --------------------------------------------------------------------------- #


def test_a_record_is_infra_only_when_every_error_is_infra() -> None:
    infra = ErrorRecord(kind="timeout", message="m", origin="infra")
    app = ErrorRecord(kind="tool_error", message="m", origin="app")
    assert errors.attribution(_record(errors=[infra])) == "infra"
    assert errors.attribution(_record(errors=[infra, app])) == "app"
    # A clean record attributes to 'app': there is nothing to excuse.
    assert errors.attribution(_record()) == "app"


def test_retry_count_is_structural_not_a_label() -> None:
    # A repeat of a *failed* call with the same arguments is a retry...
    retried = _record(
        tool_calls=[
            ToolCallTrace(name="s", arguments={"q": "x"}, status="failed", error="e"),
            ToolCallTrace(name="s", arguments={"q": "x"}, order_index=1),
        ]
    )
    assert errors.retry_count(retried) == 1

    # ...a repeat with different arguments is not...
    refined = _record(
        tool_calls=[
            ToolCallTrace(name="s", arguments={"q": "x"}, status="failed", error="e"),
            ToolCallTrace(name="s", arguments={"q": "y"}, order_index=1),
        ]
    )
    assert errors.retry_count(refined) == 0

    # ...and neither is a repeat of a call that succeeded.
    repeated = _record(
        tool_calls=[
            ToolCallTrace(name="s", arguments={"q": "x"}),
            ToolCallTrace(name="s", arguments={"q": "x"}, order_index=1),
        ]
    )
    assert errors.retry_count(repeated) == 0


def test_classify_errors_reports_origin_kind_and_attribution() -> None:
    record = _run("agent_trace", "at_infra_noise", "candidate")
    assert errors.classify_errors(record) == {
        "app": 0,
        "infra": 1,
        "by_kind": {"timeout": 1},
        "attribution": "infra",
    }


def test_tool_error_rate_carries_the_attribution_of_its_failure() -> None:
    record = _run("agent_trace", "at_infra_noise", "candidate")
    case = _case("agent_trace", "at_infra_noise")
    scored = ev.metrics_by_name(errors.evaluate(case, record))
    # Half the calls failed, but the failure was the harness's.
    assert scored["tool_error_rate"].value == 0.5
    assert scored["tool_error_rate"].detail["attribution"] == "infra"
    assert scored["infra_error_count"].detail["never_flags"] is True


# --------------------------------------------------------------------------- #
# completeness: a harness signal, not an application score.
# --------------------------------------------------------------------------- #


def test_every_committed_fixture_record_is_trace_complete() -> None:
    for suite in SUITE_NAMES:
        for case in _fixture(suite).cases:
            for side in ("baseline", "candidate"):
                scored = ev.metrics_by_name(
                    completeness.evaluate(case, _run(suite, case.case_id, side))
                )
                assert scored["trace_completeness"].value == 1.0, (suite, case.case_id)


def test_missing_latency_and_tokens_drop_completeness_by_exactly_two_checks() -> None:
    record = _record(latency_ms=0.0, output_tokens_estimate=0)
    scored = ev.metrics_by_name(completeness.evaluate(_eval_case(), record))
    assert scored["trace_completeness"].detail["checks_run"] == 9
    assert scored["trace_completeness"].detail["failed"] == [
        "latency_recorded",
        "tokens_recorded",
    ]
    assert scored["trace_completeness"].value == 0.777778  # 7 of 9


def test_conditional_checks_are_added_only_when_the_case_supplies_context() -> None:
    plain = completeness.completeness_checks(_eval_case(), _record())
    assert set(plain) == set(completeness.BASE_CHECKS)

    rich = completeness.completeness_checks(
        _eval_case(allowed_tools=["s"], retrieval_docs=_DOCS), _record()
    )
    expected = set(completeness.BASE_CHECKS) | set(completeness.CONDITIONAL_CHECKS)
    assert set(rich) == expected
    assert rich["tool_calls_present"] is False
    assert rich["retrieval_sources_present"] is False


def test_a_broken_json_output_is_a_regression_not_an_incomplete_trace() -> None:
    # final_output does not parse and parsed_output is null: a correct trace of a
    # real failure. Conflating it with a harness bug would hide the regression.
    case = _case("structured_json", "sj_invalid_json")
    record = _run("structured_json", "sj_invalid_json", "candidate")
    checks = completeness.completeness_checks(case, record)
    assert checks["parsed_output_consistent"] is True


def test_a_refusal_trace_may_carry_parsed_state_beside_prose() -> None:
    record = _record(final_output="I can't help.", parsed_output={"refused": True})
    checks = completeness.completeness_checks(_eval_case(), record)
    assert checks["parsed_output_consistent"] is True


def test_a_failed_tool_call_without_an_error_message_is_incomplete() -> None:
    record = _record(tool_calls=[ToolCallTrace(name="s", status="failed")])
    checks = completeness.completeness_checks(_eval_case(), record)
    assert checks["failed_tool_has_error"] is False


def test_a_naive_timestamp_is_incomplete() -> None:
    assert not completeness.completeness_checks(
        _eval_case(), _record(timestamp="2026-01-01 00:00:00")
    )["timestamp_iso8601"]


def test_the_executors_harness_failure_record_scores_below_one() -> None:
    # `--continue-on-error` emits an empty record with an infra error. It must be
    # visibly incomplete so Milestone 6 can exclude it rather than score its zeros.
    case = EvalCase(case_id="c1", version="1", prompt="p", risk_category="benign")
    suite = EvalSuite(suite_id="s", cases=[case])

    class _AdapterFails(MockModelAdapter):
        def generate(self, case: EvalCase, context) -> RunRecord:
            raise AdapterError("no recorded run")

    config = RunConfig(
        suite_path=Path("evals/suites/smoke.yaml"), run_id="x", strict=False
    )
    result = execute_suite(suite, _AdapterFails(), config)
    record = result.records[0]

    scored = ev.metrics_by_name(completeness.evaluate(case, record))
    assert scored["trace_completeness"].value == 0.777778
    assert scored["trace_completeness"].detail["failed"] == [
        "latency_recorded",
        "tokens_recorded",
    ]
    # And the failure is attributed to the harness, never to the application.
    assert errors.attribution(record) == "infra"
    error_scores = ev.metrics_by_name(errors.evaluate(case, record))
    assert error_scores["app_error_count"].value == 0.0
    assert error_scores["infra_error_count"].value == 1.0


# --------------------------------------------------------------------------- #
# Registry and metric metadata.
# --------------------------------------------------------------------------- #


def test_evaluate_case_returns_results_sorted_by_metric_name() -> None:
    case = _case("tool_use", "tu_wrong_tool")
    results = ev.evaluate_case(case, _run("tool_use", "tu_wrong_tool", "candidate"))
    names = [r.metric for r in results]
    assert names == sorted(names)
    assert all(r.case_id == "tu_wrong_tool" for r in results)


def test_evaluate_case_rejects_a_record_from_another_case() -> None:
    case = _case("tool_use", "tu_wrong_tool")
    other = _run("tool_use", "tu_missing_tool", "baseline")
    with pytest.raises(ev.EvaluatorError, match="case/record mismatch"):
        ev.evaluate_case(case, other)


def test_evaluate_run_scores_every_record_by_case_id() -> None:
    fixture = _fixture("tool_use")
    scored = ev.evaluate_run(fixture.cases, fixture.candidate)
    assert set(scored) == {c.case_id for c in fixture.cases}
    case = _case("tool_use", "tu_wrong_tool")
    record = _run("tool_use", "tu_wrong_tool", "candidate")
    assert scored["tu_wrong_tool"] == ev.evaluate_case(case, record)


def test_evaluate_run_refuses_a_record_with_no_matching_case() -> None:
    fixture = _fixture("tool_use")
    with pytest.raises(ev.EvaluatorError, match="unknown case_id 'ghost'"):
        ev.evaluate_run(fixture.cases, [_record(case_id="ghost")])


def test_every_metric_emitted_on_the_fixtures_declares_a_direction() -> None:
    emitted: set[str] = set()
    for suite in SUITE_NAMES:
        for case in _fixture(suite).cases:
            for side in ("baseline", "candidate"):
                record = _run(suite, case.case_id, side)
                emitted.update(r.metric for r in ev.evaluate_case(case, record))
    assert emitted <= set(ev.METRIC_DIRECTION)
    assert ev.PLACEHOLDER_METRICS <= set(ev.METRIC_DIRECTION)
    assert ev.PLACEHOLDER_METRICS <= ev.NEVER_FLAG_METRICS
    assert "infra_error_count" in ev.NEVER_FLAG_METRICS


def test_direction_values_come_from_the_declared_vocabulary() -> None:
    allowed = {ev.HIGHER_IS_BETTER, ev.LOWER_IS_BETTER, ev.NEUTRAL}
    assert set(ev.METRIC_DIRECTION.values()) <= allowed


def test_the_universal_metrics_appear_on_every_case() -> None:
    universal = {
        "task_success",
        "latency_ms",
        "cost_estimate_usd",
        "app_error_count",
        "infra_error_count",
        "retry_count",
        "tool_error_rate",
        "trace_completeness",
    }
    for suite in SUITE_NAMES:
        for case in _fixture(suite).cases:
            record = _run(suite, case.case_id, "baseline")
            names = {r.metric for r in ev.evaluate_case(case, record)}
            assert universal <= names, (suite, case.case_id)


# --------------------------------------------------------------------------- #
# Determinism: no ambient values, byte-stable output, no mutation.
# --------------------------------------------------------------------------- #


def test_scoring_is_byte_stable_across_calls() -> None:
    case = _case("agent_trace", "at_more_retries")
    record = _run("agent_trace", "at_more_retries", "candidate")
    first = [r.to_json() for r in ev.evaluate_case(case, record)]
    second = [r.to_json() for r in ev.evaluate_case(case, record)]
    assert first == second


def test_scoring_does_not_mutate_the_case_or_the_record() -> None:
    case = _case("tool_use", "tu_wrong_tool")
    record = _run("tool_use", "tu_wrong_tool", "candidate")
    before = (case.to_json(), record.to_json())
    ev.evaluate_case(case, record)
    assert (case.to_json(), record.to_json()) == before


def test_rounding_keeps_a_repeating_fraction_stable() -> None:
    assert ev.rounded(2 / 3) == 0.666667
    assert ev.ratio(1, 3, empty=0.0) == 0.333333
    assert ev.ratio(0, 0, empty=1.0) == 1.0
    assert ev.ratio(0, 0, empty=0.0) == 0.0


def test_cost_metric_keeps_eight_decimals_where_the_default_would_round_away() -> None:
    record = _record(cost_estimate_usd=3e-06)
    scored = ev.metrics_by_name(cost.evaluate(_eval_case(), record))
    assert scored["cost_estimate_usd"].value == 3e-06


# Importing any of these would put an ambient value or a network call within
# reach of a scored path. Checked against the parsed AST, not the raw text.
_BANNED_IMPORTS = frozenset(
    {"random", "secrets", "time", "datetime", "socket", "urllib", "http", "requests"}
)

_MILESTONE_5_SOURCES = tuple(
    f"src/llm_regression_observability/evaluators/{name}.py"
    for name in (
        "__init__",
        "base",
        "registry",
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
    )
)


def _imported_top_level_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return modules


@pytest.mark.parametrize("rel", _MILESTONE_5_SOURCES)
def test_no_ambient_time_randomness_or_network_in_scored_paths(rel: str) -> None:
    source = (REPO_ROOT / rel).read_text(encoding="utf-8")
    reached = _imported_top_level_modules(source) & _BANNED_IMPORTS
    assert not reached, f"{rel} must not import {sorted(reached)}"


def test_no_evaluator_reaches_for_a_learned_model() -> None:
    # The two stand-ins are named as such and can never raise a flag.
    assert ev.PLACEHOLDER_METRICS == {
        "semantic_similarity_stub",
        "explanation_quality_placeholder",
    }


def test_scoring_a_mock_adapter_run_needs_no_fixtures_on_disk() -> None:
    # The evaluators consume RunRecords, not files: nothing here touches the disk.
    case = EvalCase(
        case_id="c1",
        version="1",
        prompt="p",
        risk_category="benign",
        expected_properties={"contains": ["hello"]},
    )
    response = AdapterResponse(final_output="hello world")
    adapter = MockModelAdapter(responses={"c1": response})
    config = RunConfig(
        suite_path=Path("evals/suites/smoke.yaml"), adapter="mock", run_id="m"
    )
    result = execute_suite(EvalSuite(suite_id="s", cases=[case]), adapter, config)
    scored = ev.metrics_by_name(ev.evaluate_case(case, result.records[0]))
    assert scored["task_success"].value == 1.0
