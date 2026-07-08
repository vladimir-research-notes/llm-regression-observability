"""Tests for the core data contracts (``schemas.py``).

CPU-only and offline. They assert the three properties the rest of the pipeline
relies on:

* **Round-trip** — ``from_dict(to_dict(x)) == x`` for every model, including
  nested ones.
* **Validation** — missing required fields, unknown fields, wrong types, and
  out-of-vocabulary enum values are rejected with :class:`SchemaError`.
* **Byte-stable serialization** — ``to_json()`` sorts keys and is deterministic,
  and ``int`` coerces to ``float`` so numeric output does not drift.
"""

from __future__ import annotations

import json

import pytest

from llm_regression_observability.schemas import (
    ComparisonRecord,
    ErrorRecord,
    EvalCase,
    EvalSuite,
    FailureCluster,
    MetricResult,
    ModelTrace,
    RegressionFlag,
    ReportSummary,
    RetrievalDoc,
    RetrievalTrace,
    RunRecord,
    Schema,
    SchemaError,
    ToolCallTrace,
)

# --------------------------------------------------------------------------- #
# Fixtures: one fully-populated instance of each model.
# --------------------------------------------------------------------------- #


def make_eval_case() -> EvalCase:
    return EvalCase(
        case_id="case-001",
        version="1",
        prompt="Summarize the report as JSON.",
        risk_category="structured",
        input_payload={"report": "synthetic text"},
        expected_properties={"json_schema_valid": True},
        json_schema={"type": "object", "required": ["title"]},
        allowed_tools=["search", "lookup"],
        retrieval_docs=[
            RetrievalDoc(source_id="doc-1", text="synthetic evidence", title="Doc 1"),
            RetrievalDoc(source_id="doc-2", text="more evidence"),
        ],
        tags=["smoke", "json"],
    )


def make_eval_suite() -> EvalSuite:
    return EvalSuite(
        suite_id="smoke",
        description="Synthetic smoke suite.",
        thresholds={"latency_p95_rel": 0.25, "cost_rel": 0.20},
        cases=[make_eval_case()],
    )


def make_run_record() -> RunRecord:
    return RunRecord(
        run_id="run-baseline-001",
        case_id="case-001",
        app_version="baseline",
        model_id="mock-1",
        prompt_version="p1",
        tool_version="t1",
        timestamp="2026-01-01T00:00:00Z",
        input_tokens_estimate=42,
        output_tokens_estimate=17,
        latency_ms=123.5,
        cost_estimate_usd=0.0012,
        final_output='{"title": "x"}',
        parsed_output={"title": "x"},
        tool_calls=[
            ToolCallTrace(
                name="search",
                arguments={"query": "synthetic"},
                order_index=0,
                status="ok",
            ),
            ToolCallTrace(name="lookup", order_index=1, status="failed", error="404"),
        ],
        retrieval_sources=[
            RetrievalTrace(
                source_id="doc-1", snippet="synthetic", claim="title=x", supported=True
            )
        ],
        errors=[ErrorRecord(kind="tool_error", message="404", origin="app")],
        redaction_status="clean",
    )


def make_comparison_record() -> ComparisonRecord:
    return ComparisonRecord(
        case_id="case-001",
        baseline_run_id="run-baseline-001",
        candidate_run_id="run-candidate-001",
        changed_fields=["final_output", "latency_ms"],
        score_delta=-1.0,
        latency_delta=40.0,
        cost_delta=0.0003,
        json_validity_delta=-1.0,
        regression_flags=[
            RegressionFlag(
                category="structured_output_regression",
                severity="critical",
                metric="json_schema_validity",
                observed=0.0,
                threshold=1.0,
                attribution="app",
                explanation="Schema valid at baseline, invalid at candidate.",
            )
        ],
        human_readable_summary="Structured output regressed on case-001.",
    )


def make_report_summary() -> ReportSummary:
    return ReportSummary(
        comparison_id="cmp-001",
        suite_id="smoke",
        baseline_version="baseline",
        candidate_version="candidate",
        case_count=6,
        regressed_case_count=2,
        flags_by_category={"structured_output_regression": 1, "latency_regression": 1},
        latency_p50_delta=10.0,
        latency_p95_delta=40.0,
        cost_total_delta=0.001,
        clusters=[
            FailureCluster(
                cluster_id="cl-1",
                label="JSON validity dropped",
                category="structured_output_regression",
                case_ids=["case-001"],
            )
        ],
    )


