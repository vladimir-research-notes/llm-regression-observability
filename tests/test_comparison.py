"""Tests for the baseline-vs-candidate comparison engine (Milestone 6).

CPU-only and offline. This file is where "the detector works" stops being a claim
and becomes a fact, so it is organized around the two questions that matter:

**Recall.** For every one of the 18 injected defects across the six fixture
suites, does the engine raise the regression the fixture's ``expected.json``
says a correct detector must raise — with the right category *and* the right
severity? :data:`EXPECTED_FLAGS` pins the full flag set per case, so an extra
flag is as much a failure as a missing one.

**False positives.** For every one of the 8 negative controls — a benign JSON
reorder, a stable tool trace, sub-threshold latency and cost drift, an
infra-attributed timeout — does it stay silent?

Everything else supports those two: pairing is total (an unpaired case is neither
a pass nor a fail), thresholds need both floors, only worse moves flag, and an
improved candidate raises nothing.

The scripted-regression adapter is exercised separately from the recorded fixture
pairs. It injects a *representative* defect per category rather than the fixture's
own, so it reproduces every **category** but four of its severities are stronger
than the recorded candidate's. That divergence is pinned in
:data:`SCRIPTED_SEVERITY_DIVERGENCE` rather than hand-waved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_regression_observability import comparison as cmp
from llm_regression_observability.comparison import (
    CaseDiff,
    ComparisonError,
    ComparisonMeta,
    RunSide,
    Thresholds,
    cluster_failures,
    compare,
    compare_runs,
    diff_case,
    evaluate_flags,
    load_baseline,
    load_candidate,
    pair_cases,
)
from llm_regression_observability.datasets import (
    SUITE_NAMES,
    load_eval_suite,
    load_suite_fixture,
    resolve_fixture_roots,
)
from llm_regression_observability.evaluators import (
    METRIC_DIRECTION,
    NEVER_FLAG_METRICS,
    evaluate_case,
    metrics_by_name,
)
from llm_regression_observability.evaluators.exact import PROPERTY_CHECKS
from llm_regression_observability.executor import (
    CostModel,
    RunConfig,
    build_adapter,
    execute_suite,
    run_suite,
)
from llm_regression_observability.schemas import (
    ComparisonRecord,
    ErrorRecord,
    EvalCase,
    EvalSuite,
    ReportSummary,
    RetrievalDoc,
    RetrievalTrace,
    RunRecord,
    ToolCallTrace,
)
from llm_regression_observability.traces.storage import read_json, read_jsonl

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_SUITE = REPO_ROOT / "evals" / "suites" / "smoke.yaml"
EVALS_ROOT, DATA_ROOT = resolve_fixture_roots(None)

STRUCTURED = cmp.STRUCTURED_OUTPUT_REGRESSION
TOOL = cmp.TOOL_CALL_REGRESSION
EVIDENCE = cmp.EVIDENCE_GROUNDING_REGRESSION
REFUSAL = cmp.REFUSAL_BEHAVIOR_SHIFT
LATENCY = cmp.LATENCY_REGRESSION
COST = cmp.COST_REGRESSION
ERROR_RATE = cmp.ERROR_RATE_REGRESSION
FINAL_ANSWER = cmp.FINAL_ANSWER_REGRESSION


# --------------------------------------------------------------------------- #
# Ground truth: the exact flags the recorded fixture pairs must produce.
#
# `expected.json` names the *primary* injected category per case. This table is
# stronger: it is the complete set, so a co-flag cannot appear unnoticed. The one
# case that legitimately raises two flags is `at_failed_tool`, whose candidate
# both errors (`error_rate_regression`) and answers "partial" instead of "done"
# (`final_answer_regression`) — the fixture note says so in as many words.
# --------------------------------------------------------------------------- #

EXPECTED_FLAGS: dict[str, tuple[tuple[str, str], ...]] = {
    # structured_json
    "sj_invalid_json": ((STRUCTURED, "critical"),),
    "sj_missing_required": ((STRUCTURED, "critical"),),
    "sj_wrong_type": ((STRUCTURED, "critical"),),
    "sj_extra_field": ((STRUCTURED, "critical"),),
    "sj_control_reorder": (),
    # tool_use
    "tu_wrong_tool": ((TOOL, "critical"),),
    "tu_missing_tool": ((TOOL, "critical"),),
    "tu_arg_drift": ((TOOL, "warn"),),
    "tu_order_change": ((TOOL, "warn"),),
    "tu_control_stable": (),
    # retrieval_evidence
    "re_unsupported_claim": ((EVIDENCE, "critical"),),
    "re_missing_evidence": ((EVIDENCE, "warn"),),
    "re_wrong_source": ((EVIDENCE, "warn"),),
    "re_control_stable": (),
    # refusal_boundary
    "rb_overrefuse": ((REFUSAL, "warn"),),
    "rb_underrefuse": ((REFUSAL, "critical"),),
    "rb_risk_label": ((REFUSAL, "warn"),),
    "rb_control_stable": (),
    # latency_cost
    "lc_latency_spike": ((LATENCY, "warn"),),
    "lc_cost_spike": ((COST, "warn"),),
    "lc_latency_control": (),
    "lc_cost_control": (),
    # agent_trace
    "at_more_retries": ((ERROR_RATE, "warn"),),
    "at_failed_tool": ((ERROR_RATE, "warn"), (FINAL_ANSWER, "critical")),
    "at_infra_noise": (),
    "at_control_stable": (),
}

#: The eight negative controls. None of them may raise a flag on either path.
CONTROL_CASES: frozenset[str] = frozenset(
    {
        "sj_control_reorder",
        "tu_control_stable",
        "re_control_stable",
        "rb_control_stable",
        "lc_latency_control",
        "lc_cost_control",
        "at_infra_noise",
        "at_control_stable",
    }
)

#: Cases where the scripted adapter's *representative* defect is more severe than
#: the one the recorded fixture candidate carries. The category still matches; the
#: severity is stronger because a dropped tool is worse than a reordered one, and a
#: fabricated claim is worse than a dropped citation. See `adapters/scripted_model.py`.
SCRIPTED_SEVERITY_DIVERGENCE: dict[str, tuple[str, str]] = {
    # case_id: (recorded severity, scripted severity)
    "tu_arg_drift": ("warn", "critical"),
    "tu_order_change": ("warn", "critical"),
    "re_missing_evidence": ("warn", "critical"),
    "re_wrong_source": ("warn", "critical"),
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _side(suite: str, records, cases, version: str, adapter: str) -> RunSide:
    return RunSide.from_records(
        run_id=f"{suite}-{version}-{adapter}",
        app_version=version,
        suite_id=suite,
        adapter=adapter,
        cases=cases,
        records=records,
    )


def recorded_pair(suite: str) -> tuple[RunSide, RunSide, dict[str, object]]:
    """The committed baseline / candidate fixtures for one suite, plus its oracle."""
    fixture = load_suite_fixture(suite, EVALS_ROOT, DATA_ROOT)
    cases = {case.case_id: case for case in fixture.cases}
    baseline = _side(suite, fixture.baseline, cases, "baseline", "recorded")
    candidate = _side(suite, fixture.candidate, cases, "candidate", "recorded")
    oracle = {exp.case_id: exp for exp in fixture.expectations}
    return baseline, candidate, oracle


def scripted_pair(suite: str) -> tuple[RunSide, RunSide, dict[str, object]]:
    """A baseline replayed from fixtures, and a candidate the scripted adapter built."""
    fixture = load_suite_fixture(suite, EVALS_ROOT, DATA_ROOT)
    cases = {case.case_id: case for case in fixture.cases}
    eval_suite = EvalSuite(suite_id=suite, cases=fixture.cases)

    def run(adapter: str, version: str) -> list[RunRecord]:
        config = RunConfig(suite_path=SMOKE_SUITE, adapter=adapter, app_version=version)
        return execute_suite(eval_suite, build_adapter(adapter), config).records

    baseline = _side(suite, run("recorded", "baseline"), cases, "baseline", "recorded")
    candidate = _side(
        suite,
        run("scripted-regression", "candidate"),
        cases,
        "candidate",
        "scripted-regression",
    )
    return baseline, candidate, {exp.case_id: exp for exp in fixture.expectations}


def flags_of(result, case_id: str) -> tuple[tuple[str, str], ...]:
    for record in result.records:
        if record.case_id == case_id:
            return tuple(
                (flag.category, flag.severity) for flag in record.regression_flags
            )
    raise AssertionError(f"{case_id} not in comparison")


def make_case(case_id: str = "c1", **kwargs) -> EvalCase:
    defaults = dict(case_id=case_id, version="1", prompt="p", risk_category="benign")
    return EvalCase(**{**defaults, **kwargs})


def make_record(case_id: str = "c1", version: str = "baseline", **kwargs) -> RunRecord:
    defaults = dict(
        run_id=f"r-{version}",
        case_id=case_id,
        app_version=version,
        model_id="m",
        prompt_version="p-0",
        tool_version="t-0",
        timestamp="1970-01-01T00:00:00Z",
        input_tokens_estimate=10,
        output_tokens_estimate=10,
        latency_ms=100.0,
        cost_estimate_usd=0.001,
        final_output="synthetic answer",
    )
    return RunRecord(**{**defaults, **kwargs})


def make_diff(case: EvalCase, baseline: RunRecord, candidate: RunRecord) -> CaseDiff:
    return diff_case(
        case,
        baseline,
        candidate,
        metrics_by_name(evaluate_case(case, baseline)),
        metrics_by_name(evaluate_case(case, candidate)),
    )


def flag_categories(case, baseline, candidate, thresholds=None) -> tuple[str, ...]:
    outcome = evaluate_flags(
        make_diff(case, baseline, candidate), thresholds or Thresholds()
    )
    return outcome.categories


# --------------------------------------------------------------------------- #
# Recall and false positives on the recorded fixture pairs (the oracle)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("suite", SUITE_NAMES)
def test_recorded_candidate_raises_exactly_the_expected_flags(suite: str) -> None:
    baseline, candidate, _ = recorded_pair(suite)
    result = compare(baseline, candidate)
    for record in result.records:
        assert flags_of(result, record.case_id) == EXPECTED_FLAGS[record.case_id], (
            f"{record.case_id}: {record.human_readable_summary}"
        )


@pytest.mark.parametrize("suite", SUITE_NAMES)
def test_every_injected_defect_is_detected_with_its_annotated_category(
    suite: str,
) -> None:
    """The fixture's own ground truth (`expected.json`), checked directly."""
    baseline, candidate, oracle = recorded_pair(suite)
    result = compare(baseline, candidate)
    for case_id, expectation in oracle.items():
        raised = flags_of(result, case_id)
        if expectation.is_control:
            assert raised == (), f"{case_id} is a control but raised {raised}"
        else:
            wanted = (expectation.expected_category, expectation.expected_severity)
            assert wanted in raised, f"{case_id}: wanted {wanted}, raised {raised}"


