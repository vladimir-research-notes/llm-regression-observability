"""Tests for the observability layer (Milestone 8).

CPU-only and offline. Telemetry is the layer that gets believed without being
checked, so the tests are organized around the four ways it could mislead.

**It could claim a timing it never had.** The trace records exactly one duration
per case — ``RunRecord.latency_ms``. The request span reproduces it; every child
interval is a *derived* partition that asserts ordering and not duration, and
every span says which it is. The tests pin the request interval to the recorded
value, assert derived children tile it exactly, assert evaluator spans are
zero-width (scoring is post hoc), and an AST scan fails if any module in
``observability/`` imports a clock, an RNG, or a socket.

**It could describe a tree that is not one.** ``validate_spans`` is the contract:
parents exist and share the trace, no span is its own ancestor, a child's interval
is contained in its parent's, and derived siblings neither overlap nor leave a
gap. Each violation gets a test that constructs it and asserts the refusal —
including on the read path, so a corrupt ``spans.jsonl`` fails at load.

**It could leak.** A span carries identifiers, counts, statuses and booleans:
never a prompt, a model output, a tool argument *value*, a retrieved snippet, or
an error message. Redaction lands in Milestone 9; until a redactor has seen a
string, no telemetry artifact may copy it. The test scans every span attribute of
every committed fixture record against the free text of the record it came from.

**It could blur the harness into the application.** This is the milestone's whole
purpose. ``trace_completeness`` and ``infra_error_rate`` are properties of the
*observation*; a run whose harness broke must not read as a run whose model
regressed. The tests assert the three signal maps are disjoint, that no metric in
``NEVER_FLAG_METRICS`` reaches ``application_metrics``, and that the one fixture
case with an infra-origin error (``at_infra_noise``) raises the harness numbers
while leaving ``error_rate`` untouched.

Aggregation is deterministic throughout: same run, byte-identical ``spans.jsonl``
and ``aggregate.json``, and ``metrics_sha256`` equal to the digest of the
``metrics.jsonl`` that ``scripts/04_score_runs.py`` writes independently.
"""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest

from llm_regression_observability import cli, observability
from llm_regression_observability.datasets import (
    SUITE_NAMES,
    load_eval_suite,
    load_suite_fixture,
    resolve_fixture_roots,
)
from llm_regression_observability.evaluators import (
    NEVER_FLAG_METRICS,
    PLACEHOLDER_METRICS,
    evaluate_case,
    evaluate_run,
)
from llm_regression_observability.evaluators.base import (
    HIGHER_IS_BETTER,
    LOWER_IS_BETTER,
    METRIC_DIRECTION,
    NEUTRAL,
)
from llm_regression_observability.executor import (
    RUN_JSONL,
    RUN_META_JSON,
    RunConfig,
    RunMeta,
    run_suite,
)
from llm_regression_observability.observability import (
    AGGREGATE_JSON,
    APPLICATION,
    HARNESS,
    PLACEHOLDER,
    REDUCERS,
    SIGNALS,
    SPAN_NAMES,
    SPANS_JSONL,
    SUITE_METRIC_SPECS,
    SUITE_METRICS,
    TIMING_DERIVED,
    TIMING_NONE,
    TIMING_RECORDED,
    ObservabilityError,
    RunAggregate,
    Span,
    aggregate_run_dir,
    build_case_spans,
    build_run_spans,
    read_spans,
    reduce_metrics,
    signal_of,
    span_depths,
    span_id_for,
    step_bounds,
    summarize_spans,
    trace_id_for,
    validate_spans,
    write_spans,
)
from llm_regression_observability.observability.metrics import (
    mean,
    nonzero_ratio,
    p50,
    p95,
    total,
)
from llm_regression_observability.schemas import (
    ErrorRecord,
    EvalCase,
    MetricResult,
    RunRecord,
    SchemaError,
    ToolCallTrace,
)
from llm_regression_observability.traces.storage import (
    file_sha256,
    read_json,
    read_jsonl,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_SUITE = REPO_ROOT / "evals" / "suites" / "smoke.yaml"

#: Span counts on the eight-case smoke suite. Ground truth, not a smoke check:
#: six baseline tool calls become five in the candidate (`tu_wrong_tool` drops
#: one), and its two retrieval sources become three (`re_unsupported_claim` adds
#: an uncited one). The candidate emits one metric fewer — its unparseable JSON
#: yields no `field_diff_count` — hence one evaluator span fewer.
BASELINE_SPAN_COUNTS = {
    "request": 8,
    "model_call": 8,
    "tool_call": 6,
    "retrieval": 2,
    "evaluator": 82,
}
CANDIDATE_SPAN_COUNTS = {
    "request": 8,
    "model_call": 8,
    "tool_call": 5,
    "retrieval": 3,
    "evaluator": 81,
}

#: Per-case metrics that a single run can never emit: they compare two records.
#: No suite metric may reduce them — the comparison engine owns that dimension.
PAIRWISE_METRICS = frozenset({"tool_arg_diff", "risk_label_delta"})

#: Suite-level names that live in ``METRIC_DIRECTION`` for the comparison engine
#: and are not per-case metrics.
SUITE_LEVEL_NAMES = frozenset(
    {
        "latency_p50",
        "latency_p95",
        "cost_total_usd",
        "error_rate",
        "infra_error_rate",
    }
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def smoke_runs(tmp_path_factory) -> Path:
    """Both smoke runs on disk, unaggregated. Offline; no fixture is mutated."""
    runs = tmp_path_factory.mktemp("obs") / "runs"
    for adapter, version in (
        ("recorded", "baseline"),
        ("scripted-regression", "candidate"),
    ):
        run_suite(
            RunConfig(suite_path=SMOKE_SUITE, adapter=adapter, app_version=version),
            runs,
        )
    return runs


@pytest.fixture(scope="module")
def baseline_dir(smoke_runs: Path) -> Path:
    return smoke_runs / "smoke-baseline-recorded"


@pytest.fixture(scope="module")
def candidate_dir(smoke_runs: Path) -> Path:
    return smoke_runs / "smoke-candidate-scripted-regression"


def _load(run_dir: Path) -> tuple[RunMeta, list[RunRecord], list[EvalCase]]:
    meta = read_json(run_dir / RUN_META_JSON, RunMeta)
    records = read_jsonl(run_dir / RUN_JSONL, RunRecord)
    cases = load_eval_suite(REPO_ROOT / meta.suite_path).cases
    return meta, records, cases


def _one_case(run_dir: Path, case_id: str) -> tuple[EvalCase, RunRecord]:
    _, records, cases = _load(run_dir)
    case = next(c for c in cases if c.case_id == case_id)
    record = next(r for r in records if r.case_id == case_id)
    return case, record


def _span(spans: list[Span], name: str) -> Span:
    return next(span for span in spans if span.name == name)


def _fixture_records() -> list[tuple[EvalCase, RunRecord]]:
    """Every committed fixture record with the case that produced it."""
    roots = resolve_fixture_roots(None)
    pairs: list[tuple[EvalCase, RunRecord]] = []
    for suite_name in SUITE_NAMES:
        fixture = load_suite_fixture(suite_name, *roots)
        by_id = {case.case_id: case for case in fixture.cases}
        for record in [*fixture.baseline, *fixture.candidate]:
            pairs.append((by_id[record.case_id], record))
    return pairs


def _make_span(**overrides) -> Span:
    base = {
        "trace_id": "t" * 32,
        "span_id": "s" * 16,
        "parent_id": None,
        "name": "request",
        "case_id": "c1",
        "run_id": "r1",
        "start_ms": 0.0,
        "end_ms": 10.0,
        "status": "ok",
        "attributes": {"timing": TIMING_RECORDED},
    }
    return Span(**{**base, **overrides})


# --------------------------------------------------------------------------- #
# Span identity
# --------------------------------------------------------------------------- #


def test_ids_are_content_addressed_and_the_right_width() -> None:
    trace_id = trace_id_for("run-a", "case-1")
    span_id = span_id_for("run-a", "case-1", "request")
    assert len(trace_id) == 32
    assert len(span_id) == 16
    assert int(trace_id, 16) >= 0 and int(span_id, 16) >= 0
    # Derived from position, not generated: recomputing gives the same id.
    assert trace_id_for("run-a", "case-1") == trace_id
    assert span_id_for("run-a", "case-1", "request") == span_id


def test_ids_separate_runs_cases_and_positions() -> None:
    assert trace_id_for("run-a", "case-1") != trace_id_for("run-b", "case-1")
    assert trace_id_for("run-a", "case-1") != trace_id_for("run-a", "case-2")
    assert span_id_for("r", "c", "request") != span_id_for(
        "r", "c", "request/model_call"
    )
    assert span_id_for("r", "c", "request/tool_call/0") != span_id_for(
        "r", "c", "request/tool_call/1"
    )


def test_one_case_is_one_trace(baseline_dir: Path) -> None:
    _, records, cases = _load(baseline_dir)
    spans = build_run_spans(cases, records)
    by_case: dict[str, set[str]] = {}
    for span in spans:
        by_case.setdefault(span.case_id, set()).add(span.trace_id)
    assert all(len(ids) == 1 for ids in by_case.values())
    assert len({next(iter(ids)) for ids in by_case.values()}) == len(records)


# --------------------------------------------------------------------------- #
# Span tree shape
# --------------------------------------------------------------------------- #


def test_the_span_tree_has_the_documented_shape(baseline_dir: Path) -> None:
    case, record = _one_case(baseline_dir, "tu_wrong_tool")
    results = evaluate_case(case, record)
    spans = build_case_spans(case, record, results)

    root = spans[0]
    assert root.name == "request" and root.parent_id is None
    assert [span.name for span in spans[1:]] == (
        ["model_call"]
        + ["tool_call"] * len(record.tool_calls)
        + ["retrieval"] * len(record.retrieval_sources)
        + ["evaluator"] * len(results)
    )
    assert all(span.parent_id == root.span_id for span in spans[1:])
    assert max(span_depths(spans).values()) == 2
    assert {span.name for span in spans} <= set(SPAN_NAMES)


def test_an_unscored_run_projects_a_tree_with_no_evaluator_spans(
    baseline_dir: Path,
) -> None:
    """The executor has a trace and no scores; that is a valid, complete tree."""
    case, record = _one_case(baseline_dir, "lc_latency_spike")
    spans = build_case_spans(case, record)
    validate_spans(spans)
    assert not [span for span in spans if span.name == "evaluator"]


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [("baseline_dir", BASELINE_SPAN_COUNTS), ("candidate_dir", CANDIDATE_SPAN_COUNTS)],
)
def test_span_counts_on_the_smoke_suite(fixture, expected, request) -> None:
    run_dir = request.getfixturevalue(fixture)
    _, records, cases = _load(run_dir)
    spans = build_run_spans(cases, records, evaluate_run(cases, records))
    summary = summarize_spans(spans)
    assert summary.spans_by_name == dict(sorted(expected.items()))
    assert summary.span_count == sum(expected.values())
    assert summary.trace_count == len(records)
    assert summary.max_depth == 2


def test_a_record_with_no_case_is_refused(baseline_dir: Path) -> None:
    _, records, cases = _load(baseline_dir)
    orphan = dataclasses.replace(records[0], case_id="not_a_case")
    with pytest.raises(ObservabilityError, match="unknown case_id"):
        build_run_spans(cases, [orphan])


def test_a_case_record_mismatch_is_refused(baseline_dir: Path) -> None:
    case, record = _one_case(baseline_dir, "sj_invalid_json")
    other = dataclasses.replace(record, case_id="tu_wrong_tool")
    with pytest.raises(ObservabilityError, match="case/record mismatch"):
        build_case_spans(case, other)


# --------------------------------------------------------------------------- #
# Timings come from the trace, never the clock
# --------------------------------------------------------------------------- #


def test_the_request_span_reproduces_the_recorded_latency(baseline_dir: Path) -> None:
    _, records, cases = _load(baseline_dir)
    by_id = {case.case_id: case for case in cases}
    for record in records:
        root = build_case_spans(by_id[record.case_id], record)[0]
        assert root.start_ms == 0.0
        assert root.end_ms == record.latency_ms
        assert root.duration_ms == record.latency_ms
        assert root.attributes["timing"] == TIMING_RECORDED


def test_derived_children_tile_the_request_interval(baseline_dir: Path) -> None:
    """Ordering is recorded; duration is not. The partition says only "in order"."""
    case, record = _one_case(baseline_dir, "at_more_retries")
    spans = build_case_spans(case, record, evaluate_case(case, record))
    root = spans[0]
    derived = [s for s in spans[1:] if s.attributes["timing"] == TIMING_DERIVED]

    assert len(derived) == 1 + len(record.tool_calls) + len(record.retrieval_sources)
    assert derived[0].start_ms == root.start_ms
    assert derived[-1].end_ms == root.end_ms
    for left, right in zip(derived, derived[1:], strict=False):
        assert left.end_ms == right.start_ms
    widths = {span.duration_ms for span in derived}
    assert len(widths) == 1  # an equal share: a slot, not a measurement


def test_evaluator_spans_are_zero_width_after_the_request(baseline_dir: Path) -> None:
    case, record = _one_case(baseline_dir, "sj_invalid_json")
    spans = build_case_spans(case, record, evaluate_case(case, record))
    root = spans[0]
    evaluators = [span for span in spans if span.name == "evaluator"]
    assert evaluators
    for span in evaluators:
        assert span.start_ms == span.end_ms == root.end_ms
        assert span.attributes["timing"] == TIMING_NONE


def test_step_bounds_partition_the_interval_exactly() -> None:
    assert step_bounds(150.0, 3) == [0.0, 50.0, 100.0, 150.0]
    assert step_bounds(0.0, 4) == [0.0, 0.0, 0.0, 0.0, 0.0]
    assert step_bounds(10.0, 0) == [0.0]
    bounds = step_bounds(190.0, 7)
    assert bounds[0] == 0.0 and bounds[-1] == 190.0
    assert bounds == sorted(bounds)


def test_a_negative_recorded_latency_is_refused() -> None:
    with pytest.raises(ObservabilityError, match="negative latency_ms"):
        step_bounds(-1.0, 2)


def test_two_projections_of_one_record_are_identical(baseline_dir: Path) -> None:
    case, record = _one_case(baseline_dir, "re_unsupported_claim")
    results = evaluate_case(case, record)
    first = [span.to_json() for span in build_case_spans(case, record, results)]
    second = [span.to_json() for span in build_case_spans(case, record, results)]
    assert first == second


# --------------------------------------------------------------------------- #
# Structural validation
# --------------------------------------------------------------------------- #


def test_a_valid_tree_passes(baseline_dir: Path) -> None:
    _, records, cases = _load(baseline_dir)
    validate_spans(build_run_spans(cases, records, evaluate_run(cases, records)))


def test_a_child_that_escapes_its_parent_is_refused() -> None:
    root = _make_span()
    child = _make_span(
        span_id="c" * 16,
        parent_id=root.span_id,
        name="model_call",
        start_ms=0.0,
        end_ms=11.0,
        attributes={"timing": TIMING_DERIVED},
    )
    with pytest.raises(ObservabilityError, match="escapes its parent"):
        validate_spans([root, child])


def test_a_missing_parent_is_refused() -> None:
    orphan = _make_span(name="model_call", parent_id="deadbeefdeadbeef")
    with pytest.raises(ObservabilityError, match="not in the trace"):
        validate_spans([orphan])


def test_a_cycle_is_refused() -> None:
    a = _make_span(span_id="a" * 16, parent_id="b" * 16, name="model_call")
    b = _make_span(span_id="b" * 16, parent_id="a" * 16, name="model_call")
    with pytest.raises(ObservabilityError, match="own ancestor"):
        validate_spans([a, b])


def test_a_duplicate_span_id_is_refused() -> None:
    with pytest.raises(ObservabilityError, match="duplicate span_id"):
        validate_spans([_make_span(), _make_span()])


def test_a_span_that_ends_before_it_starts_is_refused() -> None:
    with pytest.raises(ObservabilityError, match="ends before it starts"):
        validate_spans([_make_span(start_ms=5.0, end_ms=1.0)])


def test_a_gap_between_derived_siblings_is_refused() -> None:
    root = _make_span()
    kids = [
        _make_span(
            span_id=f"{i}" * 16,
            parent_id=root.span_id,
            name="tool_call",
            start_ms=start,
            end_ms=end,
            attributes={"timing": TIMING_DERIVED},
        )
        for i, (start, end) in enumerate(((0.0, 4.0), (6.0, 10.0)))
    ]
    with pytest.raises(ObservabilityError, match="overlap or leave a gap"):
        validate_spans([root, *kids])


def test_derived_children_must_span_the_parent() -> None:
    root = _make_span()
    child = _make_span(
        span_id="c" * 16,
        parent_id=root.span_id,
        name="model_call",
        start_ms=0.0,
        end_ms=4.0,
        attributes={"timing": TIMING_DERIVED},
    )
    with pytest.raises(ObservabilityError, match="do not end at the parent"):
        validate_spans([root, child])


def test_the_span_vocabulary_is_closed() -> None:
    with pytest.raises(SchemaError, match="not in"):
        _make_span(name="database_query")
    with pytest.raises(SchemaError, match="not in"):
        _make_span(status="degraded")


# --------------------------------------------------------------------------- #
# Span status is about the operation, not the score
# --------------------------------------------------------------------------- #


def test_a_failed_tool_call_errors_its_span_and_its_request(
    candidate_dir: Path,
) -> None:
    case, record = _one_case(candidate_dir, "at_infra_noise")
    spans = build_case_spans(case, record)
    assert _span(spans, "request").status == "error"
    assert [span.status for span in spans if span.name == "tool_call"] == [
        "error" if call.status == "failed" else "ok" for call in record.tool_calls
    ]
    assert any(call.status == "failed" for call in record.tool_calls)


def test_errors_attach_to_the_request_span_with_their_origin(
    candidate_dir: Path,
) -> None:
    """``ErrorRecord`` names no step, so inventing a parent for it would be a guess."""
    _, record = _one_case(candidate_dir, "at_infra_noise")
    case, _ = _one_case(candidate_dir, "at_infra_noise")
    root = build_case_spans(case, record)[0]
    assert root.attributes["attribution"] == "infra"
    assert root.attributes["infra_error_count"] == 1
    assert root.attributes["app_error_count"] == 0
    assert root.attributes["error_kinds"] == sorted(
        {error.kind for error in record.errors}
    )


def test_a_failing_score_is_a_result_not_a_span_error(candidate_dir: Path) -> None:
    case, record = _one_case(candidate_dir, "sj_invalid_json")
    results = evaluate_case(case, record)
    spans = build_case_spans(case, record, results)
    failing = [
        span
        for span in spans
        if span.name == "evaluator" and span.attributes["passed"] is False
    ]
    assert failing, "the invalid-JSON candidate must fail at least one scorer"
    assert all(span.status == "ok" for span in failing)


def test_evaluator_spans_carry_the_metric_signal(baseline_dir: Path) -> None:
    case, record = _one_case(baseline_dir, "at_infra_noise")
    spans = build_case_spans(case, record, evaluate_case(case, record))
    signals = {
        span.attributes["metric"]: span.attributes["signal"]
        for span in spans
        if span.name == "evaluator"
    }
    assert signals["trace_completeness"] == HARNESS
    assert signals["infra_error_count"] == HARNESS
    assert signals["app_error_count"] == APPLICATION
    assert signals["task_success"] == APPLICATION


# --------------------------------------------------------------------------- #
# Spans carry symbols, never free text
# --------------------------------------------------------------------------- #


def _attribute_strings(span: Span) -> list[str]:
    out: list[str] = []
    for value in span.attributes.values():
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, list):
            out.extend(item for item in value if isinstance(item, str))
    return out