ALL_FIXTURES = [
    make_eval_case,
    make_eval_suite,
    make_run_record,
    make_comparison_record,
    make_report_summary,
    lambda: MetricResult(
        case_id="case-001", metric="json_schema_validity", value=1.0, passed=True
    ),
    lambda: ToolCallTrace(name="search", arguments={"q": "x"}, order_index=0),
    lambda: RetrievalTrace(source_id="doc-1", snippet="s", claim=None, supported=None),
    lambda: ErrorRecord(kind="timeout", message="slow", origin="infra"),
    lambda: RegressionFlag(
        category="latency_regression",
        severity="warn",
        metric="latency_delta_p95",
        observed=0.4,
        threshold=0.25,
    ),
]


# --------------------------------------------------------------------------- #
# Round-trip
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("factory", ALL_FIXTURES)
def test_round_trip_dict(factory) -> None:
    obj = factory()
    rebuilt = type(obj).from_dict(obj.to_dict())
    assert rebuilt == obj
    assert rebuilt is not obj


@pytest.mark.parametrize("factory", ALL_FIXTURES)
def test_round_trip_json(factory) -> None:
    obj = factory()
    assert type(obj).from_json(obj.to_json()) == obj


def test_nested_objects_survive_round_trip() -> None:
    rr = make_run_record()
    rebuilt = RunRecord.from_dict(rr.to_dict())
    # Nested items are rebuilt as their proper types, not left as dicts.
    assert isinstance(rebuilt.tool_calls[0], ToolCallTrace)
    assert isinstance(rebuilt.retrieval_sources[0], RetrievalTrace)
    assert isinstance(rebuilt.errors[0], ErrorRecord)
    assert rebuilt.tool_calls[1].status == "failed"

    suite = make_eval_suite()
    rebuilt_suite = EvalSuite.from_dict(suite.to_dict())
    assert isinstance(rebuilt_suite.cases[0], EvalCase)
    assert isinstance(rebuilt_suite.cases[0].retrieval_docs[0], RetrievalDoc)


def test_model_trace_is_run_record_alias() -> None:
    assert ModelTrace is RunRecord


# --------------------------------------------------------------------------- #
# Byte-stable serialization
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("factory", ALL_FIXTURES)
def test_json_keys_sorted_at_every_level(factory) -> None:
    doc = json.loads(factory().to_json())

    def check(node) -> None:
        if isinstance(node, dict):
            keys = list(node.keys())
            assert keys == sorted(keys), keys
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for item in node:
                check(item)

    check(doc)


def test_serialization_is_byte_identical_across_instances() -> None:
    # Two independently constructed equal objects serialize to identical bytes.
    assert make_run_record().to_json() == make_run_record().to_json()
    assert make_eval_suite().to_json() == make_eval_suite().to_json()


def test_to_dict_preserves_declared_field_order() -> None:
    keys = list(make_run_record().to_dict().keys())
    assert keys[:3] == ["run_id", "case_id", "app_version"]
    assert keys[-1] == "redaction_status"


def test_int_is_coerced_to_float_for_float_fields() -> None:
    rr = RunRecord(
        run_id="r",
        case_id="c",
        app_version="baseline",
        model_id="m",
        prompt_version="p",
        tool_version="t",
        timestamp="2026-01-01T00:00:00Z",
        latency_ms=120,  # int in, float stored
        cost_estimate_usd=0,  # int in, float stored
    )
    assert isinstance(rr.latency_ms, float)
    assert isinstance(rr.cost_estimate_usd, float)
    # Byte output is the float form, so an int input never drifts the artifact.
    assert '"latency_ms":120.0' in rr.to_json()


def test_metric_value_int_coerced_to_float() -> None:
    assert isinstance(MetricResult(case_id="c", metric="m", value=1).value, float)


# --------------------------------------------------------------------------- #
# Validation: missing / unknown fields
# --------------------------------------------------------------------------- #


def test_missing_required_field_rejected() -> None:
    with pytest.raises(SchemaError, match="missing required field 'version'"):
        EvalCase.from_dict(
            {"case_id": "c", "prompt": "p", "risk_category": "benign"}
        )


def test_unknown_field_rejected() -> None:
    with pytest.raises(SchemaError, match="unknown field"):
        EvalCase.from_dict(
            {
                "case_id": "c",
                "version": "1",
                "prompt": "p",
                "risk_category": "benign",
                "typo_field": 1,
            }
        )


def test_from_dict_requires_a_dict() -> None:
    with pytest.raises(SchemaError, match="expected object"):
        EvalCase.from_dict(["not", "a", "dict"])


# --------------------------------------------------------------------------- #
# Validation: wrong types
# --------------------------------------------------------------------------- #


def test_wrong_scalar_type_rejected() -> None:
    with pytest.raises(SchemaError, match="expected float"):
        MetricResult(case_id="c", metric="m", value="fast")


