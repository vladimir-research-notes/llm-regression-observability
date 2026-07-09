"""Tests for redaction and safety hygiene (Milestone 9).

CPU-only and offline. ``docs/SAFETY.md`` makes one promise — no credential-like or
PII-like string is ever written to a trace, a span, or a report — and this file is
where that promise is either kept or caught lying. It is organized around the ways
a redactor fails.

**It could miss a pattern.** Every rule in the table is driven by a sample built at
runtime (never a literal, so no scanner finds a key in this file). Each sample must
mask, and the masked text must be clean on re-scan.

**It could mask the wrong thing.** Prose, SHA-256 digests, UUIDs, run ids, ISO
timestamps and this project's own metric names go through unchanged — and every one
of the 52 committed fixture records comes out byte-identical, which is the test that
would fail first if a future rule started rewriting scored text.

**It could run too late.** The executor redacts *before* it accounts tokens and
before it hashes ``run.jsonl``, so ``records_sha256`` digests the bytes on disk. The
report generator masks before it renders. The span writer refuses before it writes.
Each is asserted against the artifact, not against the function.

**It could rewrite a join key.** ``run_id``, ``case_id``, ``model_id``, a tool name,
a source id: the redactor leaves them alone and reports ``flagged``, and the executor
refuses the run. A masked case id is a corrupted artifact wearing a safety badge.

**It could leak while complaining.** No error message, finding, or scan result may
contain the value it found.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from llm_regression_observability import cli, reports
from llm_regression_observability.adapters import (
    AdapterContext,
    MockModelAdapter,
    estimate_tokens,
)
from llm_regression_observability.datasets import repo_root
from llm_regression_observability.datasets import validation as datasets_validation
from llm_regression_observability.datasets.fixtures import (
    SUITE_NAMES,
    resolve_fixture_roots,
)
from llm_regression_observability.executor import (
    RUN_JSONL,
    ExecutorError,
    RunConfig,
    RunMeta,
    execute_suite,
    run_suite,
    write_run,
)
from llm_regression_observability.observability.spans import (
    TIMING_DERIVED,
    TIMING_RECORDED,
    Span,
    validate_spans,
    write_spans,
)
from llm_regression_observability.reports import base as reports_base
from llm_regression_observability.schemas import (
    ErrorRecord,
    EvalCase,
    EvalSuite,
    RetrievalTrace,
    RunRecord,
    ToolCallTrace,
)
from llm_regression_observability.traces import redaction
from llm_regression_observability.traces.redaction import (
    ALLOW_MARKER,
    CERTAIN_RULES,
    CLEAN,
    CONTENT_FIELDS,
    FLAGGED,
    IDENTITY_FIELDS,
    MASK,
    MIN_ENTROPY_BITS,
    NON_TEXT_FIELDS,
    REDACTED,
    RULES,
    SECRET_PATTERNS,
    TIER_CERTAIN,
    TIER_HEURISTIC,
    RedactionError,
    assert_clean,
    identity_findings,
    redact_record,
    redact_text,
    redact_value,
    scan_for_secrets,
    scan_text,
    scan_tree,
    secret_findings,
    shannon_entropy,
)
from llm_regression_observability.traces.storage import file_sha256, read_jsonl

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_SUITE = REPO_ROOT / "evals" / "suites" / "smoke.yaml"
EVALS_ROOT, DATA_ROOT = resolve_fixture_roots(None)

# Every credential in this file is assembled at runtime. A test that proves the
# scanner works must not hand the scanner a reason to flag the test.
FAKE_KEY = "sk-" + "A" * 24
FAKE_BEARER = "Bearer " + "b" * 24

# Assembled from parts so no line of this file is itself a JWT for `scan_tree`.
_JWT = ".".join(("eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiIxMjM0NTY3ODkwIn0", "dozjgNryP4J3"))
_AWS = "AKIA" + "IOSFODNN7EXAMPLE"
_ENTROPY = "Zm9vYmFyMTIzNDU2Nzg5MEFCQ0RFRg1x"

#: One sample per rule: the text the redactor sees, and the exact substring that must
#: not survive it. Kept exhaustive by ``test_every_rule_has_a_sample`` — a new rule
#: without a sample fails the suite rather than shipping untested.
SAMPLES: dict[str, tuple[str, str]] = {
    "private_key": ("-----BEGIN " + "RSA PRIVATE KEY" + "-----", "PRIVATE KEY"),
    "openai_key": (f"key: {FAKE_KEY}", FAKE_KEY),
    "aws_access_key": ("id=" + _AWS, _AWS),
    "github_token": ("ghp_" + "b" * 30, "ghp_" + "b" * 30),
    "google_api_key": ("AIza" + "C" * 30, "AIza" + "C" * 30),
    "slack_token": ("xoxb-" + "1" * 12, "xoxb-" + "1" * 12),
    "jwt": (_JWT, _JWT),
    "authorization_header": ("Authorization: " + FAKE_BEARER, "b" * 24),
    "bearer_token": (f"auth: {FAKE_BEARER}", "b" * 24),
    "secret_assignment": ('api_key="hunter2secret"', "hunter2secret"),
    "email": ("reach alice.smith@example.com for access", "alice.smith@example.com"),
    "high_entropy_token": (_ENTROPY, _ENTROPY),
}


def _text(name: str) -> str:
    return SAMPLES[name][0]


def _value(name: str) -> str:
    return SAMPLES[name][1]

#: Text that must survive redaction untouched. Each entry is a false positive some
#: plausible rule would produce; several are strings this repository itself writes.
NON_SECRETS: tuple[str, ...] = (
    "The candidate raised tool_call_regression on case tu_wrong_tool.",
    # A SHA-256 digest: every run_meta.json holds one. Lowercase hex, so the
    # mixed-class filter rejects it before entropy is even measured.
    "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "smoke-baseline-recorded vs smoke-candidate-scripted-regression",
    "1970-01-01T00:00:00Z",
    "latency moved +250.0 ms; cost +$0.001200 (estimate, not a bill)",
    "123e4567-e89b-12d3-a456-426614174000",
    "token/cost estimator with a hard max-cost guard",
    "input_tokens_estimate: 42",
    "refusal behavior no longer matches the case's expectation (under_refusal)",
)


def _record(**overrides) -> RunRecord:
    base = {
        "run_id": "r",
        "case_id": "c",
        "app_version": "baseline",
        "model_id": "m",
        "prompt_version": "p",
        "tool_version": "t",
        "timestamp": "1970-01-01T00:00:00Z",
    }
    base.update(overrides)
    return RunRecord(**base)


def _suite(*cases: EvalCase) -> EvalSuite:
    return EvalSuite(suite_id="redaction", cases=list(cases))


def _case(case_id: str = "c1") -> EvalCase:
    return EvalCase(case_id=case_id, version="1", prompt="p", risk_category="benign")


class _LeakyAdapter(MockModelAdapter):
    """Emits a record whose ``final_output`` carries a credential.

    ``output_tokens_estimate`` is left at zero so the executor derives it, which is
    what ``test_token_estimates_describe_the_text_that_is_persisted`` needs in order
    to observe *when* redaction ran relative to accounting.
    """

    def generate(self, case: EvalCase, context: AdapterContext) -> RunRecord:
        record = super().generate(case, context)
        return dataclasses.replace(
            record, final_output=f"here you go: {FAKE_KEY}", output_tokens_estimate=0
        )


class _FlaggedAdapter(MockModelAdapter):
    """Emits a record whose ``model_id`` — a join key — carries a credential."""

    def generate(self, case: EvalCase, context: AdapterContext) -> RunRecord:
        record = super().generate(case, context)
        return dataclasses.replace(record, model_id=f"model-{FAKE_KEY}")


# --------------------------------------------------------------------------- #
# The rule table
# --------------------------------------------------------------------------- #


def test_every_rule_has_a_sample() -> None:
    assert {rule.name for rule in RULES} == set(SAMPLES)


def test_every_rule_names_a_known_tier_and_a_reason() -> None:
    for rule in RULES:
        assert rule.tier in (TIER_CERTAIN, TIER_HEURISTIC), rule.name
        assert rule.why, f"{rule.name} does not say what it is"


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_every_known_secret_pattern_is_masked(name: str) -> None:
    """The milestone's first assertion: every pattern the table knows is masked."""
    text, secret = SAMPLES[name]
    masked = redact_text(text)
    assert masked != text, f"{name} was not masked"
    assert MASK.format(rule=name) in masked, f"{name} was masked by another rule"
    assert secret not in masked, f"{name} left its value behind"


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_masked_text_never_matches_a_certain_rule_again(name: str) -> None:
    """The post-condition the placeholder exists to satisfy.

    A replacement that still matched its own pattern would either loop or leak. The
    placeholder carries ``[`` and ``:``, which appear in no rule's character class.
    """
    masked = redact_text(_text(name))
    assert scan_text(masked) == ()
    assert redact_text(masked) == masked  # idempotent


