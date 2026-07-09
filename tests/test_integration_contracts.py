"""Tests for the integration contracts (Milestone 10).

CPU-only and offline. **No sibling repository is required, present, or read.** The
five external trace shapes are declared by this repository and pinned against the
committed synthetic examples under ``data/external/``; if a real emitter ever
differs, the importer is where the difference is absorbed and these tests are where
it stops being silent.

An importer is the one place in this pipeline that meets a vocabulary it does not
control, so the tests are organized around the four ways such a mapping lies.

**It could drop a field.** Every key of a source document is mapped, deliberately
ignored, or unknown. An unknown key refuses in strict mode and is recorded in
``ImportResult.unknown_fields`` in lenient mode. Asserted against every importer in
the registry, not one by one, so a new source cannot arrive without the property.

**It could invent a measurement.** A ``claude_api`` response carries no duration.
Importing it as ``0.0`` would not be missing data — it would be a *number*, and the
comparison engine believes numbers: against a timed baseline it reads as an
improvement, and as the baseline it makes every candidate an unbounded
``latency_regression``. Both halves of that sentence are tested, on real thresholds,
before the refusal that prevents them is tested.

**It could guess an attribution.** ``app`` versus ``infra`` is this repo's
distinction, and no external system emits it. Each importer publishes a table; an
unmapped error kind resolves to ``app`` *and warns*, because ``infra`` withholds
regression flags and guessing it would hide a real regression. There is a test for
the withholding, not just for the default.

**It could import a secret.** Milestone 9 left ``redaction_status="flagged"``
unreachable on any trace this project writes, and named the import boundary as where
it becomes reachable. It does, here: content is masked and a credential-like string
in a join key refuses the import without echoing the value.

Two claims close the milestone. An imported trace is **scored by the existing
evaluators unchanged** — ``evaluate_case`` on a ``(case, record)`` pair the importer
produced, with exact expected values — and it flows through the **unchanged**
executor, comparison engine, span projection and report generator: the four-case
``external`` suite imports two code-agent sessions and detects exactly the two
injected defects, with zero false positives on the two controls.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from llm_regression_observability import cli
from llm_regression_observability.comparison import compare_runs
from llm_regression_observability.datasets import (
    CONTRACT_VERSION,
    NATIVE_SCHEMA,
    NATIVE_SOURCE,
    SUPPORTED_CONTRACT_VERSIONS,
    TraceBundle,
    VersioningError,
    export_bundle,
    is_envelope,
    load_eval_suite,
    read_bundle,
    record_schema_fingerprint,
    repo_root,
    versioning,
    write_bundle,
)
from llm_regression_observability.datasets.importers import (
    IMPORT_META_JSON,
    IMPORTERS,
    SOURCES,
    ImportContext,
    ImportMeta,
    TraceImportError,
    get_importer,
    import_bundle,
    import_document,
    import_path,
    import_run,
)
from llm_regression_observability.evaluators import evaluate_case, metrics_by_name
from llm_regression_observability.evaluators.refusal import refusal_decision
from llm_regression_observability.executor import DEFAULT_TIMESTAMP, RUN_JSONL
from llm_regression_observability.observability.spans import (
    build_case_spans,
    validate_spans,
)
from llm_regression_observability.reports import generate_report
from llm_regression_observability.schemas import RunRecord
from llm_regression_observability.traces.redaction import FLAGGED, REDACTED
from llm_regression_observability.traces.storage import render_jsonl

REPO_ROOT = repo_root()
EXTERNAL_ROOT = REPO_ROOT / "data" / "external"
EXTERNAL_SUITE = REPO_ROOT / "evals" / "suites" / "external.yaml"

#: The fingerprint of the current ``RunRecord`` field set. Pinned rather than
#: recomputed: changing the trace model must fail this test, so that somebody
#: decides what the change means for a consumer that already holds a bundle.
EXPECTED_RECORD_SCHEMA = "7c781c9c2fad"

#: Foreign sources, i.e. everything but the identity importer.
FOREIGN_SOURCES = tuple(s for s in SOURCES if s != NATIVE_SOURCE)

#: Where each foreign source's committed synthetic example lives.
EXAMPLES: dict[str, Path] = {
    "code_agent_runtime": EXTERNAL_ROOT / "code_agent_runtime" / "baseline.json",
    "bioagentbench": EXTERNAL_ROOT / "bioagentbench" / "example.json",
    "bioscientist_os": EXTERNAL_ROOT / "bioscientist_os" / "example.json",
    "agent_control_bench": EXTERNAL_ROOT / "agent_control_bench" / "example.json",
    "claude_api": EXTERNAL_ROOT / "claude_api" / "example.json",
}


def _document(source: str) -> Any:
    return json.loads(EXAMPLES[source].read_text(encoding="utf-8"))


def _import(source: str, document: Any = None, **context_kwargs: Any):
    context = ImportContext(**context_kwargs)
    doc = _document(source) if document is None else document
    return import_document(doc, context, source=source)


def _fixture_records() -> list[RunRecord]:
    """Four committed baseline records — a realistic native payload."""
    from llm_regression_observability.datasets import (
        load_suite_fixture,
        resolve_fixture_roots,
    )

    evals_root, data_root = resolve_fixture_roots(None)
    return load_suite_fixture("tool_use", evals_root, data_root).baseline


# --------------------------------------------------------------------------- #
# The envelope
# --------------------------------------------------------------------------- #


def test_the_record_schema_fingerprint_is_pinned() -> None:
    """If this fails, ``RunRecord`` changed and every exported bundle is now stale.

    The fingerprint is *derived* from the dataclass, not declared, which is the whole
    point: a hand-maintained version constant is the one that nobody remembers to
    bump. Update the constant deliberately, and say in the commit what a consumer
    holding an older bundle should do.
    """
    assert record_schema_fingerprint() == EXPECTED_RECORD_SCHEMA


def test_the_fingerprint_moves_when_the_trace_model_moves(monkeypatch) -> None:
    @dataclasses.dataclass
    class _Extended(RunRecord):
        surprise: str = ""

    monkeypatch.setattr(versioning, "RunRecord", _Extended)
    assert record_schema_fingerprint() != EXPECTED_RECORD_SCHEMA


def test_export_is_deterministic_and_takes_its_timestamp_as_an_input() -> None:
    records = _fixture_records()
    stamp = "2026-06-01T12:00:00Z"
    first = export_bundle(records, exported_at=stamp, suite_id="tool_use")
    second = export_bundle(records, exported_at=stamp, suite_id="tool_use")
    assert first.to_json() == second.to_json()
    assert first.exported_at == "2026-06-01T12:00:00Z"


def test_export_bundle_requires_an_exported_at() -> None:
    """No default: a wall-clock default would make two exports of one run differ."""
    with pytest.raises(TypeError):
        export_bundle(_fixture_records())  # type: ignore[call-arg]


def test_an_unsupported_contract_version_is_refused(tmp_path: Path) -> None:
    bundle = export_bundle(_fixture_records(), exported_at=DEFAULT_TIMESTAMP)
    payload = json.loads(bundle.to_json())
    payload["contract_version"] = "99.0"
    path = tmp_path / "future.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(VersioningError, match="contract_version '99.0'"):
        read_bundle(path)


def test_an_edited_payload_fails_its_digest(tmp_path: Path) -> None:
    bundle = export_bundle(_fixture_records(), exported_at=DEFAULT_TIMESTAMP)
    payload = json.loads(bundle.to_json())
    payload["payload"]["records"][0]["final_output"] = "tampered"
    path = tmp_path / "edited.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(VersioningError, match="digest mismatch"):
        read_bundle(path)


def test_a_bare_document_is_not_an_envelope() -> None:
    bundle = export_bundle(_fixture_records(), exported_at=DEFAULT_TIMESTAMP)
    assert not is_envelope(_document("code_agent_runtime"))
    assert is_envelope(json.loads(bundle.to_json()))


def test_read_bundle_refuses_a_bare_source_document() -> None:
    with pytest.raises(VersioningError, match="not a trace bundle"):
        read_bundle(EXAMPLES["code_agent_runtime"])


def test_a_bundle_from_a_different_record_schema_refuses_then_warns() -> None:
    bundle = export_bundle(_fixture_records(), exported_at=DEFAULT_TIMESTAMP)
    stale = dataclasses.replace(bundle, record_schema="deadbeef0000")
    with pytest.raises(VersioningError, match="exported against RunRecord schema"):
        import_bundle(stale, ImportContext(strict=True))
    result = import_bundle(stale, ImportContext(strict=False))
    assert any("exported against RunRecord schema" in w for w in result.warnings)
    assert len(result.records) == len(bundle.payload["records"])


# --------------------------------------------------------------------------- #
# The native round trip: the contract's reference implementation
# --------------------------------------------------------------------------- #


def test_the_native_bundle_round_trips_byte_for_byte() -> None:
    """``import(export(records)) == records``. The five foreign importers lose
    something and say what; this one must lose nothing."""
    records = _fixture_records()
    suite_cases = load_eval_suite(EXTERNAL_SUITE).cases

    bundle = export_bundle(
        records, exported_at=DEFAULT_TIMESTAMP, suite_id="tool_use", cases=suite_cases
    )
    result = import_bundle(bundle, ImportContext())

    assert list(result.records) == records
    assert render_jsonl(result.records) == render_jsonl(records)
    assert [c.case_id for c in result.cases] == [c.case_id for c in suite_cases]
    assert result.source == NATIVE_SOURCE
    assert result.source_schema == NATIVE_SCHEMA
    assert result.warnings == ()
    assert result.absent_fields == ()


def test_a_written_bundle_reads_back_identically(tmp_path: Path) -> None:
    bundle = export_bundle(_fixture_records(), exported_at=DEFAULT_TIMESTAMP)
    path = write_bundle(tmp_path / "b.json", bundle)
    assert read_bundle(path).to_json() == bundle.to_json()


# --------------------------------------------------------------------------- #
# Properties every importer holds, whoever writes the next one
# --------------------------------------------------------------------------- #


def test_the_registry_is_consistent() -> None:
    for name, importer in IMPORTERS.items():
        assert importer.source == name
    assert SOURCES == tuple(sorted(IMPORTERS))
    assert NATIVE_SOURCE in SOURCES


def test_the_cli_source_choices_match_the_registry() -> None:
    assert cli.IMPORT_SOURCE_CHOICES == SOURCES


@pytest.mark.parametrize("source", SOURCES)
def test_every_importer_publishes_a_well_formed_contract(source: str) -> None:
    importer = get_importer(source)
    record_fields = {f.name for f in dataclasses.fields(RunRecord)}
    assert set(importer.error_origins.values()) <= {"app", "infra"}
    assert set(importer.absent_fields) <= record_fields, importer.absent_fields
    assert importer.source_schema


@pytest.mark.parametrize("source", FOREIGN_SOURCES)
def test_every_foreign_source_has_a_committed_synthetic_example(source: str) -> None:
    assert EXAMPLES[source].is_file()
    result = _import(source)
    assert result.records
    assert result.source == source


@pytest.mark.parametrize("source", FOREIGN_SOURCES)
def test_an_unmapped_source_field_refuses_in_strict_mode(source: str) -> None:
    document = _document(source)
    document["a_field_this_importer_has_never_seen"] = 1
    with pytest.raises(TraceImportError, match="unmapped field"):
        _import(source, document)


@pytest.mark.parametrize("source", FOREIGN_SOURCES)
def test_an_unmapped_source_field_is_recorded_in_lenient_mode(source: str) -> None:
    document = _document(source)
    document["a_field_this_importer_has_never_seen"] = 1
    result = _import(source, document, strict=False)
    assert result.unknown_fields == ("document.a_field_this_importer_has_never_seen",)
    assert any("unmapped field" in w for w in result.warnings)


@pytest.mark.parametrize("source", FOREIGN_SOURCES)
def test_an_unknown_source_schema_version_refuses_in_strict_mode(source: str) -> None:
    document = _document(source)
    for key in ("schema_version", "format_version", "trace_version", "api_version"):
        if key in document:
            document[key] = "999.0"
    with pytest.raises(TraceImportError, match="not one this importer was written"):
        _import(source, document)


def test_an_unknown_source_is_refused() -> None:
    with pytest.raises(TraceImportError, match="no importer for source"):
        get_importer("some_other_lab")


def test_a_bare_document_must_name_its_source() -> None:
    with pytest.raises(TraceImportError, match="does not name its source"):
        import_document(_document("code_agent_runtime"), ImportContext())


def test_a_source_that_contradicts_the_envelope_is_refused() -> None:
    bundle = export_bundle(_fixture_records(), exported_at=DEFAULT_TIMESTAMP)
    document = json.loads(bundle.to_json())
    with pytest.raises(TraceImportError, match="contradicts the bundle"):
        import_document(document, ImportContext(), source="claude_api")


def test_two_records_for_one_case_are_refused() -> None:
    document = _document("code_agent_runtime")
    document["tasks"].append(copy.deepcopy(document["tasks"][0]))
    with pytest.raises(TraceImportError, match="two records for case"):
        _import("code_agent_runtime", document)


# --------------------------------------------------------------------------- #
# code_agent_runtime: steps, order, provenance, attribution
# --------------------------------------------------------------------------- #


def test_message_steps_are_dropped_and_tool_order_ignores_them() -> None:
    result = _import("code_agent_runtime")
    record = result.record_for("tu_wrong_tool")
    # The document interleaves search / message / summarize.
    assert [c.name for c in record.tool_calls] == ["search", "summarize"]
    assert [c.order_index for c in record.tool_calls] == [0, 1]
    assert "tasks[].steps[].text" in result.ignored_fields
    assert "message" not in render_jsonl([record])


def test_agent_version_becomes_the_tool_version_and_prompt_version_is_unknown() -> None:
    record = _import("code_agent_runtime").record_for("tu_wrong_tool")
    assert record.tool_version == "ca-1.4.0"
    assert record.prompt_version == "unknown"
    assert record.model_id == "synthetic-coder-1"


def test_a_caller_override_outranks_the_source() -> None:
    record = _import(
        "code_agent_runtime", tool_version="t-9", model_id="m-9", prompt_version="p-9"
    ).record_for("tu_wrong_tool")
    assert (record.tool_version, record.model_id, record.prompt_version) == (
        "t-9",
        "m-9",
        "p-9",
    )


def test_a_failed_step_becomes_a_failed_tool_call() -> None:
    document = json.loads(
        (EXTERNAL_ROOT / "code_agent_runtime" / "candidate.json").read_text("utf-8")
    )
    result = import_document(document, ImportContext(), source="code_agent_runtime")
    record = result.record_for("at_more_retries")
    assert [c.status for c in record.tool_calls] == ["failed", "ok", "ok"]
    assert [c.order_index for c in record.tool_calls] == [0, 1, 2]
    assert [(e.kind, e.origin) for e in record.errors] == [("tool_retry", "app")]


def test_a_failure_type_with_no_origin_mapping_defaults_to_app_and_warns() -> None:
    """``infra`` withholds flags, so an unrecognized error must not resolve there."""
    document = _document("code_agent_runtime")
    document["tasks"][0]["failures"] = [{"type": "novel_failure", "detail": "d"}]
    result = _import("code_agent_runtime", document)
    record = result.record_for("tu_wrong_tool")
    assert [(e.kind, e.origin) for e in record.errors] == [("novel_failure", "app")]
    assert any("no origin mapping" in w for w in result.warnings)


def test_a_mapped_infra_failure_is_attributed_to_the_harness() -> None:
    document = _document("code_agent_runtime")
    document["tasks"][0]["failures"] = [{"type": "sandbox_error", "detail": "d"}]
    record = _import("code_agent_runtime", document).record_for("tu_wrong_tool")
    assert [e.origin for e in record.errors] == ["infra"]


def test_an_unknown_step_kind_refuses() -> None:
    document = _document("code_agent_runtime")
    document["tasks"][0]["steps"].append({"kind": "telepathy"})
    with pytest.raises(TraceImportError, match="known kinds are"):
        _import("code_agent_runtime", document)


# --------------------------------------------------------------------------- #
# bioagentbench: units, task definitions, evidence
# --------------------------------------------------------------------------- #


def test_wallclock_seconds_becomes_milliseconds() -> None:
    """The quietest, most expensive bug an importer can have."""
    record = _import("bioagentbench").record_for("bab_grounded_answer")
    assert record.latency_ms == 420.0  # 0.42 s


def test_the_task_definition_becomes_a_scorable_eval_case() -> None:
    result = _import("bioagentbench")
    case = result.case_for("bab_grounded_answer")
    assert case.risk_category == "retrieval"
    assert case.expected_properties["required_sources"] == ["doc-a"]
    assert [d.source_id for d in case.retrieval_docs or []] == ["doc-a", "doc-b"]
    assert case.allowed_tools == ["search_corpus"]


def test_an_imported_episode_is_scored_by_the_existing_evaluators_unchanged() -> None:
    """The milestone's headline claim, spelled out in exact expected values."""
    result = _import("bioagentbench")
    case = result.case_for("bab_grounded_answer")
    record = result.record_for("bab_grounded_answer")
    metrics = metrics_by_name(evaluate_case(case, record))

    assert metrics["task_success"].value == 1.0
    assert metrics["task_success"].passed is True
    assert metrics["evidence_coverage"].value == 1.0
    assert metrics["citation_presence"].value == 1.0
    assert metrics["citation_correctness"].value == 1.0
    assert metrics["unsupported_claim_count"].value == 0.0
    assert metrics["allowed_tool_ratio"].value == 1.0
    assert metrics["latency_ms"].value == 420.0
    assert metrics["cost_estimate_usd"].value == 0.0012
    assert metrics["infra_error_count"].value == 0.0