def test_the_expected_flag_table_covers_every_fixture_case() -> None:
    every_case = {
        case.case_id
        for suite in SUITE_NAMES
        for case in load_suite_fixture(suite, EVALS_ROOT, DATA_ROOT).cases
    }
    assert set(EXPECTED_FLAGS) == every_case
    assert CONTROL_CASES == {
        case_id for case_id, flags in EXPECTED_FLAGS.items() if not flags
    }


def test_no_control_case_raises_a_flag_on_either_path() -> None:
    """The false-positive count on all eight negative controls is zero."""
    for suite in SUITE_NAMES:
        for pair in (recorded_pair(suite), scripted_pair(suite)):
            baseline, candidate, _ = pair
            result = compare(baseline, candidate)
            for case_id in CONTROL_CASES & set(baseline.cases):
                assert flags_of(result, case_id) == (), (
                    f"{case_id} false-positived on the {candidate.adapter} candidate"
                )


def test_a_benign_json_reorder_changes_a_field_and_regresses_nothing() -> None:
    baseline, candidate, _ = recorded_pair("structured_json")
    result = compare(baseline, candidate)
    record = next(r for r in result.records if r.case_id == "sj_control_reorder")
    # The raw string moved; not one metric did.
    assert record.changed_fields == ["final_output"]
    assert record.regression_flags == []
    assert record.json_validity_delta == 0.0
    assert record.score_delta == 0.0
    assert "no regression" in record.human_readable_summary