@pytest.mark.parametrize("text", NON_SECRETS)
def test_non_secret_text_is_left_untouched(text: str) -> None:
    """The milestone's third assertion. Each entry is a real false-positive risk."""
    assert redact_text(text) == text
    assert scan_text(text) == ()


def test_certain_rules_are_the_ones_the_gate_refuses_on() -> None:
    """Heuristic rules mask; they never block. A suspicion must not deny a report."""
    certain = {rule.name for rule in CERTAIN_RULES}
    heuristic = {rule.name for rule in RULES if not rule.certain}
    assert certain.isdisjoint(heuristic)
    assert heuristic == {"secret_assignment", "email", "high_entropy_token"}

    for name in heuristic:
        text = _text(name)
        assert scan_text(text) == (), f"{name} would refuse an artifact"
        assert redact_text(text) != text, f"{name} did not mask"
        assert_clean(text, source="heuristic-only")  # does not raise

    for name in certain:
        with pytest.raises(RedactionError, match=name):
            assert_clean(_text(name), source="certain")


def test_an_authorization_header_keeps_its_name_and_loses_its_credential() -> None:
    """Masking the whole header would leave a trace nobody can debug."""
    masked = redact_text(_text("authorization_header"))
    assert masked.startswith("Authorization: Bearer ")
    assert "b" * 24 not in masked
    assert masked.endswith(MASK.format(rule="authorization_header"))