def test_a_grader_crash_is_infra_and_never_an_application_error() -> None:
    result = _import("bioagentbench")
    record = result.record_for("bab_grader_noise")
    case = result.case_for("bab_grader_noise")
    assert [(e.kind, e.origin) for e in record.errors] == [("grader_error", "infra")]
    metrics = metrics_by_name(evaluate_case(case, record))
    assert metrics["infra_error_count"].value == 1.0
    assert metrics["app_error_count"].value == 0.0


def test_an_unmarked_citation_scores_as_unsupported() -> None:
    """``supported`` is recorded, never judged. The conservative reading is safe."""
    document = _document("bioagentbench")
    del document["episodes"][0]["citations"][0]["supports_claim"]
    result = _import("bioagentbench", document)
    record = result.record_for("bab_grounded_answer")
    case = result.case_for("bab_grounded_answer")
    assert record.retrieval_sources[0].supported is None
    metrics = metrics_by_name(evaluate_case(case, record))
    assert metrics["unsupported_claim_count"].value == 1.0


# --------------------------------------------------------------------------- #
# bioscientist_os: flattening a workflow, and admitting what it costs
# --------------------------------------------------------------------------- #


def test_node_durations_and_costs_are_summed() -> None:
    record = _import("bioscientist_os").record_for("bsos_plan_and_summarize")
    assert record.latency_ms == 220.0  # 120 + 40 + 60
    assert record.cost_estimate_usd == 0.0012  # 0.0008 + 0.0 + 0.0004
    assert record.input_tokens_estimate == 120  # 90 + 30
    assert record.output_tokens_estimate == 45  # 20 + 25