def test_bool_rejected_where_int_expected() -> None:
    # bool is a subclass of int; a token count must be a real int.
    with pytest.raises(SchemaError, match="expected int"):
        RunRecord(
            run_id="r",
            case_id="c",
            app_version="baseline",
            model_id="m",
            prompt_version="p",
            tool_version="t",
            timestamp="2026-01-01T00:00:00Z",
            input_tokens_estimate=True,
        )


def test_wrong_list_element_type_rejected() -> None:
    with pytest.raises(SchemaError, match=r"tags\[1\]"):
        EvalCase(
            case_id="c",
            version="1",
            prompt="p",
            risk_category="benign",
            tags=["ok", 5],
        )


def test_wrong_nested_container_rejected() -> None:
    with pytest.raises(SchemaError, match="expected list"):
        RunRecord.from_dict(
            {
                "run_id": "r",
                "case_id": "c",
                "app_version": "baseline",
                "model_id": "m",
                "prompt_version": "p",
                "tool_version": "t",
                "timestamp": "2026-01-01T00:00:00Z",
                "tool_calls": {"not": "a list"},
            }
        )


def test_flags_by_category_value_type_enforced() -> None:
    with pytest.raises(SchemaError, match="expected int"):
        ReportSummary(
            comparison_id="cmp",
            suite_id="s",
            baseline_version="baseline",
            candidate_version="candidate",
            flags_by_category={"latency_regression": "one"},
        )


def test_optional_field_accepts_none_and_value() -> None:
    assert RetrievalTrace(source_id="d", snippet="s").supported is None
    assert RetrievalTrace(source_id="d", snippet="s", supported=True).supported is True


# --------------------------------------------------------------------------- #
# Validation: closed vocabularies
# --------------------------------------------------------------------------- #


def test_bad_risk_category_rejected() -> None:
    with pytest.raises(SchemaError, match="risk_category"):
        EvalCase(case_id="c", version="1", prompt="p", risk_category="not-a-category")


def test_bad_tool_status_rejected() -> None:
    with pytest.raises(SchemaError, match="status"):
        ToolCallTrace(name="s", status="weird")


def test_bad_error_origin_rejected() -> None:
    with pytest.raises(SchemaError, match="origin"):
        ErrorRecord(kind="k", message="m", origin="unknown")


def test_bad_redaction_status_rejected() -> None:
    with pytest.raises(SchemaError, match="redaction_status"):
        RunRecord(
            run_id="r",
            case_id="c",
            app_version="baseline",
            model_id="m",
            prompt_version="p",
            tool_version="t",
            timestamp="2026-01-01T00:00:00Z",
            redaction_status="scrubbed",
        )


def test_bad_severity_and_attribution_rejected() -> None:
    with pytest.raises(SchemaError, match="severity"):
        RegressionFlag(
            category="c", severity="huge", metric="m", observed=1.0, threshold=0.5
        )
    with pytest.raises(SchemaError, match="attribution"):
        RegressionFlag(
            category="c",
            severity="warn",
            metric="m",
            observed=1.0,
            threshold=0.5,
            attribution="external",
        )


def test_nested_vocab_error_reports_path() -> None:
    with pytest.raises(SchemaError, match="status"):
        RunRecord.from_dict(
            {
                "run_id": "r",
                "case_id": "c",
                "app_version": "baseline",
                "model_id": "m",
                "prompt_version": "p",
                "tool_version": "t",
                "timestamp": "2026-01-01T00:00:00Z",
                "tool_calls": [{"name": "s", "status": "weird"}],
            }
        )


# --------------------------------------------------------------------------- #
# Free-form payloads are not over-validated
# --------------------------------------------------------------------------- #


def test_free_form_payloads_allow_arbitrary_nested_data() -> None:
    case = EvalCase(
        case_id="c",
        version="1",
        prompt="p",
        risk_category="benign",
        input_payload={"nested": {"any": [1, 2, {"k": "v"}]}, "count": 3},
        expected_properties={"contains": ["x"], "min": 0.5},
    )
    assert EvalCase.from_dict(case.to_dict()) == case


def test_parsed_output_accepts_any_shape() -> None:
    for value in (None, {"a": 1}, [1, 2, 3], "raw string", 42):
        rr = RunRecord(
            run_id="r",
            case_id="c",
            app_version="baseline",
            model_id="m",
            prompt_version="p",
            tool_version="t",
            timestamp="2026-01-01T00:00:00Z",
            parsed_output=value,
        )
        assert RunRecord.from_dict(rr.to_dict()).parsed_output == value


def test_schema_error_is_a_value_error() -> None:
    assert issubclass(SchemaError, ValueError)
    assert issubclass(EvalCase, Schema)