# --------------------------------------------------------------------------- #
# The scripted-regression adapter: the ground-truth generator
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("suite", SUITE_NAMES)
def test_scripted_candidate_reproduces_every_expected_category(suite: str) -> None:
    baseline, candidate, oracle = scripted_pair(suite)
    result = compare(baseline, candidate)
    for case_id, expectation in oracle.items():
        raised = flags_of(result, case_id)
        if expectation.is_control:
            assert raised == (), f"{case_id} is a control but raised {raised}"
            continue
        categories = [category for category, _ in raised]
        assert expectation.expected_category in categories, (
            f"{case_id}: wanted {expectation.expected_category}, raised {raised}"
        )


@pytest.mark.parametrize("suite", SUITE_NAMES)
def test_scripted_severities_match_except_where_the_injector_is_harsher(
    suite: str,
) -> None:
    """The scripted adapter injects a *representative* defect per category.

    For four cases that defect is more severe than the one the recorded candidate
    carries — a dropped tool call rather than a reordered one, a fabricated claim
    rather than a dropped citation. The category is still right; pinning the
    divergence here keeps it a documented property rather than a surprise.
    """
    baseline, candidate, oracle = scripted_pair(suite)
    result = compare(baseline, candidate)
    for case_id, expectation in oracle.items():
        if expectation.is_control:
            continue
        severities = {
            category: severity
            for category, severity in flags_of(result, case_id)
        }
        observed = severities[expectation.expected_category]
        if case_id in SCRIPTED_SEVERITY_DIVERGENCE:
            recorded, scripted = SCRIPTED_SEVERITY_DIVERGENCE[case_id]
            assert expectation.expected_severity == recorded
            assert observed == scripted
        else:
            assert observed == expectation.expected_severity, case_id


def test_smoke_suite_end_to_end_detects_six_regressions_and_no_false_positives(
    tmp_path: Path,
) -> None:
    """The pipeline `docs/PLAN.md` §9 documents, run for real, on disk.

    The smoke suite is the cross-section where the scripted adapter's injected
    defect and the fixture's recorded one agree, so category *and* severity are
    both asserted here.
    """
    runs = tmp_path / "runs"
    for adapter, version in (
        ("recorded", "baseline"),
        ("scripted-regression", "candidate"),
    ):
        run_suite(
            RunConfig(suite_path=SMOKE_SUITE, adapter=adapter, app_version=version),
            runs,
        )

    result = compare_runs(
        runs / "smoke-baseline-recorded",
        runs / "smoke-candidate-scripted-regression",
        tmp_path / "comparisons",
    )

    assert result.summary.case_count == 8
    assert result.summary.regressed_case_count == 6
    assert result.meta.critical_count == 4
    assert result.meta.warn_count == 2
    assert result.summary.flags_by_category == {
        STRUCTURED: 1,
        TOOL: 1,
        EVIDENCE: 1,
        REFUSAL: 1,
        LATENCY: 1,
        ERROR_RATE: 1,
    }
    assert flags_of(result, "sj_invalid_json") == ((STRUCTURED, "critical"),)
    assert flags_of(result, "tu_wrong_tool") == ((TOOL, "critical"),)
    assert flags_of(result, "re_unsupported_claim") == ((EVIDENCE, "critical"),)
    assert flags_of(result, "rb_underrefuse") == ((REFUSAL, "critical"),)
    assert flags_of(result, "lc_latency_spike") == ((LATENCY, "warn"),)
    assert flags_of(result, "at_more_retries") == ((ERROR_RATE, "warn"),)
    assert flags_of(result, "at_infra_noise") == ()
    assert flags_of(result, "sj_control_reorder") == ()

    # Suite-level aggregates, over the paired cases only.
    assert result.summary.latency_p50_delta == 20.0
    assert result.summary.latency_p95_delta == 65.0
    assert result.summary.cost_total_delta == 0.0


# --------------------------------------------------------------------------- #
# Attribution: application regression vs harness noise (research question 9)
# --------------------------------------------------------------------------- #


def test_an_infra_attributed_case_is_surfaced_and_never_flagged() -> None:
    baseline, candidate, _ = recorded_pair("agent_trace")
    result = compare(baseline, candidate)
    record = next(r for r in result.records if r.case_id == "at_infra_noise")

    # A failed tool call and a red trace...
    assert record.changed_fields == ["tool_calls", "errors"]
    # ...but the error is origin=infra, so nothing is blamed on the application.
    assert record.regression_flags == []
    assert result.meta.infra_attributed_case_ids == ["at_infra_noise"]
    assert "infra-origin" in record.human_readable_summary


def test_infra_attribution_withholds_a_flag_it_would_otherwise_raise() -> None:
    """A degradation on an infra-attributed record is recorded, not dropped.

    `at_infra_noise` moves no metric a rule reads, so the suppression never bites
    on the fixtures. Here it does: the candidate is 4x slower *and* infra-errored.
    """
    case = make_case()
    baseline = make_record(latency_ms=100.0)
    candidate = make_record(
        version="candidate",
        latency_ms=400.0,
        errors=[ErrorRecord(kind="timeout", message="upstream", origin="infra")],
    )

    outcome = evaluate_flags(make_diff(case, baseline, candidate), Thresholds())
    assert outcome.flags == ()
    assert outcome.suppressed == (LATENCY,)

    # The same degradation with an *app*-origin error does flag.
    app_side = make_record(
        version="candidate",
        latency_ms=400.0,
        errors=[ErrorRecord(kind="tool_error", message="boom", origin="app")],
    )
    assert set(flag_categories(case, baseline, app_side)) == {LATENCY, ERROR_RATE}


def test_a_mixed_origin_record_is_attributed_to_the_application() -> None:
    """One app error makes the whole record `app`: under doubt, flag."""
    case = make_case()
    baseline = make_record()
    candidate = make_record(
        version="candidate",
        latency_ms=400.0,
        errors=[
            ErrorRecord(kind="timeout", message="upstream", origin="infra"),
            ErrorRecord(kind="tool_error", message="boom", origin="app"),
        ],
    )
    outcome = evaluate_flags(make_diff(case, baseline, candidate), Thresholds())
    assert outcome.suppressed == ()
    assert LATENCY in outcome.categories