def test_a_multi_model_run_is_recorded_as_multi_and_says_what_was_lost() -> None:
    result = _import("bioscientist_os")
    record = result.record_for("bsos_plan_and_summarize")
    assert record.model_id == "multi:synthetic-planner-1+synthetic-writer-1"
    assert any("Per-model attribution is lost" in w for w in result.warnings)


def test_a_failed_workflow_is_an_application_error() -> None:
    record = _import("bioscientist_os").record_for("bsos_failed_workflow")
    assert [(e.kind, e.origin) for e in record.errors] == [("workflow_failed", "app")]


def test_a_trace_with_no_task_definition_has_a_vacuous_task_success() -> None:
    """Not ``1.0`` with a verdict, not ``0.0``: nothing was checked, nothing passed."""
    result = _import("bioscientist_os")
    case = result.case_for("bsos_plan_and_summarize")
    assert case.expected_properties == {}
    metrics = metrics_by_name(
        evaluate_case(case, result.record_for("bsos_plan_and_summarize"))
    )
    assert metrics["task_success"].value == 1.0
    assert metrics["task_success"].passed is None
    assert metrics["task_success"].detail["vacuous"] is True


# --------------------------------------------------------------------------- #
# agent_control_bench: the decision, and where it came from
# --------------------------------------------------------------------------- #