def test_no_span_copies_free_text_out_of_the_trace() -> None:
    """Redaction lands in Milestone 9. Until a redactor has seen a string, no
    telemetry artifact may copy it — outputs, tool arguments, snippets, messages."""
    for case, record in _fixture_records():
        secrets = {case.prompt, record.final_output}
        secrets |= {call.error or "" for call in record.tool_calls}
        secrets |= {error.message for error in record.errors}
        secrets |= {source.snippet for source in record.retrieval_sources}
        secrets |= {source.claim or "" for source in record.retrieval_sources}
        for call in record.tool_calls:
            secrets |= {str(value) for value in call.arguments.values()}
        secrets = {text for text in secrets if len(text) > 3}

        for span in build_case_spans(case, record, evaluate_case(case, record)):
            for text in _attribute_strings(span):
                assert text not in secrets, f"{span.name} leaked {text!r}"


def test_tool_spans_carry_argument_keys_but_never_values(baseline_dir: Path) -> None:
    case, record = _one_case(baseline_dir, "tu_wrong_tool")
    spans = build_case_spans(case, record)
    tools = [span for span in spans if span.name == "tool_call"]
    assert tools
    for span, call in zip(tools, record.tool_calls, strict=True):
        assert span.attributes["tool_name"] == call.name
        assert span.attributes["argument_keys"] == sorted(call.arguments)
        assert "arguments" not in span.attributes
        assert span.attributes["allowed"] is (call.name in (case.allowed_tools or []))


