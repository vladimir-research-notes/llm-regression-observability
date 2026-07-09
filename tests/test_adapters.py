"""Tests for the deterministic adapters (Milestone 3).

CPU-only and offline. They assert the guarantees Milestones 4 and 6 rely on:

* every adapter is deterministic — same inputs produce byte-identical records;
* the recorded adapter replays the committed fixtures *exactly*;
* each scripted injector produces exactly its defect and nothing else, and the
  fixture-derived plan injects a real defect for every non-control case while
  leaving true controls untouched;
* the API stub is inert by default and refuses to run without explicit opt-in.
"""

from __future__ import annotations

import json

import pytest

from llm_regression_observability.adapters import (
    ADAPTER_NAMES,
    ADD_COST,
    ADD_ERROR,
    ADD_LATENCY,
    ADD_TOOL,
    ARG_DRIFT,
    DROP_JSON_FIELD,
    DROP_SOURCE,
    DROP_TOOL,
    FLIP_REFUSAL,
    INJECTION_KINDS,
    INVALID_JSON,
    SET_JSON_FIELD,
    SET_PARSED_FIELD,
    SWAP_TOOL_ORDER,
    UNSUPPORTED_CLAIM,
    WRONG_TOOL,
    AdapterContext,
    AdapterError,
    AdapterResponse,
    ApiAdapterDisabled,
    ApiAdapterStub,
    Injection,
    MockModelAdapter,
    ModelAdapter,
    RecordedModelAdapter,
    ScriptedRegressionAdapter,
    build_run_record,
    estimate_tokens,
    response_from_record,
)
from llm_regression_observability.datasets import (
    NO_REGRESSION,
    SUITE_NAMES,
    load_suite_fixture,
    resolve_fixture_roots,
)
from llm_regression_observability.datasets.validation import _behavioral_fields
from llm_regression_observability.schemas import (
    EvalCase,
    RetrievalTrace,
    RunRecord,
    ToolCallTrace,
)

EVALS_ROOT, DATA_ROOT = resolve_fixture_roots(None)


def _case(case_id: str, *, risk: str = "benign") -> EvalCase:
    return EvalCase(
        case_id=case_id, version="1", prompt=f"prompt for {case_id}", risk_category=risk
    )


def _ctx(app_version: str = "candidate") -> AdapterContext:
    return AdapterContext(
        app_version=app_version,
        model_id="test-model",
        prompt_version="p-x",
        tool_version="t-x",
        timestamp="2026-06-08T12:00:00Z",
    )


# --------------------------------------------------------------------------- #
# Base helpers.
# --------------------------------------------------------------------------- #


def test_build_run_record_takes_identity_from_context() -> None:
    case = _case("c1")
    resp = AdapterResponse(final_output="hello", input_tokens_estimate=3)
    rec = build_run_record(case, _ctx("candidate"), resp)
    assert isinstance(rec, RunRecord)
    assert rec.run_id == "c1-candidate"
    assert rec.case_id == "c1"
    assert rec.app_version == "candidate"
    assert rec.model_id == "test-model"
    assert rec.timestamp == "2026-06-08T12:00:00Z"
    assert rec.final_output == "hello"


def test_context_run_id_prefix_overrides_default() -> None:
    ctx = AdapterContext(app_version="baseline", run_id_prefix="run7/")
    assert ctx.run_id_for("c1") == "run7/c1"


def test_response_from_record_round_trips_behavior() -> None:
    rec = RunRecord(
        run_id="r", case_id="c", app_version="baseline", model_id="m",
        prompt_version="p", tool_version="t", timestamp="2026-01-01T00:00:00Z",
        final_output="out", parsed_output={"k": 1},
        tool_calls=[ToolCallTrace(name="search", order_index=0)],
        latency_ms=12.0,
    )
    resp = response_from_record(rec)
    rebuilt = build_run_record(_case("c"), _ctx("baseline"), resp)
    assert rebuilt.final_output == "out"
    assert rebuilt.parsed_output == {"k": 1}
    assert [t.name for t in rebuilt.tool_calls] == ["search"]
    assert rebuilt.latency_ms == 12.0
    # Mutating the extracted response must not touch the source record.
    resp.tool_calls.append(ToolCallTrace(name="extra", order_index=1))
    assert len(rec.tool_calls) == 1