def test_the_refusal_decision_is_read_from_the_trace_not_guessed() -> None:
    result = _import("agent_control_bench")
    record = result.record_for("acb_declines_as_policy_requires")
    refused, source = refusal_decision(record)
    assert (refused, source) == (True, "parsed_output")
    assert record.parsed_output == {"refused": True, "risk_label": "high"}


def test_an_under_refusal_and_its_policy_violation_both_score() -> None:
    result = _import("agent_control_bench")
    case = result.case_for("acb_answers_and_violates_policy")
    record = result.record_for("acb_answers_and_violates_policy")
    metrics = metrics_by_name(evaluate_case(case, record))

    assert case.expected_properties == {
        "should_refuse": True,
        "expected_risk_label": "high",
    }
    assert metrics["refusal_match"].value == 0.0
    assert metrics["refusal_match"].detail["shift"] == "under_refusal"
    assert metrics["risk_label_match"].value == 0.0
    assert metrics["task_success"].value == 0.0
    assert [(e.kind, e.origin) for e in record.errors] == [("policy_bypass", "app")]


def test_the_policy_version_becomes_the_tool_version() -> None:
    record = _import("agent_control_bench").record_for("acb_answers_benign_prompt")
    assert record.tool_version == "acb-2026-06"


# --------------------------------------------------------------------------- #
# claude_api: content blocks, and the measurement that must not be invented
# --------------------------------------------------------------------------- #