def test_a_specific_rule_masks_before_a_general_one() -> None:
    """Rule order is load bearing: the reader should learn *which* provider leaked."""
    masked = redact_text(f"api_key={FAKE_KEY}")
    assert MASK.format(rule="openai_key") in masked
    assert MASK.format(rule="secret_assignment") not in masked


def test_the_entropy_rule_requires_mixed_character_classes() -> None:
    digest = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    assert shannon_entropy(digest) >= MIN_ENTROPY_BITS  # entropy alone would flag it
    assert redact_text(digest) == digest  # ...the class filter is what saves it
    assert redact_text("a" * 40) == "a" * 40  # long, low entropy
    assert redact_text("Ab1") == "Ab1"  # mixed, too short


def test_shannon_entropy_is_zero_for_one_symbol_and_one_bit_for_two() -> None:
    assert shannon_entropy("") == 0.0
    assert shannon_entropy("aaaa") == 0.0
    assert shannon_entropy("abab") == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Findings never echo the value
# --------------------------------------------------------------------------- #


def test_findings_locate_a_credential_without_echoing_it() -> None:
    text = f"line one\nkey: {FAKE_KEY}\nline three\nauth: {FAKE_BEARER}\n"
    assert secret_findings(text) == ((2, "openai_key"), (4, "bearer_token"))
    assert secret_findings("nothing credential-like here") == ()


def test_the_refusal_names_the_pattern_and_the_line_but_never_the_value() -> None:
    with pytest.raises(RedactionError) as excinfo:
        assert_clean(f"a\nb {FAKE_KEY}\n", source="spans.jsonl")
    message = str(excinfo.value)
    assert "line 2 (openai_key)" in message
    assert "spans.jsonl" in message
    assert FAKE_KEY not in message


def test_scan_for_secrets_reports_names_only() -> None:
    assert scan_for_secrets(_text("openai_key")) == ["openai_key"]
    assert scan_for_secrets("plain synthetic prose") == []


# --------------------------------------------------------------------------- #
# One table, every caller
# --------------------------------------------------------------------------- #