# --------------------------------------------------------------------------- #
# Direction awareness: only *worse* moves flag
# --------------------------------------------------------------------------- #


def test_an_improved_candidate_raises_nothing() -> None:
    case = make_case(
        json_schema={
            "type": "object",
            "properties": {"a": {"type": "integer"}},
            "required": ["a"],
        },
        expected_properties={"json_parses": True, "json_schema_valid": True},
    )
    broken = make_record(
        final_output='{"a": "one"}', latency_ms=500.0, cost_estimate_usd=0.01
    )
    fixed = make_record(
        version="candidate",
        final_output='{"a": 1}',
        latency_ms=50.0,
        cost_estimate_usd=0.001,
    )
    assert flag_categories(case, broken, fixed) == ()


def test_a_faster_and_cheaper_candidate_raises_nothing() -> None:
    case = make_case()
    slow = make_record(latency_ms=1000.0, cost_estimate_usd=0.05)
    fast = make_record(version="candidate", latency_ms=10.0, cost_estimate_usd=0.0001)
    assert flag_categories(case, slow, fast) == ()


def test_fewer_app_errors_is_not_a_regression() -> None:
    case = make_case()
    noisy = make_record(
        errors=[ErrorRecord(kind="tool_error", message="x", origin="app")]
    )
    clean = make_record(version="candidate")
    assert flag_categories(case, noisy, clean) == ()


# --------------------------------------------------------------------------- #
# Thresholds: both floors, direction, and configurability
# --------------------------------------------------------------------------- #


# Floors: +50 ms absolute AND +25% relative. Both must cross.
@pytest.mark.parametrize(
    ("baseline_ms", "candidate_ms", "flags"),
    [
        (150.0, 180.0, False),  # +30 ms, +20%   — neither floor
        (1000.0, 1060.0, False),  # +60 ms, +6%  — absolute only
        (10.0, 30.0, False),  # +20 ms, +200%    — relative only
        (150.0, 150.0, False),  # unchanged
        (420.0, 150.0, False),  # faster: never a regression
        (150.0, 210.0, True),  # +60 ms, +40%    — both
        (150.0, 420.0, True),  # +270 ms, +180%  — both
        (0.0, 100.0, True),  # from a zero baseline: the absolute rule decides
    ],
)
def test_latency_needs_both_a_relative_and_an_absolute_move(
    baseline_ms: float, candidate_ms: float, flags: bool
) -> None:
    case = make_case()
    baseline = make_record(latency_ms=baseline_ms)
    candidate = make_record(version="candidate", latency_ms=candidate_ms)
    raised = flag_categories(case, baseline, candidate)
    assert (LATENCY in raised) is flags


# Floors: +$0.0005 absolute AND +20% relative. Both must cross.
@pytest.mark.parametrize(
    ("baseline_usd", "candidate_usd", "flags"),
    [
        (0.002, 0.0022, False),  # +$0.0002, +10%   — neither floor
        (0.05, 0.0506, False),  # +$0.0006, +1.2%   — absolute only
        (0.0001, 0.0004, False),  # +$0.0003, +300% — relative only
        (0.002, 0.001, False),  # cheaper: never a regression
        (0.002, 0.0026, True),  # +$0.0006, +30%    — both
        (0.002, 0.006, True),  # +$0.004, +200%     — both
    ],
)
def test_cost_needs_both_a_relative_and_an_absolute_move(
    baseline_usd: float, candidate_usd: float, flags: bool
) -> None:
    case = make_case()
    baseline = make_record(cost_estimate_usd=baseline_usd)
    candidate = make_record(version="candidate", cost_estimate_usd=candidate_usd)
    raised = flag_categories(case, baseline, candidate)
    assert (COST in raised) is flags


def test_a_suite_can_raise_its_own_latency_tolerance() -> None:
    case = make_case()
    baseline = make_record(latency_ms=150.0)
    candidate = make_record(version="candidate", latency_ms=420.0)
    assert LATENCY in flag_categories(case, baseline, candidate)
    lenient = Thresholds(latency_p95_rel=5.0, latency_abs_ms=50.0)
    assert LATENCY not in flag_categories(case, baseline, candidate, lenient)


def test_thresholds_are_read_from_the_suite_manifest() -> None:
    suite = load_eval_suite(SMOKE_SUITE)
    thresholds = Thresholds.from_mapping(suite.thresholds)
    assert thresholds.latency_p95_rel == 0.25
    assert thresholds.latency_abs_ms == 50.0
    assert thresholds.cost_rel == 0.20
    assert thresholds.cost_abs_usd == 0.0005
    assert thresholds.tool_sequence_similarity == 0.80


def test_an_unknown_threshold_is_an_error_not_a_shrug() -> None:
    with pytest.raises(ComparisonError, match="unknown threshold"):
        Thresholds.from_mapping({"latency_p95_rel": 0.3, "laytency_abs_ms": 50})


@pytest.mark.parametrize(
    "bad", [{"cost_rel": -0.1}, {"cost_rel": "cheap"}, {"cost_rel": True}]
)
def test_a_malformed_threshold_is_rejected(bad: dict) -> None:
    with pytest.raises(ComparisonError):
        Thresholds.from_mapping(bad)


def test_empty_thresholds_fall_back_to_the_documented_defaults() -> None:
    assert Thresholds.from_mapping({}) == Thresholds()
    assert Thresholds.from_mapping(None) == Thresholds()


# --------------------------------------------------------------------------- #
# One defect, one signal: category ownership of a task_success drop
# --------------------------------------------------------------------------- #


def test_every_checkable_property_is_owned_by_exactly_one_category() -> None:
    assert set(PROPERTY_CHECKS) <= set(cmp.PROPERTY_CATEGORY)
    assert set(cmp.PROPERTY_CATEGORY.values()) <= set(cmp.REGRESSION_CATEGORIES)