def test_content_blocks_become_output_text_and_tool_calls() -> None:
    record = _import("claude_api").record_for("capi_tool_use")
    assert record.final_output == "Looking that up now."
    assert [(c.name, c.arguments) for c in record.tool_calls] == [
        ("search", {"query": "synthetic-001"})
    ]
    assert record.input_tokens_estimate == 40
    assert record.output_tokens_estimate == 18


def test_an_unknown_content_block_refuses_rather_than_vanishing() -> None:
    """A dropped block may be a tool call, and a dropped tool call is a flag that
    never fires."""
    document = _document("claude_api")
    document["records"][0]["response"]["content"].append({"type": "server_tool_use"})
    with pytest.raises(TraceImportError, match="known block types are"):
        _import("claude_api", document)


def test_a_thinking_block_is_dropped_by_name() -> None:
    document = _document("claude_api")
    document["records"][0]["response"]["content"].insert(
        0, {"type": "thinking", "thinking": "internal reasoning", "signature": "sig"}
    )
    result = _import("claude_api", document)
    assert "internal reasoning" not in render_jsonl(result.records)
    assert "content[].thinking" in result.ignored_fields


def test_max_tokens_becomes_a_truncated_output_error() -> None:
    record = _import("claude_api").record_for("capi_truncated_output")
    assert [(e.kind, e.origin) for e in record.errors] == [("truncated_output", "app")]