def test_the_pattern_table_has_exactly_one_definition() -> None:
    """Milestone 7 promised the table would move here and both callers follow it.

    A second copy is a false sense of safety: the copy that misses a pattern is the
    one that runs. Identity, not equality, is the assertion.
    """
    assert datasets_validation.SECRET_PATTERNS is SECRET_PATTERNS
    assert reports_base.secret_findings is secret_findings
    assert datasets_validation.scan_for_secrets is scan_for_secrets
    assert SECRET_PATTERNS == tuple((r.name, r.pattern) for r in CERTAIN_RULES)


def test_the_report_generator_masks_by_default_and_can_be_asked_not_to() -> None:
    assert reports.masking_redactor is redact_text
    assert reports.identity_redactor("x " + FAKE_KEY) == "x " + FAKE_KEY


# --------------------------------------------------------------------------- #
# Values and records
# --------------------------------------------------------------------------- #


def test_redact_value_walks_lists_dicts_and_keys() -> None:
    """A key inside model output is text the model chose, not schema."""
    value = {
        "note": f"use {FAKE_KEY}",
        FAKE_KEY: "as a key",
        "nested": [{"deep": _text("email")}, 3, None, True],
    }
    out = redact_value(value)
    assert out["note"] == "use " + MASK.format(rule="openai_key")
    assert MASK.format(rule="openai_key") in out  # the key was masked too
    assert FAKE_KEY not in str(out)
    assert MASK.format(rule="email") in out["nested"][0]["deep"]
    assert out["nested"][1:] == [3, None, True]  # non-strings pass through


def test_every_run_record_field_is_classified() -> None:
    """Adding a field to `RunRecord` must force a decision, not a default.

    The redactor rewrites content, refuses on identity, and ignores numbers. A field
    in none of the three sets is a field nobody classified — and an unclassified
    string field is how a secret reaches disk.
    """
    classified = set(IDENTITY_FIELDS) | set(CONTENT_FIELDS) | set(NON_TEXT_FIELDS)
    declared = {f.name for f in dataclasses.fields(RunRecord)}
    assert classified == declared, declared ^ classified
    # And the three are disjoint: no field is both a join key and rewritable.
    assert len(IDENTITY_FIELDS) + len(CONTENT_FIELDS) + len(NON_TEXT_FIELDS) == len(
        declared
    )


def test_a_clean_record_is_reported_clean_and_unchanged() -> None:
    record = _record(final_output="a perfectly ordinary answer")
    outcome = redact_record(record)
    assert outcome.status == CLEAN
    assert outcome.masked == ()
    assert outcome.record.to_json() == dataclasses.replace(
        record, redaction_status=CLEAN
    ).to_json()


def test_a_record_is_masked_across_every_content_field() -> None:
    record = _record(
        final_output=f"answer {FAKE_KEY}",
        parsed_output={"k": _text("email")},
        tool_calls=[
            ToolCallTrace(
                name="search",
                arguments={"q": f"{FAKE_BEARER}"},
                error=_text("github_token"),
                status="failed",
            )
        ],
        retrieval_sources=[
            RetrievalTrace(source_id="doc-1", snippet=_text("jwt"), claim=FAKE_KEY)
        ],
        errors=[ErrorRecord(kind="tool_error", message=f"failed with {FAKE_KEY}")],
    )
    outcome = redact_record(record)
    assert outcome.status == REDACTED
    assert set(outcome.masked) >= {"openai_key", "email", "bearer_token", "jwt"}

    blob = outcome.record.to_json()
    assert FAKE_KEY not in blob
    assert secret_findings(blob) == ()
    # Symbols the pipeline joins on are untouched.
    assert outcome.record.tool_calls[0].name == "search"
    assert outcome.record.tool_calls[0].status == "failed"
    assert outcome.record.retrieval_sources[0].source_id == "doc-1"
    assert outcome.record.errors[0].kind == "tool_error"


def test_redacting_a_record_twice_changes_nothing() -> None:
    """Idempotent in bytes *and* in status — the second pass finds nothing to mask,
    and must not conclude from that that the record was never masked."""
    once = redact_record(_record(final_output=f"x {FAKE_KEY}")).record
    twice = redact_record(once).record
    assert once.redaction_status == REDACTED
    assert twice.to_json() == once.to_json()