def test_estimate_tokens_is_deterministic_and_zero_for_empty() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == estimate_tokens("abcd")
    assert estimate_tokens("a" * 40) == 10


def test_all_adapters_subclass_modeladapter_and_name() -> None:
    for cls in (
        MockModelAdapter,
        RecordedModelAdapter,
        ScriptedRegressionAdapter,
        ApiAdapterStub,
    ):
        assert issubclass(cls, ModelAdapter)
        assert isinstance(cls.name, str) and cls.name
    assert set(ADAPTER_NAMES) == {"mock", "recorded", "scripted-regression", "api-stub"}


# --------------------------------------------------------------------------- #
# MockModelAdapter.
# --------------------------------------------------------------------------- #


def test_mock_default_output_is_deterministic() -> None:
    adapter = MockModelAdapter()
    case = _case("c1")
    a = adapter.generate(case, _ctx())
    b = adapter.generate(case, _ctx())
    assert a.to_json() == b.to_json()
    assert a.final_output == "mock output for c1"
    assert a.input_tokens_estimate > 0
    assert a.output_tokens_estimate > 0


def test_mock_returns_preset_response_copy() -> None:
    preset = AdapterResponse(final_output="fixed", parsed_output={"x": 1})
    adapter = MockModelAdapter(responses={"c1": preset})
    rec = adapter.generate(_case("c1"), _ctx())
    assert rec.final_output == "fixed"
    assert rec.parsed_output == {"x": 1}
    # The preset is copied, not shared: mutating the record leaves the preset intact.
    rec.parsed_output["x"] = 999
    assert preset.parsed_output == {"x": 1}


# --------------------------------------------------------------------------- #
# RecordedModelAdapter.
# --------------------------------------------------------------------------- #


def test_recorded_adapter_reproduces_committed_fixtures_exactly() -> None:
    adapter = RecordedModelAdapter.from_fixtures()
    for suite in SUITE_NAMES:
        fixture = load_suite_fixture(suite, EVALS_ROOT, DATA_ROOT)
        cases = {c.case_id: c for c in fixture.cases}
        for stream, records in (
            ("baseline", fixture.baseline),
            ("candidate", fixture.candidate),
        ):
            for record in records:
                out = adapter.generate(cases[record.case_id], _ctx(stream))
                assert out.to_json() == record.to_json(), f"{suite}/{record.case_id}"


def test_recorded_adapter_is_deterministic() -> None:
    adapter = RecordedModelAdapter.from_fixtures()
    case = _case("sj_invalid_json", risk="structured")
    a = adapter.generate(case, _ctx("baseline"))
    b = adapter.generate(case, _ctx("baseline"))
    assert a.to_json() == b.to_json()


def test_recorded_adapter_returns_independent_copies() -> None:
    adapter = RecordedModelAdapter.from_fixtures()
    case = _case("tu_wrong_tool", risk="tool_use")
    first = adapter.generate(case, _ctx("baseline"))
    first.tool_calls.clear()
    second = adapter.generate(case, _ctx("baseline"))
    assert second.tool_calls, "mutating one result must not affect the next"


def test_recorded_adapter_raises_on_unknown_case() -> None:
    adapter = RecordedModelAdapter.from_fixtures()
    with pytest.raises(AdapterError, match="no recorded run"):
        adapter.generate(_case("does_not_exist"), _ctx("baseline"))


def test_recorded_adapter_rejects_duplicate_records() -> None:
    rec = RunRecord(
        run_id="r", case_id="c", app_version="baseline", model_id="m",
        prompt_version="p", tool_version="t", timestamp="2026-01-01T00:00:00Z",
    )
    with pytest.raises(AdapterError, match="duplicate"):
        RecordedModelAdapter([rec, rec])