def test_a_refusal_without_a_label_degrades_visibly_to_the_lexical_fallback() -> None:
    record = _import("claude_api").record_for("capi_refusal_without_label")
    refused, source = refusal_decision(record)
    assert (refused, source) == (True, "lexical_fallback")


def test_a_response_with_no_recorded_duration_refuses() -> None:
    document = _document("claude_api")
    del document["records"][0]["latency_ms"]
    with pytest.raises(TraceImportError, match="would not be missing data"):
        _import("claude_api", document)


def test_the_same_response_imports_as_zero_in_lenient_mode_and_warns() -> None:
    document = _document("claude_api")
    del document["records"][0]["latency_ms"]
    result = _import("claude_api", document, strict=False)
    assert result.record_for("capi_tool_use").latency_ms == 0.0
    assert any("would not be missing data" in w for w in result.warnings)
    assert "latency_ms" in result.absent_fields


def test_why_a_zero_latency_is_worse_than_a_refusal(tmp_path: Path) -> None:
    """The two failure modes the refusal above exists to prevent, on real thresholds.

    A zeroed candidate reads as an *improvement* (no flag, and a real slowdown could
    hide behind it); a zeroed baseline makes the candidate an unbounded regression.
    """
    from llm_regression_observability.comparison.base import relative

    assert relative(0.0, 300.0) == -1.0  # candidate "got 100% faster"
    assert relative(300.0, 0.0) == float("inf")  # candidate regressed from nothing


# --------------------------------------------------------------------------- #
# No secret crosses the boundary (Milestone 9's open edge)
# --------------------------------------------------------------------------- #

# Assembled at runtime so no scanner finds a credential shape in this file.
FAKE_KEY = "sk-" + "B" * 24


def test_a_secret_in_content_is_masked_and_the_record_is_marked_redacted() -> None:
    document = _document("code_agent_runtime")
    document["tasks"][0]["result"]["text"] = f"the key is {FAKE_KEY}"
    result = _import("code_agent_runtime", document)
    record = result.record_for("tu_wrong_tool")

    assert FAKE_KEY not in record.final_output
    assert "[REDACTED:openai_key]" in record.final_output
    assert record.redaction_status == REDACTED
    assert any("redacted credential" in w for w in result.warnings)


def test_a_secret_in_a_join_key_refuses_the_import_without_echoing_it() -> None:
    """``flagged`` was unreachable before Milestone 10. This edge reaches it."""
    document = _document("code_agent_runtime")
    document["tasks"][0]["steps"][0]["tool"] = FAKE_KEY
    with pytest.raises(TraceImportError) as excinfo:
        _import("code_agent_runtime", document)
    message = str(excinfo.value)
    assert "tool_calls[0].name" in message
    assert "openai_key" in message
    assert FAKE_KEY not in message
    assert FLAGGED in message or "join key" in message


def test_no_committed_external_example_carries_a_secret() -> None:
    from llm_regression_observability.datasets import scan_for_secrets

    for path in sorted(EXTERNAL_ROOT.rglob("*.json")):
        assert not scan_for_secrets(path.read_text(encoding="utf-8")), path


# --------------------------------------------------------------------------- #
# The span model comes free: spans are a projection, not a payload
# --------------------------------------------------------------------------- #


def test_an_imported_record_projects_into_a_valid_span_tree() -> None:
    """No importer knows what a span is. It does not have to: ``build_case_spans`` is a
    pure function of ``(EvalCase, RunRecord, metrics)``, so mapping onto ``RunRecord``
    buys the whole observability layer."""
    result = _import("bioagentbench")
    case = result.case_for("bab_grounded_answer")
    record = result.record_for("bab_grounded_answer")

    spans = build_case_spans(case, record, evaluate_case(case, record))
    validate_spans(spans)

    names = [s.name for s in spans]
    assert names[0] == "request"
    assert names.count("tool_call") == 1
    assert names.count("retrieval") == 1
    assert spans[0].end_ms == 420.0  # the recorded duration, unchanged


def test_a_bundle_carries_no_spans() -> None:
    """Shipping a derivation the receiver can recompute and cannot verify."""
    bundle = export_bundle(_fixture_records(), exported_at=DEFAULT_TIMESTAMP)
    assert set(bundle.payload) <= {"records", "cases"}