def test_a_claim_of_clean_over_a_live_secret_is_overruled() -> None:
    """The dangerous lie. The scan is the authority, never the adapter."""
    lying = _record(final_output=f"secret {FAKE_KEY}", redaction_status=CLEAN)
    outcome = redact_record(lying)
    assert outcome.status == REDACTED
    assert FAKE_KEY not in outcome.record.final_output


def test_a_prior_redaction_is_never_downgraded_to_clean() -> None:
    """The conservative lie, preserved. ``clean < redacted < flagged``, take the max.

    A record whose text is already full of placeholders scans clean on a second pass.
    Concluding ``clean`` would erase the one fact ``redaction_status`` exists to
    record, and would make the field disagree with the text beside it.
    """
    already = _record(final_output="x [REDACTED:openai_key]", redaction_status=REDACTED)
    assert redact_record(already).status == REDACTED

    arrived_flagged = _record(final_output="ordinary", redaction_status=FLAGGED)
    outcome = redact_record(arrived_flagged)
    assert outcome.status == FLAGGED
    assert outcome.identity == ()  # nothing *this* pass found
    assert "arrived with" in outcome.describe()


def test_a_secret_in_a_join_key_is_flagged_and_never_rewritten() -> None:
    record = _record(model_id=f"model-{FAKE_KEY}")
    outcome = redact_record(record)
    assert outcome.status == FLAGGED
    assert outcome.flagged
    assert outcome.identity == (("model_id", "openai_key"),)
    # The key is left exactly as it was: masking it would corrupt the artifact.
    assert outcome.record.model_id == record.model_id
    assert FAKE_KEY not in outcome.describe()
    assert "model_id" in outcome.describe()


def test_nested_join_keys_are_scanned_too() -> None:
    record = _record(
        tool_calls=[ToolCallTrace(name=f"tool-{FAKE_KEY}")],
        retrieval_sources=[RetrievalTrace(source_id=f"src-{FAKE_KEY}", snippet="s")],
        errors=[ErrorRecord(kind=f"kind-{FAKE_KEY}", message="m")],
    )
    assert identity_findings(record) == (
        ("errors[0].kind", "openai_key"),
        ("retrieval_sources[0].source_id", "openai_key"),
        ("tool_calls[0].name", "openai_key"),
    )
    assert redact_record(record).status == FLAGGED


def test_every_declared_redaction_status_is_reachable() -> None:
    """The schema allows three values; a vocabulary with a dead member is a lie."""
    produced = {
        redact_record(_record(final_output="ordinary")).status,
        redact_record(_record(final_output=f"x {FAKE_KEY}")).status,
        redact_record(_record(model_id=FAKE_KEY)).status,
    }
    assert produced == {CLEAN, REDACTED, FLAGGED}
    assert produced == set(RunRecord._ALLOWED["redaction_status"])


# --------------------------------------------------------------------------- #
# Redaction runs before persistence
# --------------------------------------------------------------------------- #


def test_the_executor_redacts_before_it_hashes_and_writes(tmp_path: Path) -> None:
    """``records_sha256`` must digest the bytes on disk, which are the masked ones."""
    config = RunConfig(suite_path=SMOKE_SUITE, adapter="mock", run_id="leaky")
    result = execute_suite(_suite(_case()), _LeakyAdapter(), config)

    assert [r.redaction_status for r in result.records] == [REDACTED]
    assert result.meta.redacted_case_count == 1

    write_run(result, tmp_path)
    written = (tmp_path / "leaky" / RUN_JSONL).read_text(encoding="utf-8")
    assert FAKE_KEY not in written
    assert MASK.format(rule="openai_key") in written
    assert secret_findings(written) == ()
    # write_run's own digest check passes only because the hash was taken after
    # redaction; this asserts the ordering rather than trusting the comment.
    assert file_sha256(tmp_path / "leaky" / RUN_JSONL) == result.meta.records_sha256