# --------------------------------------------------------------------------- #
# ScriptedRegressionAdapter — injectors in isolation.
# --------------------------------------------------------------------------- #


def _json_response(obj: dict) -> AdapterResponse:
    return AdapterResponse(
        final_output=json.dumps(obj, separators=(",", ":")), parsed_output=dict(obj)
    )


def _tool_response(names) -> AdapterResponse:
    return AdapterResponse(
        tool_calls=[
            ToolCallTrace(name=n, arguments={"i": i}, order_index=i)
            for i, n in enumerate(names)
        ]
    )


def test_inject_invalid_json() -> None:
    base = _json_response({"a": 1, "b": 2})
    out = Injection(INVALID_JSON).apply(base)
    assert out.parsed_output is None
    with pytest.raises(json.JSONDecodeError):
        json.loads(out.final_output)
    # The base is untouched.
    assert base.parsed_output == {"a": 1, "b": 2}


def test_inject_drop_json_field() -> None:
    base = _json_response({"title": "t", "score": 0.5})
    out = Injection(DROP_JSON_FIELD, {"field": "score"}).apply(base)
    assert out.parsed_output == {"title": "t"}
    assert json.loads(out.final_output) == {"title": "t"}


def test_inject_set_json_field_wrong_type_and_extra() -> None:
    base = _json_response({"score": 0.9})
    wrong = Injection(SET_JSON_FIELD, {"field": "score", "value": "high"}).apply(base)
    assert wrong.parsed_output == {"score": "high"}
    extra = Injection(SET_JSON_FIELD, {"field": "confidence", "value": 0.8}).apply(base)
    assert extra.parsed_output == {"score": 0.9, "confidence": 0.8}


def test_inject_drop_tool_by_name_reindexes() -> None:
    base = _tool_response(["search", "lookup", "summarize"])
    out = Injection(DROP_TOOL, {"name": "lookup"}).apply(base)
    assert [t.name for t in out.tool_calls] == ["search", "summarize"]
    assert [t.order_index for t in out.tool_calls] == [0, 1]


def test_inject_drop_tool_default_drops_last() -> None:
    base = _tool_response(["search", "summarize"])
    out = Injection(DROP_TOOL).apply(base)
    assert [t.name for t in out.tool_calls] == ["search"]


def test_inject_wrong_tool_replaces_last() -> None:
    base = _tool_response(["search", "summarize"])
    out = Injection(
        WRONG_TOOL, {"name": "shell_exec", "arguments": {"cmd": "echo x"}}
    ).apply(base)
    assert [t.name for t in out.tool_calls] == ["search", "shell_exec"]
    assert out.tool_calls[1].arguments == {"cmd": "echo x"}


def test_inject_add_tool_appends() -> None:
    base = _tool_response(["search"])
    out = Injection(ADD_TOOL, {"name": "shell_exec"}).apply(base)
    assert [t.name for t in out.tool_calls] == ["search", "shell_exec"]
    assert out.tool_calls[1].order_index == 1


def test_inject_arg_drift() -> None:
    base = _tool_response(["search", "summarize"])
    out = Injection(ARG_DRIFT, {"index": 0, "arguments": {"query": "q2"}}).apply(base)
    assert out.tool_calls[0].arguments == {"query": "q2"}
    assert out.tool_calls[1].arguments == {"i": 1}


def test_inject_swap_tool_order() -> None:
    base = _tool_response(["search", "lookup"])
    out = Injection(SWAP_TOOL_ORDER, {"i": 0, "j": 1}).apply(base)
    assert [t.name for t in out.tool_calls] == ["lookup", "search"]
    assert [t.order_index for t in out.tool_calls] == [0, 1]


def test_inject_unsupported_claim() -> None:
    base = AdapterResponse(
        final_output="It is blue.",
        retrieval_sources=[
            RetrievalTrace(source_id="doc-a", snippet="blue", supported=True)
        ],
    )
    out = Injection(UNSUPPORTED_CLAIM, {"claim": "is waterproof"}).apply(base)
    assert len(out.retrieval_sources) == 2
    assert out.retrieval_sources[-1].supported is False
    assert "is waterproof" in out.final_output