def test_no_rule_can_flag_on_a_never_flag_metric() -> None:
    assert not (cmp.TRIGGER_METRICS & NEVER_FLAG_METRICS)


def test_every_trigger_metric_is_a_known_metric_or_a_named_pairwise_signal() -> None:
    # `refusal_decision` is the verdict of `evaluators.refusal.refusal_decision()`
    # on each side, not an emitted MetricResult, so it has no METRIC_DIRECTION row.
    pairwise = {"refusal_decision"}
    assert cmp.TRIGGER_METRICS - pairwise <= set(METRIC_DIRECTION)


def test_a_structured_defect_does_not_also_report_a_final_answer_regression() -> None:
    """`task_success` is the union of the case's properties; flagging its drop
    unconditionally would report every schema defect twice."""
    baseline, candidate, _ = recorded_pair("structured_json")
    result = compare(baseline, candidate)
    record = next(r for r in result.records if r.case_id == "sj_missing_required")
    assert record.score_delta == -1.0  # task_success really did drop 1 -> 0
    assert [f.category for f in record.regression_flags] == [STRUCTURED]


def test_a_process_only_property_failure_reports_the_process_category() -> None:
    """`at_more_retries` keeps its answer ("done") but violates `max_tool_errors`.

    The task-success drop belongs to `error_rate_regression`, which owns that
    property — not to `final_answer_regression`, whose answer-level properties all
    still hold.
    """
    baseline, candidate, _ = scripted_pair("agent_trace")
    result = compare(baseline, candidate)
    record = next(r for r in result.records if r.case_id == "at_more_retries")
    assert record.score_delta == -1.0
    assert [f.category for f in record.regression_flags] == [ERROR_RATE]


def test_an_answer_level_property_failure_reports_a_final_answer_regression() -> None:
    """`at_failed_tool`'s recorded candidate answers "partial": the answer degraded."""
    baseline, candidate, _ = recorded_pair("agent_trace")
    result = compare(baseline, candidate)
    record = next(r for r in result.records if r.case_id == "at_failed_tool")
    assert [f.category for f in record.regression_flags] == [ERROR_RATE, FINAL_ANSWER]


def test_a_case_with_only_pairwise_properties_never_drops_task_success() -> None:
    """`lc_*` declare `final_output_stable`, which one record cannot answer."""
    baseline, candidate, _ = recorded_pair("latency_cost")
    result = compare(baseline, candidate)
    for record in result.records:
        assert record.score_delta == 0.0
        assert FINAL_ANSWER not in [f.category for f in record.regression_flags]


def test_a_zero_score_delta_does_not_mean_no_regression() -> None:
    """The Milestone 5 review asked this directly.

    `lc_*` score `task_success = 1.0` vacuously on both sides, so `score_delta` is
    `0.0` for every one of them — and two of them still regress, on latency and on
    cost. A rule that read `score_delta == 0` as "clean" would miss both.
    """
    baseline, candidate, _ = recorded_pair("latency_cost")
    result = compare(baseline, candidate)
    spiked = [r for r in result.records if r.regression_flags]
    assert {r.case_id for r in spiked} == {"lc_latency_spike", "lc_cost_spike"}
    assert all(r.score_delta == 0.0 for r in spiked)


def test_a_permitted_extra_field_is_not_a_structured_output_regression() -> None:
    """`field_diff_count` rises, `json_schema_validity` holds: the field is legal.

    `field_diff` reports extra keys regardless of `additionalProperties`, so it is
    deliberately not a trigger. The Milestone 5 review flagged this risk.
    """
    schema = {
        "type": "object",
        "properties": {"a": {"type": "integer"}},
        "required": ["a"],
        "additionalProperties": True,
    }
    case = make_case(risk_category="structured", json_schema=schema)
    baseline = make_record(final_output='{"a": 1}')
    candidate = make_record(version="candidate", final_output='{"a": 1, "b": 2}')

    diff = make_diff(case, baseline, candidate)
    assert diff.metric_deltas["field_diff_count"] == 1.0  # the shape really moved
    assert diff.value("json_schema_validity") == (1.0, 1.0)  # and stayed legal
    assert evaluate_flags(diff, Thresholds()).categories == ()

    # ...whereas the same extra field under `additionalProperties: false` is illegal.
    strict = dict(schema, additionalProperties=False)
    strict_case = make_case(risk_category="structured", json_schema=strict)
    outcome = evaluate_flags(make_diff(strict_case, baseline, candidate), Thresholds())
    assert outcome.categories == (STRUCTURED,)
    assert outcome.flags[0].severity == "critical"


def test_an_adapter_failure_record_is_attributed_to_infra_and_raises_nothing() -> None:
    """The executor's `--continue-on-error` record, as Milestone 6 sees it.

    It has no latency, no tokens, no output — every metric it emits looks like a
    catastrophic regression. Its single error is `origin=infra`, so nothing is
    blamed on the application. The Milestone 4 and 5 reviews left this open.
    """
    case = make_case()
    baseline = make_record()
    failed = make_record(
        version="candidate",
        latency_ms=0.0,
        cost_estimate_usd=0.0,
        input_tokens_estimate=0,
        output_tokens_estimate=0,
        final_output="",
        errors=[
            ErrorRecord(kind="adapter_error", message="adapter blew up", origin="infra")
        ],
    )
    outcome = evaluate_flags(make_diff(case, baseline, failed), Thresholds())
    assert outcome.flags == ()
    assert outcome.entries == ()


# --------------------------------------------------------------------------- #
# Per-dimension rules on hand-built pairs
# --------------------------------------------------------------------------- #


def test_a_disallowed_tool_is_critical_and_a_reorder_is_a_warning() -> None:
    def call(name: str, index: int, **args) -> ToolCallTrace:
        return ToolCallTrace(name=name, arguments=args, order_index=index)

    case = make_case(
        risk_category="tool_use",
        allowed_tools=["search", "lookup"],
        expected_properties={"expected_tools": ["search", "lookup"]},
    )
    baseline = make_record(tool_calls=[call("search", 0), call("lookup", 1)])

    disallowed = make_record(
        version="candidate", tool_calls=[call("search", 0), call("shell_exec", 1)]
    )
    outcome = evaluate_flags(make_diff(case, baseline, disallowed), Thresholds())
    assert outcome.categories == (TOOL,)
    assert outcome.flags[0].severity == "critical"
    assert "shell_exec" in outcome.flags[0].explanation

    reordered = make_record(
        version="candidate", tool_calls=[call("lookup", 0), call("search", 1)]
    )
    outcome = evaluate_flags(make_diff(case, baseline, reordered), Thresholds())
    assert outcome.categories == (TOOL,)
    assert outcome.flags[0].severity == "warn"
    assert outcome.entries[0].signature == "sequence_drift"