def test_token_estimates_describe_the_text_that_is_persisted() -> None:
    """Redaction runs before accounting, so a reader can recompute the estimate.

    Where the adapter supplies no token count the executor derives one. Deriving it
    from the *unredacted* text would publish a number nobody holding the artifact can
    reproduce — and would quietly encode the length of the secret.
    """
    config = RunConfig(suite_path=SMOKE_SUITE, adapter="mock", run_id="leaky")
    record = execute_suite(_suite(_case()), _LeakyAdapter(), config).records[0]

    assert record.output_tokens_estimate == estimate_tokens(record.final_output)
    assert record.output_tokens_estimate != estimate_tokens(f"here you go: {FAKE_KEY}")


def test_a_run_whose_join_key_holds_a_secret_is_refused(tmp_path: Path) -> None:
    config = RunConfig(suite_path=SMOKE_SUITE, adapter="mock", run_id="flagged")
    with pytest.raises(ExecutorError) as excinfo:
        execute_suite(_suite(_case()), _FlaggedAdapter(), config)
    message = str(excinfo.value)
    assert "model_id" in message and "openai_key" in message
    assert FAKE_KEY not in message
    assert not list(tmp_path.iterdir())  # nothing was written


def test_run_meta_counts_the_records_it_masked(tmp_path: Path) -> None:
    result = run_suite(
        RunConfig(suite_path=SMOKE_SUITE, adapter="recorded", app_version="baseline"),
        tmp_path,
    )
    assert result.meta.redacted_case_count == 0
    assert {r.redaction_status for r in result.records} == {CLEAN}
    assert RunMeta.from_json(
        result.meta_path.read_text(encoding="utf-8")
    ).redacted_case_count == 0


def test_the_committed_fixtures_pass_through_redaction_unchanged() -> None:
    """The guard against a future rule quietly rewriting scored text.

    Every fixture record is an input to a score. If redaction ever changes one, the
    baseline and candidate it compares stop meaning what the expectation file says
    they mean — and the failure would surface as a mysterious evaluator drift rather
    than as a redaction bug. It surfaces here instead.
    """
    seen = 0
    for suite in SUITE_NAMES:
        for name in ("baseline.jsonl", "candidate.jsonl"):
            for record in read_jsonl(DATA_ROOT / suite / name, RunRecord):
                outcome = redact_record(record)
                assert outcome.status == CLEAN, (suite, record.case_id)
                assert outcome.record.to_json() == record.to_json()
                seen += 1
    assert seen == 52  # 6 suites x (baseline + candidate)


# --------------------------------------------------------------------------- #
# Redaction runs before reports and before spans
# --------------------------------------------------------------------------- #


def test_the_report_generator_refuses_to_emit_unredacted_content() -> None:
    """The milestone's fourth assertion, at the level of the gate itself."""
    with pytest.raises(reports.ReportError, match="openai_key"):
        reports.base.assert_redaction_clean(f"run id: {FAKE_KEY}", source="r.md")


def _leaky_span_tree() -> list[Span]:
    """A valid tree whose only defect is a credential in a tool *name* (a join key)."""
    request = Span(
        trace_id="t",
        span_id="s0",
        name="request",
        run_id="r",
        case_id="c",
        start_ms=0.0,
        end_ms=10.0,
        attributes={"timing": TIMING_RECORDED},
    )
    tool_call = Span(
        trace_id="t",
        span_id="s1",
        parent_id="s0",
        name="tool_call",
        run_id="r",
        case_id="c",
        start_ms=0.0,
        end_ms=10.0,
        attributes={"timing": TIMING_DERIVED, "tool_name": f"tool-{FAKE_KEY}"},
    )
    return [request, tool_call]


def test_a_span_file_carrying_a_secret_is_refused_and_not_written(
    tmp_path: Path,
) -> None:
    """Spans are a gate target, not a mask target: every string in them is a key.

    The tree is structurally valid, so this asserts the *redaction* gate fired and
    not ``validate_spans``.
    """
    spans = _leaky_span_tree()
    validate_spans(spans)  # the tree itself is fine

    out = tmp_path / "spans.jsonl"
    with pytest.raises(RedactionError, match="openai_key") as excinfo:
        write_spans(out, spans)
    assert FAKE_KEY not in str(excinfo.value)
    assert not out.exists()


# --------------------------------------------------------------------------- #
# Repository hygiene
# --------------------------------------------------------------------------- #