# --------------------------------------------------------------------------- #
# End to end: an imported trace through the unchanged pipeline
# --------------------------------------------------------------------------- #


def _import_side(app_version: str, run_id: str, document: str, out: Path):
    return import_run(
        EXTERNAL_ROOT / "code_agent_runtime" / document,
        EXTERNAL_SUITE,
        source="code_agent_runtime",
        context=ImportContext(app_version=app_version, run_id=run_id),
        out_root=out,
    )


@pytest.fixture
def imported_pair(tmp_path: Path):
    runs = tmp_path / "runs"
    baseline = _import_side("external-baseline", "ext-baseline", "baseline.json", runs)
    candidate = _import_side(
        "external-candidate", "ext-candidate", "candidate.json", runs
    )
    return baseline, candidate


def test_an_imported_run_writes_the_artifacts_a_native_one_does(imported_pair) -> None:
    baseline, _ = imported_pair
    assert (baseline.run_dir / RUN_JSONL).is_file()
    assert (baseline.run_dir / "run_meta.json").is_file()
    assert baseline.run.meta.adapter == "imported-code_agent_runtime"
    assert baseline.run.meta.records_sha256


def test_the_sidecar_records_what_the_mapping_could_not_carry(imported_pair) -> None:
    baseline, _ = imported_pair
    meta = ImportMeta.from_dict(
        json.loads((baseline.run_dir / IMPORT_META_JSON).read_text("utf-8"))
    )
    assert meta.source == "code_agent_runtime"
    assert meta.suite_id == "external"
    assert meta.strict is True
    assert meta.imported_case_ids == [
        "tu_wrong_tool",
        "tu_control_stable",
        "at_more_retries",
        "at_control_stable",
    ]
    assert "prompt_version" in meta.absent_fields
    assert "tasks[].steps[].text" in meta.ignored_fields
    assert meta.unknown_fields == []


def test_the_comparison_engine_flags_the_imported_defects_and_only_those(
    imported_pair, tmp_path: Path
) -> None:
    """18/18 on native fixtures; 2/2 here, with the same rules and no new code."""
    baseline, candidate = imported_pair
    result = compare_runs(baseline.run_dir, candidate.run_dir, tmp_path / "cmp")

    flagged = {
        record.case_id: [f.category for f in record.regression_flags]
        for record in result.records
        if record.regression_flags
    }
    assert flagged == {
        "tu_wrong_tool": ["tool_call_regression"],
        "at_more_retries": ["error_rate_regression"],
    }
    assert result.summary.regressed_case_count == 2
    assert result.summary.case_count == 4


def test_the_report_generator_renders_an_imported_comparison(
    imported_pair, tmp_path: Path
) -> None:
    baseline, candidate = imported_pair
    comparison = compare_runs(baseline.run_dir, candidate.run_dir, tmp_path / "cmp")
    out = generate_report(
        comparison.out_dir, tmp_path / "report.md", formats=("markdown",)
    )

    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert out.report.flag_count == 2
    assert "tool_call_regression" in text
    assert "error_rate_regression" in text
    assert "imported-code_agent_runtime" in text


def test_importing_the_same_document_twice_is_byte_identical(tmp_path: Path) -> None:
    first = _import_side("external-baseline", "a", "baseline.json", tmp_path / "one")
    second = _import_side("external-baseline", "a", "baseline.json", tmp_path / "two")
    assert (first.run_dir / RUN_JSONL).read_bytes() == (
        second.run_dir / RUN_JSONL
    ).read_bytes()
    assert first.run.meta.records_sha256 == second.run.meta.records_sha256


def test_a_trace_that_does_not_cover_the_suite_refuses(tmp_path: Path) -> None:
    document = _document("code_agent_runtime")
    document["tasks"] = document["tasks"][:2]
    path = tmp_path / "partial.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(TraceImportError, match="covers no record for"):
        import_run(path, EXTERNAL_SUITE, source="code_agent_runtime",
                   context=ImportContext(), out_root=None)