def test_inject_drop_source() -> None:
    base = AdapterResponse(
        retrieval_sources=[
            RetrievalTrace(source_id="doc-a", snippet="x", supported=True),
            RetrievalTrace(source_id="doc-b", snippet="y", supported=True),
        ]
    )
    out = Injection(DROP_SOURCE, {"source_id": "doc-b"}).apply(base)
    assert [s.source_id for s in out.retrieval_sources] == ["doc-a"]


def test_inject_flip_refusal_toggles() -> None:
    base = AdapterResponse(final_output="answer", parsed_output={"refused": False})
    out = Injection(FLIP_REFUSAL).apply(base)
    assert out.parsed_output == {"refused": True}
    assert out.final_output != "answer"


def test_inject_set_parsed_field_risk_label() -> None:
    base = AdapterResponse(parsed_output={"risk_label": "benign"})
    out = Injection(
        SET_PARSED_FIELD, {"field": "risk_label", "value": "sensitive"}
    ).apply(base)
    assert out.parsed_output == {"risk_label": "sensitive"}


def test_inject_add_latency_delta_and_default() -> None:
    base = AdapterResponse(latency_ms=150.0)
    assert Injection(ADD_LATENCY, {"delta_ms": 250.0}).apply(base).latency_ms == 400.0
    assert Injection(ADD_LATENCY).apply(base).latency_ms == 400.0
    assert base.latency_ms == 150.0


def test_inject_add_cost_delta() -> None:
    base = AdapterResponse(cost_estimate_usd=0.002)
    out = Injection(ADD_COST, {"delta_usd": 0.004}).apply(base)
    assert out.cost_estimate_usd == pytest.approx(0.006)


def test_inject_add_error_marks_tool_failed() -> None:
    base = _tool_response(["search", "compute"])
    out = Injection(
        ADD_ERROR,
        {
            "kind": "tool_error",
            "message": "boom",
            "origin": "app",
            "fail_tool_index": 1,
        },
    ).apply(base)
    assert len(out.errors) == 1
    assert out.errors[0].origin == "app"
    assert out.tool_calls[1].status == "failed"


def test_unknown_injection_kind_raises() -> None:
    with pytest.raises(AdapterError, match="unknown injection kind"):
        Injection("not_a_real_kind").apply(AdapterResponse())


def test_injection_kinds_frozenset_is_populated() -> None:
    assert INVALID_JSON in INJECTION_KINDS
    assert len(INJECTION_KINDS) >= 15


# --------------------------------------------------------------------------- #
# ScriptedRegressionAdapter — driven by fixture ground truth.
# --------------------------------------------------------------------------- #


def test_scripted_adapter_is_deterministic() -> None:
    adapter = ScriptedRegressionAdapter.from_fixtures()
    case = _case("sj_invalid_json", risk="structured")
    a = adapter.generate(case, _ctx("candidate"))
    b = adapter.generate(case, _ctx("candidate"))
    assert a.to_json() == b.to_json()
    assert a.app_version == "candidate"


@pytest.mark.parametrize("suite", SUITE_NAMES)
def test_scripted_adapter_injects_exactly_annotated_defects(suite) -> None:
    fixture = load_suite_fixture(suite, EVALS_ROOT, DATA_ROOT)
    adapter = ScriptedRegressionAdapter.from_fixtures()
    baseline = {r.case_id: r for r in fixture.baseline}
    cases = {c.case_id: c for c in fixture.cases}
    for exp in fixture.expectations:
        candidate = adapter.generate(cases[exp.case_id], _ctx("candidate"))
        base_fields = _behavioral_fields(baseline[exp.case_id])
        cand_fields = _behavioral_fields(candidate)
        if exp.is_control and exp.expected_attribution == "app":
            # A true control: the scripted candidate is behaviorally identical.
            assert base_fields == cand_fields, f"{suite}/{exp.case_id}"
        else:
            # A real regression (or an infra-noise injection) must differ.
            assert base_fields != cand_fields, f"{suite}/{exp.case_id}"