def test_scan_tree_finds_a_planted_credential(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "config.py").write_text(
        f"HOST = 'localhost'\nKEY = '{FAKE_KEY}'\n", encoding="utf-8"
    )
    findings = scan_tree(tmp_path)
    assert [(f.path, f.line, f.rule) for f in findings] == [
        ("pkg/config.py", 2, "openai_key")
    ]
    assert FAKE_KEY not in findings[0].render()


def test_scan_tree_honours_the_allow_marker(tmp_path: Path) -> None:
    (tmp_path / "t.py").write_text(
        f"assert scan('{FAKE_KEY}')  # {ALLOW_MARKER}\n", encoding="utf-8"
    )
    assert scan_tree(tmp_path) == ()


def test_scan_tree_skips_generated_and_binary_paths(tmp_path: Path) -> None:
    for relative in ("results/runs/r/run.jsonl", ".git/objects/x", "a/__pycache__/c"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(FAKE_KEY, encoding="utf-8")
    (tmp_path / "weights.bin").write_bytes(FAKE_KEY.encode())
    assert scan_tree(tmp_path) == ()


def test_scan_tree_is_deterministic_and_sorted(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text(f"{FAKE_KEY}\n", encoding="utf-8")
    (tmp_path / "a.py").write_text(f"x\n{FAKE_KEY}\n", encoding="utf-8")
    findings = scan_tree(tmp_path)
    assert [(f.path, f.line) for f in findings] == [("a.py", 2), ("b.py", 1)]
    assert scan_tree(tmp_path) == findings


def test_this_repository_holds_no_committed_credential() -> None:
    """The hygiene check, run against the tree it protects."""
    findings = scan_tree(repo_root())
    assert findings == (), "\n".join(f.render() for f in findings)


def test_the_hygiene_scan_reports_only_the_certain_tier(tmp_path: Path) -> None:
    """An author email in ``pyproject.toml`` is not a leaked credential.

    A hygiene check that flags every SHA-256 digest and every email in the tree is a
    hygiene check that gets skipped. Masking is applied on suspicion; a repository is
    scanned for evidence. The two tiers are the same table, read with different
    thresholds of proof.
    """
    for name in ("email", "high_entropy_token", "secret_assignment"):
        (tmp_path / f"{name}.md").write_text(_text(name), encoding="utf-8")
    assert scan_tree(tmp_path) == ()  # heuristic hits are not hygiene findings
    for name in ("email", "high_entropy_token", "secret_assignment"):
        assert redact_text(_text(name)) != _text(name)  # ...but a trace masks them


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_scan_secrets_passes_on_this_repository(capsys) -> None:
    assert cli.main(["scan-secrets", str(repo_root())]) == 0
    printed = capsys.readouterr().out
    assert "findings         : 0" in printed
    assert "result           : PASS" in printed


def test_cli_scan_secrets_fails_on_a_planted_credential(
    tmp_path: Path, capsys
) -> None:
    (tmp_path / "leak.py").write_text(f"K = '{FAKE_KEY}'\n", encoding="utf-8")
    assert cli.main(["scan-secrets", str(tmp_path)]) == 1
    printed = capsys.readouterr().out
    assert "leak.py:1" in printed and "openai_key" in printed
    assert FAKE_KEY not in printed
    assert "result           : FAIL" in printed


def test_cli_run_suite_reports_the_redaction_outcome(tmp_path: Path, capsys) -> None:
    code = cli.main(
        ["run-suite", "--suite", str(SMOKE_SUITE), "--out", str(tmp_path), "--dry-run"]
    )
    assert code == 0
    assert "redaction        : 0 record(s) masked, 8 clean" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #

_FORBIDDEN = {"time", "random", "datetime", "socket", "urllib", "requests", "os"}


def test_the_redactor_reaches_for_no_clock_rng_or_socket() -> None:
    """Redaction sits in every scored path; an ambient value here poisons all of it."""
    tree = ast.parse(Path(redaction.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert not (imported & _FORBIDDEN), sorted(imported & _FORBIDDEN)


def test_redaction_of_the_same_text_is_byte_identical_across_calls() -> None:
    blob = "\n".join(_text(name) for name in sorted(SAMPLES))
    assert redact_text(blob) == redact_text(blob)
    assert scan_text(redact_text(blob)) == ()
