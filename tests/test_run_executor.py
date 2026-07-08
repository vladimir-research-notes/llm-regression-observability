"""Tests for the run-suite executor (Milestone 4).

CPU-only and offline. They pin the guarantees every later stage depends on:

* the suite loader resolves a manifest's ``suite/case_id`` refs into an ordered
  ``EvalSuite``, and fails loudly on a dangling or duplicated reference;
* ``traces/storage.py`` writes byte-stable JSONL/JSON that round-trips through
  the schemas;
* a run over the recorded adapter produces exactly one ``RunRecord`` per case,
  in suite order, with token / cost / latency fields populated deterministically;
* the executor owns run identity (it re-stamps replayed records) but preserves
  provenance unless explicitly overridden;
* two runs on the same inputs are byte-identical, and byte-identical except for
  ``run_id`` when only the run id differs;
* ``run_meta.json`` is a true summary of ``run.jsonl`` (totals + SHA-256);
* nothing in the new code reads the clock, the RNG, or the network.
"""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest

from llm_regression_observability import cli
from llm_regression_observability.adapters import (
    ADAPTER_NAMES,
    AdapterContext,
    AdapterResponse,
    ApiAdapterStub,
    MockModelAdapter,
    RecordedModelAdapter,
    ScriptedRegressionAdapter,
)
from llm_regression_observability.datasets import (
    load_eval_suite,
    resolve_fixture_roots,
)
from llm_regression_observability.datasets.validation import _behavioral_fields
from llm_regression_observability.executor import (
    DEFAULT_TIMESTAMP,
    RUN_JSONL,
    RUN_META_JSON,
    CostModel,
    ExecutorError,
    RunConfig,
    RunMeta,
    build_adapter,
    default_run_id,
    execute_suite,
    run_suite,
    write_run,
)
from llm_regression_observability.schemas import (
    EvalCase,
    EvalSuite,
    RunRecord,
    SchemaError,
)
from llm_regression_observability.traces.storage import (
    file_sha256,
    read_json,
    read_jsonl,
    render_jsonl,
    sha256_hex,
    write_json,
    write_jsonl,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_SUITE = REPO_ROOT / "evals" / "suites" / "smoke.yaml"
EVALS_ROOT, DATA_ROOT = resolve_fixture_roots(None)

# The eight cases of evals/suites/smoke.yaml, in manifest order.
SMOKE_CASE_IDS = [
    "sj_invalid_json",
    "tu_wrong_tool",
    "re_unsupported_claim",
    "rb_underrefuse",
    "lc_latency_spike",
    "at_more_retries",
    "at_infra_noise",
    "sj_control_reorder",
]


def _config(**overrides) -> RunConfig:
    base = {"suite_path": SMOKE_SUITE, "adapter": "recorded", "app_version": "baseline"}
    base.update(overrides)
    return RunConfig(**base)


def _record(**overrides) -> RunRecord:
    base = {
        "run_id": "r",
        "case_id": "c",
        "app_version": "baseline",
        "model_id": "m",
        "prompt_version": "p",
        "tool_version": "t",
        "timestamp": "2026-01-01T00:00:00Z",
    }
    base.update(overrides)
    return RunRecord(**base)


# --------------------------------------------------------------------------- #
# datasets/loader.py — resolving a manifest into an EvalSuite.
# --------------------------------------------------------------------------- #


def test_load_eval_suite_resolves_manifest_in_order() -> None:
    suite = load_eval_suite(SMOKE_SUITE)
    assert isinstance(suite, EvalSuite)
    assert suite.suite_id == "smoke"
    assert [c.case_id for c in suite.cases] == SMOKE_CASE_IDS
    assert all(isinstance(c, EvalCase) for c in suite.cases)


def test_load_eval_suite_carries_thresholds_and_description() -> None:
    suite = load_eval_suite(SMOKE_SUITE)
    assert suite.description
    assert suite.thresholds["latency_abs_ms"] == 50
    assert suite.thresholds["cost_rel"] == 0.20


def test_load_eval_suite_rejects_dangling_case_reference(tmp_path: Path) -> None:
    manifest = tmp_path / "bad.yaml"
    manifest.write_text(
        "suite_id: bad\ncases:\n  - structured_json/does_not_exist\n", encoding="utf-8"
    )
    with pytest.raises(SchemaError, match="not found in cases.json"):
        load_eval_suite(manifest)


def test_load_eval_suite_rejects_unknown_suite(tmp_path: Path) -> None:
    manifest = tmp_path / "bad.yaml"
    manifest.write_text("suite_id: bad\ncases:\n  - nope/x\n", encoding="utf-8")
    with pytest.raises(SchemaError, match="unknown suite"):
        load_eval_suite(manifest)


def test_load_eval_suite_rejects_duplicate_case(tmp_path: Path) -> None:
    manifest = tmp_path / "dup.yaml"
    manifest.write_text(
        "suite_id: dup\ncases:\n"
        "  - structured_json/sj_invalid_json\n"
        "  - structured_json/sj_invalid_json\n",
        encoding="utf-8",
    )
    with pytest.raises(SchemaError, match="duplicate case"):
        load_eval_suite(manifest)


# --------------------------------------------------------------------------- #
# traces/storage.py — byte-stable persistence.
# --------------------------------------------------------------------------- #


def test_write_jsonl_is_sorted_compact_and_newline_terminated(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "a" / "run.jsonl", [_record(case_id="c1")])
    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    assert b", " not in raw and b'": ' not in raw  # compact separators
    keys = list(json.loads(raw.decode()).keys())
    assert keys == sorted(keys)


def test_write_jsonl_round_trips_through_schemas(tmp_path: Path) -> None:
    records = [_record(case_id="c1", parsed_output={"k": 1}), _record(case_id="c2")]
    path = write_jsonl(tmp_path / "run.jsonl", records)
    loaded = read_jsonl(path, RunRecord)
    assert [r.to_json() for r in loaded] == [r.to_json() for r in records]


def test_write_jsonl_is_byte_stable_across_calls(tmp_path: Path) -> None:
    records = [_record(case_id="c1")]
    a = write_jsonl(tmp_path / "a.jsonl", records).read_bytes()
    b = write_jsonl(tmp_path / "b.jsonl", records).read_bytes()
    assert a == b
    assert sha256_hex(render_jsonl(records)) == file_sha256(tmp_path / "a.jsonl")


def test_read_jsonl_reports_line_number_on_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    path.write_text(_record().to_json() + "\nnot json\n", encoding="utf-8")
    with pytest.raises(SchemaError, match=r":2: invalid JSON"):
        read_jsonl(path, RunRecord)


def test_read_jsonl_reports_line_number_on_schema_violation(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    path.write_text('{"case_id": "c"}\n', encoding="utf-8")
    with pytest.raises(SchemaError, match=r":1: .*missing required field"):
        read_jsonl(path, RunRecord)


def test_read_jsonl_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    path.write_text(_record().to_json() + "\n\n", encoding="utf-8")
    assert len(read_jsonl(path, RunRecord)) == 1


def test_write_json_validates_on_read_back(tmp_path: Path) -> None:
    meta = RunMeta(
        run_id="r", suite_id="s", suite_path="p", adapter="mock",
        app_version="baseline", model_id="m", prompt_version="p",
        tool_version="t", timestamp=DEFAULT_TIMESTAMP,
    )
    path = write_json(tmp_path / "run_meta.json", meta)
    assert read_json(path, RunMeta).to_json() == meta.to_json()


# --------------------------------------------------------------------------- #
# Adapter selection.
# --------------------------------------------------------------------------- #


def test_build_adapter_returns_each_named_adapter() -> None:
    assert isinstance(build_adapter("recorded"), RecordedModelAdapter)
    assert isinstance(build_adapter("scripted-regression"), ScriptedRegressionAdapter)
    assert isinstance(build_adapter("mock"), MockModelAdapter)
    assert isinstance(build_adapter("api-stub"), ApiAdapterStub)


def test_build_adapter_rejects_unknown_name() -> None:
    with pytest.raises(ExecutorError, match="unknown adapter"):
        build_adapter("gpt-hallucinated")


def test_cli_adapter_choices_match_the_adapter_registry() -> None:
    # cli.py mirrors these constants to stay import-light; guard against drift.
    assert set(cli.ADAPTER_CHOICES) == set(ADAPTER_NAMES)
    assert cli.DEFAULT_COST_INPUT_PER_1K == CostModel().input_usd_per_1k
    assert cli.DEFAULT_COST_OUTPUT_PER_1K == CostModel().output_usd_per_1k


def test_default_run_id_is_deterministic_and_safe() -> None:
    assert default_run_id("smoke", "baseline", "recorded") == "smoke-baseline-recorded"


# --------------------------------------------------------------------------- #
# Executing the smoke suite over the recorded adapter.
# --------------------------------------------------------------------------- #


def test_run_produces_one_record_per_case_in_suite_order() -> None:
    result = run_suite(_config(), out_root=None)
    assert [r.case_id for r in result.records] == SMOKE_CASE_IDS
    assert result.meta.case_count == len(SMOKE_CASE_IDS)
    assert result.run_dir is None  # out_root=None writes nothing


def test_token_cost_latency_fields_are_populated_deterministically() -> None:
    a = run_suite(_config(), out_root=None)
    b = run_suite(_config(), out_root=None)
    assert [r.to_json() for r in a.records] == [r.to_json() for r in b.records]
    for record in a.records:
        assert record.input_tokens_estimate > 0
        assert record.output_tokens_estimate > 0
        assert record.latency_ms > 0.0
        assert record.cost_estimate_usd > 0.0


def test_executor_restamps_run_identity_on_replayed_records() -> None:
    # The recorded adapter replays fixtures verbatim, including their own
    # per-case run_id ("<case>-baseline"). The executor owns run identity.
    result = run_suite(_config(run_id="my-run"), out_root=None)
    assert {r.run_id for r in result.records} == {"my-run"}
    assert {r.app_version for r in result.records} == {"baseline"}


def test_executor_preserves_provenance_unless_overridden() -> None:
    preserved = run_suite(_config(), out_root=None)
    assert preserved.meta.model_id == "recorded-mock-v1"
    assert preserved.meta.timestamp == "2026-06-01T12:00:00Z"
    assert preserved.meta.identity_overrides == []

    overridden = run_suite(
        _config(model_id="m-2", timestamp="2026-07-01T00:00:00Z"), out_root=None
    )
    assert {r.model_id for r in overridden.records} == {"m-2"}
    assert {r.timestamp for r in overridden.records} == {"2026-07-01T00:00:00Z"}
    assert overridden.meta.identity_overrides == ["model_id", "timestamp"]


def test_scripted_adapter_names_itself_rather_than_a_model() -> None:
    result = run_suite(
        _config(adapter="scripted-regression", app_version="candidate"), out_root=None
    )
    assert result.meta.model_id == "scripted-regression-adapter"


def test_run_id_must_be_a_safe_directory_name() -> None:
    with pytest.raises(ExecutorError, match="not a safe directory name"):
        run_suite(_config(run_id="../../etc/passwd"), out_root=None)
    with pytest.raises(ExecutorError, match="not a safe directory name"):
        run_suite(_config(run_id=""), out_root=None)


# --------------------------------------------------------------------------- #
# Token / cost estimation.
# --------------------------------------------------------------------------- #


def _bare_suite() -> EvalSuite:
    case = EvalCase(
        case_id="c1",
        version="1",
        prompt="a" * 40,  # 10 tokens under the ~4-chars/token estimator
        risk_category="benign",
    )
    return EvalSuite(suite_id="bare", cases=[case])


def test_executor_estimates_tokens_and_cost_when_adapter_supplies_none() -> None:
    adapter = MockModelAdapter(responses={"c1": AdapterResponse(final_output="b" * 20)})
    config = _config(adapter="mock", run_id="bare")
    result = execute_suite(_bare_suite(), adapter, config)

    record = result.records[0]
    assert record.input_tokens_estimate == 10  # 40 chars / 4
    assert record.output_tokens_estimate == 5  # 20 chars / 4
    assert record.cost_estimate_usd == pytest.approx(
        CostModel().estimate(10, 5), abs=1e-12
    )
    assert result.meta.tokens_estimated_cases == 1
    assert result.meta.cost_estimated_cases == 1


def test_executor_does_not_overwrite_recorded_tokens_or_cost() -> None:
    result = run_suite(_config(), out_root=None)
    assert result.meta.tokens_estimated_cases == 0
    assert result.meta.cost_estimated_cases == 0
    # Fixture values survive untouched.
    first = next(r for r in result.records if r.case_id == "sj_invalid_json")
    assert first.input_tokens_estimate == 120
    assert first.cost_estimate_usd == pytest.approx(0.0015)


def test_recompute_cost_prices_every_record_with_the_cost_model() -> None:
    model = CostModel(input_usd_per_1k=1.0, output_usd_per_1k=2.0)
    result = run_suite(_config(cost_model=model, recompute_cost=True), out_root=None)
    assert result.meta.cost_estimated_cases == len(result.records)
    for record in result.records:
        expected = model.estimate(
            record.input_tokens_estimate, record.output_tokens_estimate
        )
        assert record.cost_estimate_usd == pytest.approx(expected, abs=1e-12)


def test_input_token_estimate_includes_payload_and_retrieval_docs() -> None:
    # re_unsupported_claim carries retrieval_docs; a prompt-only estimate would
    # be strictly smaller than one that bills the provided context.
    suite = load_eval_suite(SMOKE_SUITE)
    case = next(c for c in suite.cases if c.case_id == "re_unsupported_claim")
    assert case.retrieval_docs

    adapter = MockModelAdapter(responses={case.case_id: AdapterResponse()})
    config = _config(adapter="mock", run_id="ctx")
    with_docs = execute_suite(EvalSuite(suite_id="s", cases=[case]), adapter, config)

    from llm_regression_observability.adapters import estimate_tokens

    assert with_docs.records[0].input_tokens_estimate > estimate_tokens(case.prompt)


def test_cost_model_rounds_for_byte_stability() -> None:
    model = CostModel(input_usd_per_1k=0.0000000001, output_usd_per_1k=0.0)
    assert model.estimate(1, 0) == 0.0  # below the 8-decimal floor, not 1e-13


# --------------------------------------------------------------------------- #
# RunMeta edge cases: an empty run, and a run with non-constant provenance.
# --------------------------------------------------------------------------- #


def test_empty_suite_produces_an_empty_but_valid_run(tmp_path: Path) -> None:
    empty = EvalSuite(suite_id="empty")
    result = execute_suite(empty, MockModelAdapter(), _config(run_id="empty"))
    assert result.records == []
    assert result.meta.case_count == 0
    assert result.meta.cost_total_usd == 0.0
    assert result.meta.records_sha256 == sha256_hex("")
    # The artifact still round-trips.
    write_run(result, tmp_path)
    assert read_jsonl(result.run_path, RunRecord) == []
    assert read_json(result.meta_path, RunMeta).case_count == 0


class _MixedProvenanceAdapter(MockModelAdapter):
    """Emits a different ``model_id`` per case, which no shipped adapter does."""

    def generate(self, case: EvalCase, context: AdapterContext) -> RunRecord:
        record = super().generate(case, context)
        return dataclasses.replace(record, model_id=f"model-{case.case_id}")


def test_run_meta_reports_mixed_when_provenance_is_not_constant() -> None:
    suite = EvalSuite(
        suite_id="mixed",
        cases=[
            EvalCase(case_id="c1", version="1", prompt="p", risk_category="benign"),
            EvalCase(case_id="c2", version="1", prompt="p", risk_category="benign"),
        ],
    )
    result = execute_suite(suite, _MixedProvenanceAdapter(), _config(run_id="mixed"))
    assert {r.model_id for r in result.records} == {"model-c1", "model-c2"}
    assert result.meta.model_id == RunMeta.MIXED
    # A field that *is* constant still reports its value.
    assert result.meta.timestamp == DEFAULT_TIMESTAMP


def test_explicit_model_id_override_beats_mixed_provenance() -> None:
    suite = EvalSuite(
        suite_id="mixed",
        cases=[
            EvalCase(case_id="c1", version="1", prompt="p", risk_category="benign"),
            EvalCase(case_id="c2", version="1", prompt="p", risk_category="benign"),
        ],
    )
    config = _config(run_id="mixed", model_id="forced")
    result = execute_suite(suite, _MixedProvenanceAdapter(), config)
    assert result.meta.model_id == "forced"


# --------------------------------------------------------------------------- #
# Artifacts on disk.
# --------------------------------------------------------------------------- #


def test_run_writes_run_jsonl_and_run_meta(tmp_path: Path) -> None:
    result = run_suite(_config(), out_root=tmp_path)
    assert result.run_dir == tmp_path / "smoke-baseline-recorded"
    assert result.run_path.is_file()
    assert result.meta_path.is_file()
    assert result.run_path.name == RUN_JSONL
    assert result.meta_path.name == RUN_META_JSON


def test_written_run_jsonl_round_trips_through_the_schemas(tmp_path: Path) -> None:
    result = run_suite(_config(), out_root=tmp_path)
    loaded = read_jsonl(result.run_path, RunRecord)
    assert [r.to_json() for r in loaded] == [r.to_json() for r in result.records]


def test_run_meta_is_a_true_summary_of_run_jsonl(tmp_path: Path) -> None:
    result = run_suite(_config(), out_root=tmp_path)
    meta = read_json(result.meta_path, RunMeta)
    records = read_jsonl(result.run_path, RunRecord)

    assert meta.records_sha256 == file_sha256(result.run_path)
    assert meta.case_count == len(records)
    assert meta.input_tokens_total == sum(r.input_tokens_estimate for r in records)
    assert meta.output_tokens_total == sum(r.output_tokens_estimate for r in records)
    assert meta.cost_total_usd == pytest.approx(
        sum(r.cost_estimate_usd for r in records)
    )
    assert meta.latency_ms_total == pytest.approx(sum(r.latency_ms for r in records))
    assert meta.tool_call_count == sum(len(r.tool_calls) for r in records)
    assert meta.retrieval_source_count == sum(len(r.retrieval_sources) for r in records)
    assert meta.error_count == sum(len(r.errors) for r in records)
    assert meta.suite_path == "evals/suites/smoke.yaml"  # repo-relative, no home dir
    assert meta.cost_model.to_dict() == CostModel().to_dict()


def test_two_runs_on_the_same_inputs_are_byte_identical(tmp_path: Path) -> None:
    first = run_suite(_config(), out_root=tmp_path / "one")
    second = run_suite(_config(), out_root=tmp_path / "two")
    assert first.run_path.read_bytes() == second.run_path.read_bytes()
    assert first.meta_path.read_bytes() == second.meta_path.read_bytes()


def test_two_runs_differ_only_in_run_id(tmp_path: Path) -> None:
    first = run_suite(_config(run_id="alpha"), out_root=tmp_path)
    second = run_suite(_config(run_id="beta"), out_root=tmp_path)

    def _without_run_id(records: list[RunRecord]) -> list[dict]:
        return [
            {k: v for k, v in r.to_dict().items() if k != "run_id"} for r in records
        ]

    assert _without_run_id(first.records) == _without_run_id(second.records)
    # The run id is the *only* difference, so the digests must not match.
    assert first.meta.records_sha256 != second.meta.records_sha256


def test_rerunning_into_an_existing_directory_is_idempotent(tmp_path: Path) -> None:
    first = run_suite(_config(), out_root=tmp_path)
    before = first.run_path.read_bytes()
    second = run_suite(_config(), out_root=tmp_path)
    assert second.run_path.read_bytes() == before


# --------------------------------------------------------------------------- #
# Baseline vs candidate — the offline smoke pipeline.
# --------------------------------------------------------------------------- #


def test_scripted_candidate_run_differs_from_recorded_baseline_run() -> None:
    baseline = run_suite(_config(), out_root=None)
    candidate = run_suite(
        _config(adapter="scripted-regression", app_version="candidate"), out_root=None
    )
    base_by_id = {r.case_id: r for r in baseline.records}
    cand_by_id = {r.case_id: r for r in candidate.records}
    assert set(base_by_id) == set(cand_by_id)

    # sj_control_reorder is a true control: no injection, so behavior is identical.
    control = "sj_control_reorder"
    assert _behavioral_fields(base_by_id[control]) == _behavioral_fields(
        cand_by_id[control]
    )
    for case_id in set(base_by_id) - {control}:
        assert _behavioral_fields(base_by_id[case_id]) != _behavioral_fields(
            cand_by_id[case_id]
        ), case_id


def test_candidate_run_surfaces_infra_error_without_app_error() -> None:
    candidate = run_suite(
        _config(adapter="scripted-regression", app_version="candidate"), out_root=None
    )
    infra = next(r for r in candidate.records if r.case_id == "at_infra_noise")
    assert [e.origin for e in infra.errors] == ["infra"]
    assert candidate.meta.infra_error_count >= 1


# --------------------------------------------------------------------------- #
# Failure handling.
# --------------------------------------------------------------------------- #


def _unknown_case_suite() -> EvalSuite:
    return EvalSuite(
        suite_id="unknown",
        cases=[
            EvalCase(
                case_id="not_in_fixtures",
                version="1",
                prompt="p",
                risk_category="benign",
            )
        ],
    )


def test_strict_mode_aborts_on_adapter_failure() -> None:
    adapter = RecordedModelAdapter.from_fixtures()
    with pytest.raises(ExecutorError, match="failed on case 'not_in_fixtures'"):
        execute_suite(_unknown_case_suite(), adapter, _config(run_id="x"))


def test_continue_on_error_records_an_infra_error() -> None:
    adapter = RecordedModelAdapter.from_fixtures()
    result = execute_suite(
        _unknown_case_suite(), adapter, _config(run_id="x", strict=False)
    )
    record = result.records[0]
    assert [e.kind for e in record.errors] == ["adapter_error"]
    assert [e.origin for e in record.errors] == ["infra"]
    assert result.meta.app_error_count == 0
    assert result.meta.infra_error_count == 1


def test_api_stub_run_is_refused_before_any_network_call() -> None:
    # Selecting api-stub is allowed; generating is not. No paid API, ever.
    with pytest.raises(ExecutorError, match="disabled by default"):
        run_suite(_config(adapter="api-stub"), out_root=None)


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #


def test_cli_run_suite_writes_artifacts(tmp_path: Path, capsys) -> None:
    code = cli.main(
        [
            "run-suite",
            "--suite", str(SMOKE_SUITE),
            "--adapter", "recorded",
            "--app-version", "baseline",
            "--out", str(tmp_path),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "result           : PASS" in out
    run_dir = tmp_path / "smoke-baseline-recorded"
    assert (run_dir / RUN_JSONL).is_file()
    assert (run_dir / RUN_META_JSON).is_file()


def test_cli_run_suite_dry_run_writes_nothing(tmp_path: Path, capsys) -> None:
    code = cli.main(
        ["run-suite", "--suite", str(SMOKE_SUITE), "--out", str(tmp_path), "--dry-run"]
    )
    assert code == 0
    assert "none written (--dry-run)" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_cli_run_suite_fails_cleanly_without_traceback(tmp_path: Path, capsys) -> None:
    code = cli.main(
        ["run-suite", "--suite", str(SMOKE_SUITE), "--out", str(tmp_path),
         "--run-id", "../escape"]
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "result           : FAIL" in out
    assert "not a safe directory name" in out


def test_cli_run_suite_rejects_unknown_adapter() -> None:
    with pytest.raises(SystemExit):
        cli.main(["run-suite", "--suite", str(SMOKE_SUITE), "--adapter", "nope"])


# --------------------------------------------------------------------------- #
# Determinism: the new code may not reach for the clock, the RNG, or the network.
# --------------------------------------------------------------------------- #

# Any of these, imported at all, would put an ambient value or a network call
# within reach of a scored path. Checked against the parsed AST rather than the
# raw text, so prose *about* ``datetime.now()`` does not trip the test.
_BANNED_IMPORTS = frozenset(
    {"random", "secrets", "time", "datetime", "socket", "urllib", "http", "requests"}
)

_MILESTONE_4_SOURCES = (
    "src/llm_regression_observability/executor.py",
    "src/llm_regression_observability/traces/storage.py",
    "src/llm_regression_observability/traces/__init__.py",
    "src/llm_regression_observability/datasets/loader.py",
    "src/llm_regression_observability/cli.py",
)


def _imported_top_level_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return modules


@pytest.mark.parametrize("rel", _MILESTONE_4_SOURCES)
def test_no_ambient_time_randomness_or_network_in_scored_paths(rel: str) -> None:
    source = (REPO_ROOT / rel).read_text(encoding="utf-8")
    reached = _imported_top_level_modules(source) & _BANNED_IMPORTS
    assert not reached, f"{rel} must not import {sorted(reached)}"


def test_adapter_context_timestamp_is_an_input_not_the_clock() -> None:
    assert AdapterContext(app_version="baseline").timestamp == DEFAULT_TIMESTAMP