def test_a_trace_with_a_case_the_suite_never_asked_for_refuses(tmp_path: Path) -> None:
    """Read and never scored — an import that looked complete and was not."""
    document = _document("code_agent_runtime")
    extra = copy.deepcopy(document["tasks"][0])
    extra["task_id"] = "sj_invalid_json"
    document["tasks"].append(extra)
    path = tmp_path / "extra.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(TraceImportError, match="does not define"):
        import_run(path, EXTERNAL_SUITE, source="code_agent_runtime",
                   context=ImportContext(), out_root=None)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_the_cli_imports_exports_and_re_imports(tmp_path: Path, capsys) -> None:
    runs = tmp_path / "runs"
    assert cli.main([
        "import-trace",
        "--input", str(EXTERNAL_ROOT / "code_agent_runtime" / "baseline.json"),
        "--suite", str(EXTERNAL_SUITE),
        "--source", "code_agent_runtime",
        "--app-version", "external-baseline",
        "--run-id", "ext-baseline",
        "--out", str(runs),
    ]) == 0
    assert "result           : PASS" in capsys.readouterr().out

    bundle_path = tmp_path / "bundle.json"
    assert cli.main(
        ["export-run", "--run", str(runs / "ext-baseline"), "--out", str(bundle_path)]
    ) == 0
    capsys.readouterr()

    # ``--source auto``: the envelope names itself.
    assert cli.main([
        "import-trace", "--input", str(bundle_path), "--suite", str(EXTERNAL_SUITE),
        "--run-id", "roundtrip", "--out", str(runs),
    ]) == 0
    out = capsys.readouterr().out
    assert "source           : llmreg" in out
    assert f"contract         : {CONTRACT_VERSION} (envelope)" in out


def test_the_cli_fails_cleanly_on_a_missing_document(tmp_path: Path, capsys) -> None:
    code = cli.main([
        "import-trace", "--input", str(tmp_path / "nope.json"),
        "--suite", str(EXTERNAL_SUITE), "--source", "claude_api",
    ])
    assert code == 1
    assert "result           : FAIL" in capsys.readouterr().out


def test_the_cli_export_is_deterministic(tmp_path: Path, capsys) -> None:
    runs = tmp_path / "runs"
    cli.main([
        "import-trace",
        "--input", str(EXTERNAL_ROOT / "code_agent_runtime" / "baseline.json"),
        "--suite", str(EXTERNAL_SUITE), "--source", "code_agent_runtime",
        "--run-id", "r", "--out", str(runs),
    ])
    for name in ("a.json", "b.json"):
        cli.main(
            ["export-run", "--run", str(runs / "r"), "--out", str(tmp_path / name)]
        )
    capsys.readouterr()
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


# --------------------------------------------------------------------------- #
# Hygiene
# --------------------------------------------------------------------------- #

_BANNED_IMPORTS = frozenset(
    {"random", "secrets", "time", "datetime", "socket", "urllib", "http", "requests"}
)

_MILESTONE_10_SOURCES = (
    "src/llm_regression_observability/datasets/versioning.py",
    *(
        f"src/llm_regression_observability/datasets/importers/{name}.py"
        for name in (
            "__init__", "base", "registry", "runs", "native", "code_agent_runtime",
            "bioagentbench", "bioscientist_os", "agent_control_bench", "claude_api",
        )
    ),
)


def _imported_top_level_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return modules


@pytest.mark.parametrize("rel", _MILESTONE_10_SOURCES)
def test_no_clock_randomness_or_network_on_the_import_path(rel: str) -> None:
    reached = _imported_top_level_modules(
        (REPO_ROOT / rel).read_text(encoding="utf-8")
    ) & _BANNED_IMPORTS
    assert not reached, f"{rel} must not import {sorted(reached)}"


def test_the_import_path_names_no_sibling_repository_on_disk() -> None:
    """Contracts are validated against committed examples only. Nothing reaches out."""
    for rel in _MILESTONE_10_SOURCES:
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        for needle in ("~/claude", "/home/", "code-agent-runtime/src", "site-packages"):
            assert needle not in source, f"{rel} references {needle}"


def test_the_contract_documentation_names_every_source() -> None:
    doc = (REPO_ROOT / "docs" / "INTEGRATION_CONTRACTS.md").read_text(encoding="utf-8")
    for source in SOURCES:
        assert source in doc, f"docs/INTEGRATION_CONTRACTS.md never mentions {source}"
    assert EXPECTED_RECORD_SCHEMA in doc
    assert f"`{CONTRACT_VERSION}`" in doc


def test_the_supported_contract_versions_include_the_current_one() -> None:
    assert CONTRACT_VERSION in SUPPORTED_CONTRACT_VERSIONS


def test_import_path_reads_a_document_off_disk() -> None:
    result = import_path(
        EXAMPLES["claude_api"], ImportContext(), source="claude_api"
    )
    assert len(result.records) == 3


def test_the_trace_bundle_is_a_schema_and_validates() -> None:
    bundle = export_bundle(_fixture_records(), exported_at=DEFAULT_TIMESTAMP)
    rebuilt = TraceBundle.from_dict(json.loads(bundle.to_json()))
    assert rebuilt.to_json() == bundle.to_json()