def test_a_new_unsupported_claim_is_critical_but_a_lost_citation_is_a_warning() -> None:
    docs = [
        RetrievalDoc(source_id="doc-a", text="blue"),
        RetrievalDoc(source_id="doc-b", text="2kg"),
    ]
    blue = RetrievalTrace(
        source_id="doc-a", snippet="blue", claim="is blue", supported=True
    )
    case = make_case(risk_category="retrieval", retrieval_docs=docs)
    baseline = make_record(retrieval_sources=[blue])
    fabricated = make_record(
        version="candidate",
        retrieval_sources=[
            blue,
            RetrievalTrace(
                source_id="doc-a",
                snippet="?",
                claim="is waterproof",
                supported=False,
            ),
        ],
    )
    outcome = evaluate_flags(make_diff(case, baseline, fabricated), Thresholds())
    assert outcome.categories == (EVIDENCE,)
    assert outcome.flags[0].severity == "critical"
    assert outcome.entries[0].signature == "new_unsupported_claim"

    # The same claim, no longer supported: the answer did not invent anything new.
    unsupported = make_record(
        version="candidate",
        retrieval_sources=[
            RetrievalTrace(
                source_id="doc-b", snippet="?", claim="is blue", supported=False
            ),
        ],
    )
    outcome = evaluate_flags(make_diff(case, baseline, unsupported), Thresholds())
    assert outcome.flags[0].severity == "warn"


def test_under_refusal_is_critical_and_over_refusal_is_a_warning() -> None:
    refuse_case = make_case(
        risk_category="refusal", expected_properties={"should_refuse": True}
    )
    refused = make_record(final_output="I can't help.", parsed_output={"refused": True})
    complied = make_record(
        version="candidate",
        final_output="Here it is.",
        parsed_output={"refused": False},
    )
    outcome = evaluate_flags(make_diff(refuse_case, refused, complied), Thresholds())
    assert outcome.categories == (REFUSAL,)
    assert outcome.flags[0].severity == "critical"
    assert outcome.entries[0].signature == "under_refusal"

    answer_case = make_case(
        risk_category="refusal", expected_properties={"should_refuse": False}
    )
    outcome = evaluate_flags(make_diff(answer_case, complied, refused), Thresholds())
    assert outcome.flags[0].severity == "warn"
    assert outcome.entries[0].signature == "over_refusal"


def test_a_refusal_flip_outside_the_refusal_suite_does_not_flag() -> None:
    """The lexical fallback is too weak to raise a flag from on its own."""
    case = make_case(risk_category="benign")
    answered = make_record(final_output="The answer is 42.")
    refused = make_record(
        version="candidate", final_output="I'm sorry, but I can't help."
    )
    assert REFUSAL not in flag_categories(case, answered, refused)


def test_a_retry_raises_the_error_rate_category_not_the_tool_category() -> None:
    case = make_case(risk_category="agent", allowed_tools=["search"])
    baseline = make_record(
        tool_calls=[ToolCallTrace(name="search", arguments={"q": "x"}, order_index=0)]
    )
    candidate = make_record(
        version="candidate",
        tool_calls=[
            ToolCallTrace(
                name="search", arguments={"q": "x"}, order_index=0, status="failed",
                error="transient",
            ),
            ToolCallTrace(name="search", arguments={"q": "x"}, order_index=1),
        ],
        errors=[ErrorRecord(kind="tool_retry", message="retried", origin="app")],
    )
    outcome = evaluate_flags(make_diff(case, baseline, candidate), Thresholds())
    assert outcome.categories == (ERROR_RATE,)
    assert outcome.flags[0].severity == "warn"


# --------------------------------------------------------------------------- #
# Pairing: unpaired cases are reported, never scored
# --------------------------------------------------------------------------- #


def test_unpaired_cases_are_reported_on_both_sides() -> None:
    cases = {cid: make_case(cid) for cid in ("a", "b", "c", "d")}
    baseline = _side(
        "s",
        [make_record(cid) for cid in ("a", "b", "c")],
        cases,
        "baseline",
        "recorded",
    )
    candidate = _side(
        "s",
        [make_record(cid, "candidate") for cid in ("b", "c", "d")],
        cases,
        "candidate",
        "mock",
    )
    pairing = pair_cases(baseline, candidate)
    assert pairing.paired == ("b", "c")
    assert pairing.only_baseline == ("a",)
    assert pairing.only_candidate == ("d",)
    assert pairing.unpaired == ("a", "d")
    assert not pairing.complete

    result = compare(baseline, candidate)
    assert result.summary.case_count == 2  # only the paired cases are scored
    assert result.meta.unpaired_baseline_case_ids == ["a"]
    assert result.meta.unpaired_candidate_case_ids == ["d"]
    assert [r.case_id for r in result.records] == ["b", "c"]


def test_pairing_keeps_the_baseline_run_order() -> None:
    cases = {cid: make_case(cid) for cid in ("z", "a", "m")}
    baseline = _side(
        "s", [make_record(c) for c in ("z", "a", "m")], cases, "baseline", "recorded"
    )
    candidate = _side(
        "s",
        [make_record(c, "candidate") for c in ("m", "z", "a")],
        cases,
        "candidate",
        "mock",
    )
    assert pair_cases(baseline, candidate).paired == ("z", "a", "m")


# --------------------------------------------------------------------------- #
# Diffing: behavioral fields only
# --------------------------------------------------------------------------- #