def test_a_case_with_no_allow_list_reports_unknown_rather_than_permitted(
    baseline_dir: Path,
) -> None:
    case, record = _one_case(baseline_dir, "at_more_retries")
    stripped = dataclasses.replace(case, allowed_tools=None)
    spans = build_case_spans(stripped, record)
    for span in (s for s in spans if s.name == "tool_call"):
        assert span.attributes["allowed"] is None


# --------------------------------------------------------------------------- #
# Reducers
# --------------------------------------------------------------------------- #


def test_reducers_compute_what_they_are_named() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert mean(values) == 2.5
    assert total(values) == 10.0
    assert p50(values) == 2.5
    assert p95(values) == pytest.approx(3.85)
    assert nonzero_ratio([0.0, 0.0, 2.0, 5.0]) == 0.5


def test_reducers_are_empty_safe() -> None:
    assert all(reducer([]) == 0.0 for reducer in REDUCERS.values())


def test_ratio_counts_cases_and_mean_counts_events() -> None:
    """The distinction ``app_error_count -> error_rate`` depends on: two errors on
    one case out of eight is an error *rate* of 1/8, not 2/8."""
    column = [2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert nonzero_ratio(column) == 0.125
    assert mean(column) == 0.25


def test_percentile_reducers_reuse_the_pinned_type_7_definition() -> None:
    from llm_regression_observability.evaluators import percentile

    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert p50(values) == percentile(values, 0.50)
    assert p95(values) == percentile(values, 0.95)


# --------------------------------------------------------------------------- #
# The suite metric table
# --------------------------------------------------------------------------- #


def test_every_suite_metric_name_is_unique() -> None:
    names = [spec.suite_metric for spec in SUITE_METRIC_SPECS]
    assert len(names) == len(set(names)) == len(SUITE_METRICS)


def test_every_spec_names_a_known_reducer_metric_and_signal() -> None:
    for spec in SUITE_METRIC_SPECS:
        assert spec.reducer in REDUCERS
        assert spec.metric in METRIC_DIRECTION
        assert spec.signal in SIGNALS


def test_direction_is_inherited_not_restated() -> None:
    """Every reducer here is monotone, so a suite metric is worse the same way."""
    for spec in SUITE_METRIC_SPECS:
        assert spec.direction == METRIC_DIRECTION[spec.metric]
    assert SUITE_METRICS["success_rate"].direction == HIGHER_IS_BETTER
    assert SUITE_METRICS["latency_p95"].direction == LOWER_IS_BETTER
    assert SUITE_METRICS["infra_error_rate"].direction == NEUTRAL


def test_no_suite_metric_reduces_a_pairwise_metric() -> None:
    assert not PAIRWISE_METRICS & {spec.metric for spec in SUITE_METRIC_SPECS}


def test_every_per_case_metric_the_fixtures_emit_has_a_suite_metric() -> None:
    """A metric with no spec would be scored per case and vanish from the suite."""
    emitted = {
        result.metric
        for case, record in _fixture_records()
        for result in evaluate_case(case, record)
    }
    reduced = {spec.metric for spec in SUITE_METRIC_SPECS}
    assert emitted <= reduced, f"unreduced metrics: {sorted(emitted - reduced)}"


def test_the_metric_direction_table_covers_every_name_it_should() -> None:
    """Nothing in ``METRIC_DIRECTION`` is orphaned: each name is a per-case metric
    the specs reduce, a pairwise metric, or a suite-level name."""
    reduced = {spec.metric for spec in SUITE_METRIC_SPECS}
    orphans = set(METRIC_DIRECTION) - reduced - PAIRWISE_METRICS - SUITE_LEVEL_NAMES
    assert not orphans, f"orphaned metric names: {sorted(orphans)}"


def test_signal_of_classifies_every_per_case_metric() -> None:
    assert signal_of("task_success") == APPLICATION
    assert signal_of("trace_completeness") == HARNESS
    assert signal_of("infra_error_count") == HARNESS
    for name in PLACEHOLDER_METRICS:
        assert signal_of(name) == PLACEHOLDER


# --------------------------------------------------------------------------- #
# Aggregation: the harness never enters the application's scores
# --------------------------------------------------------------------------- #


def test_the_three_signal_maps_are_disjoint(baseline_dir: Path) -> None:
    result = aggregate_run_dir(baseline_dir)
    agg = result.aggregate
    keys = [
        set(agg.application_metrics),
        set(agg.harness_metrics),
        set(agg.placeholder_metrics),
    ]
    for i, left in enumerate(keys):
        for right in keys[i + 1 :]:
            assert not left & right


def test_no_never_flag_metric_reaches_the_application_scores(
    candidate_dir: Path,
) -> None:
    agg = aggregate_run_dir(candidate_dir).aggregate
    forbidden = {
        spec.suite_metric
        for spec in SUITE_METRIC_SPECS
        if spec.metric in NEVER_FLAG_METRICS
    }
    assert forbidden
    assert not forbidden & set(agg.application_metrics)


def test_trace_completeness_and_infra_errors_are_harness_signals(
    candidate_dir: Path,
) -> None:
    agg = aggregate_run_dir(candidate_dir).aggregate
    assert set(agg.harness_metrics) == {
        "trace_completeness",
        "infra_error_rate",
        "infra_error_total",
        "incomplete_trace_rate",
    }
    assert "trace_completeness" not in agg.application_metrics
    assert "infra_error_rate" not in agg.application_metrics


def test_the_infra_case_moves_the_harness_numbers_and_not_the_error_rate(
    candidate_dir: Path,
) -> None:
    """``at_infra_noise`` has a failed tool call and a red trace. One of the eight
    candidate cases errored for infra reasons; exactly one errored for app reasons.
    They are counted in different maps."""
    agg = aggregate_run_dir(candidate_dir).aggregate
    assert agg.infra_attributed_case_ids == ["at_infra_noise"]
    assert agg.harness_metrics["infra_error_rate"] == 0.125
    assert agg.harness_metrics["infra_error_total"] == 1.0
    assert agg.application_metrics["error_rate"] == 0.125  # at_more_retries, not it
    assert agg.application_metrics["app_error_total"] == 1.0


def test_tool_error_rate_mean_mixes_origins_and_is_pinned_here(
    candidate_dir: Path,
) -> None:
    """A known blur, asserted rather than described.

    ``tool_error_rate`` is descriptive: an infra timeout fails a tool call too. So
    the suite mean sits in ``application_metrics`` while one of the two cases that
    raise it (``at_infra_noise``) is infra-attributed. It cannot cause a false
    regression — the flag rule reads ``app_error_count``, never this — but the
    suite mean does mix origins, and ``docs/LIMITATIONS.md`` says so. If this
    number ever becomes origin-aware, this test breaks first.
    """
    _, records, cases = _load(candidate_dir)
    scored = evaluate_run(cases, records)
    raising = {
        case_id: result.detail["attribution"]
        for case_id, results in scored.items()
        for result in results
        if result.metric == "tool_error_rate" and result.value > 0.0
    }
    assert raising == {"at_more_retries": "app", "at_infra_noise": "infra"}

    agg = aggregate_run_dir(candidate_dir).aggregate
    assert agg.application_metrics["tool_error_rate_mean"] == 0.125
    assert agg.application_metrics["app_error_total"] == 1.0  # only the app one


def test_a_clean_baseline_has_no_harness_findings(baseline_dir: Path) -> None:
    agg = aggregate_run_dir(baseline_dir).aggregate
    assert agg.infra_attributed_case_ids == []
    assert agg.incomplete_trace_case_ids == []
    assert agg.harness_metrics["trace_completeness"] == 1.0
    assert agg.harness_metrics["infra_error_rate"] == 0.0
    assert agg.harness_metrics["incomplete_trace_rate"] == 0.0
    assert agg.application_metrics["success_rate"] == 1.0


def test_a_degraded_trace_surfaces_as_a_harness_finding_not_a_regression(
    baseline_dir: Path,
) -> None:
    """Strip the recorded latency and the trace becomes incomplete. The suite says
    so in ``harness_metrics``; ``success_rate`` is untouched."""
    meta, records, cases = _load(baseline_dir)
    broken = [
        dataclasses.replace(record, latency_ms=0.0) if i == 0 else record
        for i, record in enumerate(records)
    ]
    scored = evaluate_run(cases, broken)
    spans = build_run_spans(cases, broken, scored)
    agg = observability.aggregate_run(meta, broken, scored, spans)

    assert agg.incomplete_trace_case_ids == [broken[0].case_id]
    assert agg.harness_metrics["incomplete_trace_rate"] == 0.125
    assert agg.harness_metrics["trace_completeness"] < 1.0
    assert agg.application_metrics["success_rate"] == 1.0


def test_an_absent_metric_yields_no_suite_metric() -> None:
    """Absent is *not applicable*, never zero."""
    scored = {
        "c1": [MetricResult(case_id="c1", metric="task_success", value=1.0)],
    }
    reduced = reduce_metrics(scored)
    assert reduced.application == {"success_rate": 1.0}
    assert "json_validity_rate" not in reduced.application
    assert reduced.coverage == {"success_rate": 1}


def test_metric_coverage_says_how_many_cases_answered(baseline_dir: Path) -> None:
    """``evidence_coverage_mean = 1.0`` over one of eight cases is a fact about one
    case; a summary that does not say so invites a misreading."""
    agg = aggregate_run_dir(baseline_dir).aggregate
    assert agg.metric_coverage["success_rate"] == 8
    assert agg.metric_coverage["evidence_coverage_mean"] == 1
    assert agg.metric_coverage["json_validity_rate"] == 2
    assert set(agg.metric_coverage) == (
        set(agg.application_metrics)
        | set(agg.placeholder_metrics)
        | (set(agg.harness_metrics) - {"incomplete_trace_rate"})
    )


def test_the_placeholders_are_reported_and_claimed_for_nothing(
    baseline_dir: Path,
) -> None:
    agg = aggregate_run_dir(baseline_dir).aggregate
    assert agg.placeholder_metrics == {"explanation_quality_mean": 0.0}


def test_suite_metrics_match_the_run_metadata_totals(baseline_dir: Path) -> None:
    """Two independent paths to the same numbers: the executor's own totals, and a
    reduction over the scored metrics."""
    meta, _, _ = _load(baseline_dir)
    agg = aggregate_run_dir(baseline_dir).aggregate
    assert agg.application_metrics["latency_total_ms"] == meta.latency_ms_total
    assert agg.application_metrics["cost_total_usd"] == meta.cost_total_usd
    assert agg.spans.recorded_duration_ms == meta.latency_ms_total
    assert agg.case_count == meta.case_count


# --------------------------------------------------------------------------- #
# Artifacts
# --------------------------------------------------------------------------- #


def test_aggregating_writes_spans_and_the_summary(
    tmp_path: Path, baseline_dir: Path
) -> None:
    run_copy = tmp_path / "run"
    run_copy.mkdir()
    for name in (RUN_JSONL, RUN_META_JSON):
        run_copy.joinpath(name).write_bytes((baseline_dir / name).read_bytes())

    result = aggregate_run_dir(run_copy, tmp_path / "processed")
    assert result.spans_path == run_copy / SPANS_JSONL
    assert result.aggregate_path == (
        tmp_path / "processed" / result.aggregate.run_id / AGGREGATE_JSON
    )
    assert file_sha256(result.spans_path) == result.aggregate.spans_sha256

    reloaded = read_json(result.aggregate_path, RunAggregate)
    assert reloaded.to_json() == result.aggregate.to_json()
    assert read_spans(result.spans_path) == result.spans


def test_the_metrics_digest_matches_the_metrics_file_score_runs_writes(
    tmp_path: Path, baseline_dir: Path
) -> None:
    """``aggregate.json`` and ``metrics.jsonl`` have different writers; the digest
    is what proves they reduced the same stream."""
    from llm_regression_observability.traces.storage import write_jsonl

    result = aggregate_run_dir(baseline_dir)
    path = write_jsonl(tmp_path / "metrics.jsonl", result.metrics)
    assert file_sha256(path) == result.aggregate.metrics_sha256


def test_dry_run_writes_nothing(tmp_path: Path, baseline_dir: Path) -> None:
    run_copy = tmp_path / "run"
    run_copy.mkdir()
    for name in (RUN_JSONL, RUN_META_JSON):
        run_copy.joinpath(name).write_bytes((baseline_dir / name).read_bytes())

    result = aggregate_run_dir(run_copy, None)
    assert result.spans_path is None and result.aggregate_path is None
    assert not (run_copy / SPANS_JSONL).exists()
    assert result.aggregate.spans_sha256  # computed all the same


def test_two_aggregations_of_the_same_run_are_byte_identical(
    tmp_path: Path, baseline_dir: Path
) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    run_a, run_b = first / "run", second / "run"
    for run_dir in (run_a, run_b):
        run_dir.mkdir(parents=True)
        for name in (RUN_JSONL, RUN_META_JSON):
            run_dir.joinpath(name).write_bytes((baseline_dir / name).read_bytes())

    left = aggregate_run_dir(run_a, first / "processed")
    right = aggregate_run_dir(run_b, second / "processed")
    assert left.spans_path.read_bytes() == right.spans_path.read_bytes()
    assert left.aggregate_path.read_bytes() == right.aggregate_path.read_bytes()


def test_an_invalid_tree_is_never_written(tmp_path: Path) -> None:
    root = _make_span()
    escaping = _make_span(
        span_id="c" * 16,
        parent_id=root.span_id,
        name="model_call",
        end_ms=99.0,
        attributes={"timing": TIMING_DERIVED},
    )
    path = tmp_path / SPANS_JSONL
    with pytest.raises(ObservabilityError):
        write_spans(path, [root, escaping])
    assert not path.exists()


def test_a_corrupt_span_file_fails_at_load(tmp_path: Path, baseline_dir: Path) -> None:
    spans = aggregate_run_dir(baseline_dir).spans
    path = write_spans(tmp_path / SPANS_JSONL, spans)
    kept = [line for line in path.read_text().splitlines() if '"request"' not in line]
    path.write_text("\n".join(kept) + "\n")
    with pytest.raises(ObservabilityError, match="not in the trace"):
        read_spans(path)


def test_a_span_round_trips_through_json() -> None:
    span = _make_span(attributes={"timing": TIMING_RECORDED, "error_kinds": ["x"]})
    assert Span.from_json(span.to_json()) == span


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_aggregate_is_a_documented_subcommand() -> None:
    args = cli.build_parser().parse_args(["aggregate", "--run", "x"])
    assert args.out == "results/processed"
    assert args.dry_run is False
    assert args.func is not None


def test_the_cli_separates_the_two_metric_families(
    tmp_path: Path, baseline_dir: Path, capsys
) -> None:
    code = cli.main(
        ["aggregate", "--run", str(baseline_dir), "--out", str(tmp_path), "--dry-run"]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "application metrics" in out
    assert "harness metrics (observation quality — never application scores)" in out
    assert "result           : PASS" in out
    assert "none written (--dry-run)" in out
    assert not list(tmp_path.iterdir())


def test_the_cli_fails_cleanly_on_a_missing_run(tmp_path: Path, capsys) -> None:
    code = cli.main(["aggregate", "--run", str(tmp_path / "nope")])
    assert code == 1
    assert "result           : FAIL" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Hygiene: nothing in observability/ can reach a clock, an RNG, or a socket
# --------------------------------------------------------------------------- #

_BANNED_IMPORTS = frozenset(
    {"random", "secrets", "time", "datetime", "socket", "urllib", "http", "requests"}
)

_MILESTONE_8_SOURCES = tuple(
    f"src/llm_regression_observability/observability/{name}.py"
    for name in ("__init__", "spans", "metrics", "aggregation")
)


def _imported_top_level_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return modules


@pytest.mark.parametrize("rel", _MILESTONE_8_SOURCES)
def test_no_ambient_time_randomness_or_network_in_the_span_path(rel: str) -> None:
    reached = _imported_top_level_modules(
        (REPO_ROOT / rel).read_text(encoding="utf-8")
    ) & _BANNED_IMPORTS
    assert not reached, f"{rel} must not import {sorted(reached)}"


def test_the_aggregate_carries_no_timestamp(baseline_dir: Path) -> None:
    """A timestamp would make two aggregations of one run differ byte for byte."""
    payload = json.loads(aggregate_run_dir(baseline_dir).aggregate.to_json())
    assert not [key for key in payload if "time" in key or "date" in key]


def test_the_observability_package_is_dependency_free() -> None:
    stdlib = {
        "__future__",
        "ast",
        "collections",
        "dataclasses",
        "hashlib",
        "pathlib",
        "statistics",
        "typing",
    }
    for rel in _MILESTONE_8_SOURCES:
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        absolute = _imported_top_level_modules(source)
        assert absolute <= stdlib, f"{rel} imports {sorted(absolute - stdlib)}"


# --------------------------------------------------------------------------- #
# One error record, one origin — the property the whole layer rests on
# --------------------------------------------------------------------------- #


def test_a_record_with_one_app_error_among_infra_errors_is_the_apps_fault() -> None:
    record = RunRecord(
        run_id="r",
        case_id="c",
        app_version="candidate",
        model_id="m",
        prompt_version="p",
        tool_version="t",
        timestamp="1970-01-01T00:00:00Z",
        latency_ms=10.0,
        final_output="x",
        errors=[
            ErrorRecord(kind="gateway_timeout", message="m", origin="infra"),
            ErrorRecord(kind="tool_error", message="m", origin="app"),
        ],
        tool_calls=[ToolCallTrace(name="lookup", status="failed", error="boom")],
    )
    case = EvalCase(case_id="c", version="1", prompt="p", risk_category="agent")
    root = build_case_spans(case, record)[0]
    assert root.attributes["attribution"] == "app"
    assert root.status == "error"
    assert root.attributes["error_kinds"] == ["gateway_timeout", "tool_error"]