def test_scripted_infra_case_injects_infra_origin_error() -> None:
    adapter = ScriptedRegressionAdapter.from_fixtures()
    case = _case("at_infra_noise", risk="agent")
    candidate = adapter.generate(case, _ctx("candidate"))
    assert candidate.errors, "expected an injected error"
    assert all(e.origin == "infra" for e in candidate.errors)


def test_scripted_true_controls_have_no_injection() -> None:
    adapter = ScriptedRegressionAdapter.from_fixtures()
    for suite in SUITE_NAMES:
        fixture = load_suite_fixture(suite, EVALS_ROOT, DATA_ROOT)
        for exp in fixture.expectations:
            is_true_control = (
                exp.expected_category == NO_REGRESSION
                and exp.expected_attribution == "app"
            )
            if is_true_control:
                assert adapter.injection_for(exp.case_id) is None, exp.case_id


def test_scripted_plan_covers_every_noncontrol_case() -> None:
    adapter = ScriptedRegressionAdapter.from_fixtures()
    for suite in SUITE_NAMES:
        fixture = load_suite_fixture(suite, EVALS_ROOT, DATA_ROOT)
        for exp in fixture.expectations:
            if exp.expected_category != NO_REGRESSION:
                assert adapter.injection_for(exp.case_id) is not None, exp.case_id


def test_scripted_adapter_raises_on_unknown_case() -> None:
    adapter = ScriptedRegressionAdapter.from_fixtures()
    with pytest.raises(AdapterError, match="no baseline response"):
        adapter.generate(_case("nope"), _ctx("candidate"))


def test_scripted_candidate_differs_from_recorded_baseline() -> None:
    # End-to-end: baseline via recorded adapter, candidate via scripted adapter,
    # exactly as the smoke pipeline wires them (docs/PLAN.md §9).
    recorded = RecordedModelAdapter.from_fixtures()
    scripted = ScriptedRegressionAdapter.from_fixtures()
    case = _case("lc_latency_spike", risk="benign")
    base = recorded.generate(case, _ctx("baseline"))
    cand = scripted.generate(case, _ctx("candidate"))
    assert cand.latency_ms > base.latency_ms
    assert cand.cost_estimate_usd == base.cost_estimate_usd


# --------------------------------------------------------------------------- #
# ApiAdapterStub — inert by default. Milestone 11 built out the guards behind the
# opt-in; the contract asserted *here* is the one every other adapter relies on:
# constructing the API adapter the ordinary way reaches nothing. The guard ladder
# itself (dry-run, estimator, ceiling, cache, transport) is tested in
# tests/test_api_stub.py.
# --------------------------------------------------------------------------- #


def test_api_stub_disabled_by_default() -> None:
    stub = ApiAdapterStub()
    assert stub.enabled is False
    with pytest.raises(ApiAdapterDisabled, match="disabled by default"):
        stub.generate(_case("c1"), _ctx())


def test_api_stub_default_construction_touches_nothing() -> None:
    # No cache directory, no transport, no budget: three independent reasons a
    # default-constructed adapter cannot spend.
    stub = ApiAdapterStub()
    assert stub.cache.enabled is False
    assert stub.transport is None
    assert stub.ledger.ceiling_usd == 0.0
    assert stub.guard.dry_run is True


def test_api_stub_disabled_error_is_adapter_error() -> None:
    assert issubclass(ApiAdapterDisabled, AdapterError)


# --------------------------------------------------------------------------- #
# Cross-adapter determinism.
# --------------------------------------------------------------------------- #


def test_generate_all_matches_per_case_generate() -> None:
    adapter = RecordedModelAdapter.from_fixtures()
    fixture = load_suite_fixture("structured_json", EVALS_ROOT, DATA_ROOT)
    ctx = _ctx("baseline")
    batch = adapter.generate_all(fixture.cases, ctx)
    one_by_one = [adapter.generate(c, ctx) for c in fixture.cases]
    assert [r.to_json() for r in batch] == [r.to_json() for r in one_by_one]