def test_changed_fields_ignores_run_identity_and_provenance() -> None:
    baseline = make_record()
    candidate = RunRecord(
        run_id="totally-different",
        case_id="c1",
        app_version="candidate",
        model_id="another-model",
        prompt_version="p-9",
        tool_version="t-9",
        timestamp="2020-01-01T00:00:00Z",
        input_tokens_estimate=10,
        output_tokens_estimate=10,
        latency_ms=100.0,
        cost_estimate_usd=0.001,
        final_output="synthetic answer",
    )
    assert cmp.changed_fields(baseline, candidate) == ()
    assert set(cmp.IDENTITY_FIELDS) | set(cmp.PROVENANCE_FIELDS) >= {
        "run_id",
        "app_version",
        "model_id",
        "timestamp",
    }
    assert not set(cmp.BEHAVIORAL_FIELDS) & set(cmp.PROVENANCE_FIELDS)


def test_a_metric_emitted_on_one_side_only_is_reported_as_dropped() -> None:
    """The candidate's unparseable JSON emits no `field_diff_count`."""
    baseline, candidate, _ = recorded_pair("structured_json")
    diff = cmp.diff_sides(baseline, candidate, pair_cases(baseline, candidate))[0]
    assert diff.case_id == "sj_invalid_json"
    assert diff.dropped_metrics == ("field_diff_count",)
    assert "field_diff_count" not in diff.metric_deltas


def test_relative_change_from_a_zero_baseline_is_infinite_not_zero() -> None:
    assert cmp.relative(1.0, 0.0) == float("inf")
    assert cmp.relative(0.0, 0.0) == 0.0
    assert cmp.relative(150.0, 100.0) == 0.5


# --------------------------------------------------------------------------- #
# Clustering
# --------------------------------------------------------------------------- #


def test_cases_that_failed_the_same_way_land_in_one_cluster() -> None:
    baseline, candidate, _ = recorded_pair("structured_json")
    clusters = {c.cluster_id: c for c in compare(baseline, candidate).summary.clusters}
    assert clusters[f"{STRUCTURED}__schema_invalid"].case_ids == [
        "sj_extra_field",
        "sj_missing_required",
        "sj_wrong_type",
    ]
    # A parse failure is a different root cause from a validity failure.
    assert clusters[f"{STRUCTURED}__parse_failure"].case_ids == ["sj_invalid_json"]


def test_clustering_carries_the_offending_symbol() -> None:
    baseline, candidate, _ = recorded_pair("tool_use")
    ids = [c.cluster_id for c in compare(baseline, candidate).summary.clusters]
    assert f"{TOOL}__disallowed_tool-shell_exec" in ids
    assert f"{TOOL}__missing_tool-lookup" in ids


def test_a_case_that_failed_two_ways_appears_in_two_clusters() -> None:
    baseline, candidate, _ = recorded_pair("agent_trace")
    clusters = compare(baseline, candidate).summary.clusters
    holding = [c.cluster_id for c in clusters if "at_failed_tool" in c.case_ids]
    assert len(holding) == 2
    assert {c.split("__")[0] for c in holding} == {ERROR_RATE, FINAL_ANSWER}


def test_clusters_are_emitted_in_taxonomy_order_with_sorted_case_ids() -> None:
    baseline, candidate, _ = recorded_pair("agent_trace")
    clusters = compare(baseline, candidate).summary.clusters
    order = [cmp.REGRESSION_CATEGORIES.index(c.category) for c in clusters]
    assert order == sorted(order)
    for cluster in clusters:
        assert cluster.case_ids == sorted(cluster.case_ids)


def test_an_empty_comparison_produces_no_clusters() -> None:
    assert cluster_failures([]) == []


def test_a_cluster_id_is_safe_as_a_path_segment() -> None:
    cluster_id = cmp.cluster_id_for(TOOL, "disallowed_tool:shell exec/../etc")
    assert "/" not in cluster_id and " " not in cluster_id


# --------------------------------------------------------------------------- #
# Comparability
# --------------------------------------------------------------------------- #


def test_two_runs_of_different_suites_cannot_be_compared() -> None:
    cases = {"a": make_case("a")}
    left = _side("suite-one", [make_record("a")], cases, "baseline", "recorded")
    right = _side(
        "suite-two", [make_record("a", "candidate")], cases, "candidate", "mock"
    )
    with pytest.raises(ComparisonError, match="suite mismatch"):
        compare(left, right)


def test_a_run_cannot_regress_against_itself() -> None:
    cases = {"a": make_case("a")}
    side = _side("s", [make_record("a")], cases, "baseline", "recorded")
    with pytest.raises(ComparisonError, match="same run"):
        compare(side, side)


def test_a_cost_model_mismatch_disables_the_cost_rule_and_says_so() -> None:
    """A cost delta across two price lists measures the price list."""
    cases = {"a": make_case("a")}
    baseline = RunSide.from_records(
        run_id="b", app_version="baseline", suite_id="s", adapter="recorded",
        cases=cases, records=[make_record("a", cost_estimate_usd=0.002)],
        cost_model=CostModel().to_dict(),
    )
    candidate = RunSide.from_records(
        run_id="c", app_version="candidate", suite_id="s", adapter="recorded",
        cases=cases, records=[make_record("a", "candidate", cost_estimate_usd=0.006)],
        cost_model=CostModel(input_usd_per_1k=0.03, output_usd_per_1k=0.15).to_dict(),
    )
    result = compare(baseline, candidate)
    notes = result.meta.comparability_notes
    assert result.meta.cost_comparable is False
    assert any("different cost models" in note for note in notes)
    assert flags_of(result, "a") == ()  # +200% cost, and deliberately not flagged
    # ...while the same pair priced identically does flag.
    same_price = RunSide.from_records(
        run_id="c", app_version="candidate", suite_id="s", adapter="recorded",
        cases=cases, records=[make_record("a", "candidate", cost_estimate_usd=0.006)],
        cost_model=CostModel().to_dict(),
    )
    assert flags_of(compare(baseline, same_price), "a") == ((COST, "warn"),)


def test_a_duplicate_case_in_a_run_is_rejected() -> None:
    cases = {"a": make_case("a")}
    with pytest.raises(ComparisonError, match="duplicate record"):
        _side(
            "s",
            [make_record("a"), make_record("a")],
            cases,
            "baseline",
            "recorded",
        )


def test_a_record_for_an_unknown_case_is_rejected() -> None:
    cases = {"a": make_case("a")}
    with pytest.raises(ComparisonError, match="unknown case"):
        _side("s", [make_record("ghost")], cases, "baseline", "recorded")


# --------------------------------------------------------------------------- #
# Artifacts: shape, validity, and byte stability
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def smoke_runs(tmp_path_factory) -> Path:
    runs = tmp_path_factory.mktemp("runs")
    for adapter, version in (
        ("recorded", "baseline"),
        ("scripted-regression", "candidate"),
    ):
        run_suite(
            RunConfig(suite_path=SMOKE_SUITE, adapter=adapter, app_version=version),
            runs,
        )
    return runs


def test_the_three_artifacts_are_written_and_reload_against_their_schemas(
    smoke_runs: Path, tmp_path: Path
) -> None:
    result = compare_runs(
        smoke_runs / "smoke-baseline-recorded",
        smoke_runs / "smoke-candidate-scripted-regression",
        tmp_path,
    )
    out = tmp_path / "smoke-baseline-vs-candidate"
    assert result.out_dir == out

    records = read_jsonl(out / "comparison.jsonl", ComparisonRecord)
    summary = read_json(out / "summary.json", ReportSummary)
    meta = read_json(out / "comparison_meta.json", ComparisonMeta)

    assert len(records) == 8
    assert summary.comparison_id == meta.comparison_id == "smoke-baseline-vs-candidate"
    assert meta.records_sha256 == result.meta.records_sha256
    assert meta.thresholds["latency_abs_ms"] == 50.0
    for record in records:
        assert record.baseline_run_id == "smoke-baseline-recorded"
        assert record.candidate_run_id == "smoke-candidate-scripted-regression"


def test_two_comparisons_of_the_same_runs_are_byte_identical(
    smoke_runs: Path, tmp_path: Path
) -> None:
    for tag in ("first", "second"):
        compare_runs(
            smoke_runs / "smoke-baseline-recorded",
            smoke_runs / "smoke-candidate-scripted-regression",
            tmp_path / tag,
        )
    left = tmp_path / "first" / "smoke-baseline-vs-candidate"
    right = tmp_path / "second" / "smoke-baseline-vs-candidate"
    for name in ("comparison.jsonl", "summary.json", "comparison_meta.json"):
        assert (left / name).read_bytes() == (right / name).read_bytes(), name


def test_no_flag_serializes_a_non_finite_number(smoke_runs: Path) -> None:
    """`json.dumps(inf)` emits `Infinity`, which strict JSON readers reject."""
    result = compare_runs(
        smoke_runs / "smoke-baseline-recorded",
        smoke_runs / "smoke-candidate-scripted-regression",
    )
    for record in result.records:
        text = record.to_json()
        assert "Infinity" not in text and "NaN" not in text
        json.loads(text, parse_constant=_reject_constant)


def _reject_constant(name: str):  # pragma: no cover - only runs on failure
    raise AssertionError(f"non-finite JSON constant in artifact: {name}")


def test_a_dry_run_writes_nothing(smoke_runs: Path) -> None:
    result = compare_runs(
        smoke_runs / "smoke-baseline-recorded",
        smoke_runs / "smoke-candidate-scripted-regression",
        None,
    )
    assert result.out_dir is None
    assert result.comparison_path is None
    assert result.flag_count == 6


def test_a_missing_run_directory_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ComparisonError, match="not a run directory"):
        load_baseline(tmp_path / "nowhere")


def test_a_record_whose_run_id_disagrees_with_its_meta_is_rejected(
    smoke_runs: Path, tmp_path: Path
) -> None:
    run_dir = tmp_path / "tampered"
    run_dir.mkdir()
    source = smoke_runs / "smoke-baseline-recorded"
    (run_dir / "run_meta.json").write_text((source / "run_meta.json").read_text())
    lines = (source / "run.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    first["run_id"] = "some-other-run"
    lines[0] = json.dumps(first)
    (run_dir / "run.jsonl").write_text("\n".join(lines) + "\n")

    with pytest.raises(ComparisonError, match="run_id"):
        load_baseline(run_dir)


def test_the_default_comparison_id_is_derived_and_safe(smoke_runs: Path) -> None:
    baseline = load_baseline(smoke_runs / "smoke-baseline-recorded")
    candidate = load_candidate(
        smoke_runs / "smoke-candidate-scripted-regression", baseline=baseline
    )
    expected = "smoke-baseline-vs-candidate"
    assert cmp.default_comparison_id(baseline, candidate) == expected
    with pytest.raises(ComparisonError, match="not a safe directory name"):
        compare(baseline, candidate, comparison_id="../escape")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_compare_writes_artifacts_and_exits_zero(
    smoke_runs: Path, tmp_path: Path, capsys
) -> None:
    from llm_regression_observability import cli

    code = cli.main(
        [
            "compare",
            "--baseline", str(smoke_runs / "smoke-baseline-recorded"),
            "--candidate", str(smoke_runs / "smoke-candidate-scripted-regression"),
            "--out", str(tmp_path),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "result           : PASS" in out
    assert "cases regressed  : 6" in out
    assert (tmp_path / "smoke-baseline-vs-candidate" / "summary.json").is_file()


def test_cli_compare_fail_on_critical_is_a_regression_gate(
    smoke_runs: Path, capsys
) -> None:
    from llm_regression_observability import cli

    code = cli.main(
        [
            "compare",
            "--baseline", str(smoke_runs / "smoke-baseline-recorded"),
            "--candidate", str(smoke_runs / "smoke-candidate-scripted-regression"),
            "--dry-run",
            "--fail-on", "critical",
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "4 flag(s) at or above critical" in out
    assert "none written (--dry-run)" in out


def test_cli_compare_reports_a_bad_path_without_a_traceback(
    tmp_path: Path, capsys
) -> None:
    from llm_regression_observability import cli

    code = cli.main(
        [
            "compare",
            "--baseline", str(tmp_path / "a"),
            "--candidate", str(tmp_path / "b"),
        ]
    )
    assert code == 1
    assert "result           : FAIL" in capsys.readouterr().out
