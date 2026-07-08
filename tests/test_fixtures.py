"""Tests for the fixture suites, loaders, and validator (Milestone 2).

CPU-only and offline. They assert the guarantees Milestone 6 later depends on:

* the six committed suites validate cleanly (schemas, referential integrity,
  ground-truth annotations, safety scan);
* the loaders round-trip real fixtures and reject malformed ones;
* the restricted-YAML reader parses the manifest subset and refuses what it does
  not support;
* the injected-defect and control invariants hold per case;
* the secret scanner catches credential-like strings and the manifest resolves.
"""

from __future__ import annotations

import pytest

from llm_regression_observability.datasets import (
    NO_REGRESSION,
    SUITE_EXPECTED_CATEGORIES,
    SUITE_NAMES,
    SUITE_RISK_CATEGORY,
    CaseExpectation,
    YamlError,
    evals_fixtures_root,
    load_suite_fixture,
    load_suite_manifest,
    load_yaml,
    resolve_fixture_roots,
    scan_for_secrets,
    suites_root,
    validate_fixtures,
)
from llm_regression_observability.datasets.validation import _behavioral_fields
from llm_regression_observability.schemas import SchemaError

EVALS_ROOT, DATA_ROOT = resolve_fixture_roots(None)


# --------------------------------------------------------------------------- #
# The committed fixtures validate.
# --------------------------------------------------------------------------- #


def test_all_fixtures_validate_clean() -> None:
    report = validate_fixtures()
    assert report.ok, [i.render() for i in report.issues]
    assert report.suites_checked == list(SUITE_NAMES)
    assert report.defect_count > 0
    assert report.control_count > 0
    assert report.case_count == report.defect_count + report.control_count


@pytest.mark.parametrize("suite", SUITE_NAMES)
def test_every_suite_has_paired_fixtures(suite) -> None:
    fixture = load_suite_fixture(suite, EVALS_ROOT, DATA_ROOT)
    case_ids = {c.case_id for c in fixture.cases}
    assert case_ids, f"{suite} has no cases"
    assert {e.case_id for e in fixture.expectations} == case_ids
    assert {r.case_id for r in fixture.baseline} == case_ids
    assert {r.case_id for r in fixture.candidate} == case_ids
    # Every case declares the suite's risk category.
    for case in fixture.cases:
        assert case.risk_category == SUITE_RISK_CATEGORY[suite]


@pytest.mark.parametrize("suite", SUITE_NAMES)
def test_defects_are_real_and_annotated(suite) -> None:
    fixture = load_suite_fixture(suite, EVALS_ROOT, DATA_ROOT)
    baseline = {r.case_id: r for r in fixture.baseline}
    candidate = {r.case_id: r for r in fixture.candidate}
    allowed = SUITE_EXPECTED_CATEGORIES[suite] | {NO_REGRESSION}
    for exp in fixture.expectations:
        assert exp.expected_category in allowed
        if exp.is_control:
            assert exp.expected_severity == NO_REGRESSION
        else:
            # A non-control expectation must correspond to a real diff.
            base = _behavioral_fields(baseline[exp.case_id])
            cand = _behavioral_fields(candidate[exp.case_id])
            assert base != cand, f"{suite}/{exp.case_id}: defect not injected"
            assert exp.expected_severity in {"info", "warn", "critical"}


def test_infra_attributed_control_exists() -> None:
    # Research question 9: an infra-attributed degradation is surfaced but not
    # flagged. The agent_trace suite must ship exactly this ground truth.
    fixture = load_suite_fixture("agent_trace", EVALS_ROOT, DATA_ROOT)
    infra = [e for e in fixture.expectations if e.expected_attribution == "infra"]
    assert infra, "expected an infra-attributed case in agent_trace"
    for exp in infra:
        assert exp.expected_category == NO_REGRESSION


# --------------------------------------------------------------------------- #
# Loader validation.
# --------------------------------------------------------------------------- #


def test_case_expectation_round_trip() -> None:
    exp = CaseExpectation(
        case_id="c",
        injected_defect="d",
        expected_category="latency_regression",
        expected_severity="warn",
    )
    assert CaseExpectation.from_dict(exp.to_dict()) == exp


def test_case_expectation_rejects_unknown_category() -> None:
    with pytest.raises(SchemaError, match="expected_category"):
        CaseExpectation(
            case_id="c",
            injected_defect="d",
            expected_category="made_up_regression",
            expected_severity="warn",
        )


def test_case_expectation_control_is_detected() -> None:
    exp = CaseExpectation(
        case_id="c",
        injected_defect="benign change",
        expected_category=NO_REGRESSION,
        expected_severity=NO_REGRESSION,
    )
    assert exp.is_control


# --------------------------------------------------------------------------- #
# Restricted-YAML reader.
# --------------------------------------------------------------------------- #


def test_yaml_parses_manifest_subset() -> None:
    data = load_yaml(
        "suite_id: demo\n"
        "description: a demo\n"
        "thresholds:\n"
        "  latency_abs_ms: 50\n"
        "  cost_rel: 0.2\n"
        "cases:\n"
        "  - structured_json/sj_invalid_json\n"
        "  - tool_use/tu_wrong_tool\n"
    )
    assert data["suite_id"] == "demo"
    assert data["thresholds"] == {"latency_abs_ms": 50, "cost_rel": 0.2}
    assert data["cases"] == [
        "structured_json/sj_invalid_json",
        "tool_use/tu_wrong_tool",
    ]


def test_yaml_scalar_types_and_comments() -> None:
    data = load_yaml(
        "# a comment\n"
        "flag: true\n"
        "off: false\n"
        "nothing: null\n"
        "count: 7           # inline comment\n"
        "ratio: 0.25\n"
        "name: plain text\n"
        'quoted: "keeps: colon"\n'
    )
    assert data == {
        "flag": True,
        "off": False,
        "nothing": None,
        "count": 7,
        "ratio": 0.25,
        "name": "plain text",
        "quoted": "keeps: colon",
    }


def test_yaml_rejects_tabs_and_flow_style() -> None:
    with pytest.raises(YamlError):
        load_yaml("key:\n\t- x\n")
    with pytest.raises(YamlError):
        load_yaml("cases: [a, b]\n")
    with pytest.raises(YamlError):
        load_yaml("cases:\n  - a: 1\n")  # sequences of mappings unsupported


# --------------------------------------------------------------------------- #
# Manifest loading + resolution.
# --------------------------------------------------------------------------- #


def test_smoke_manifest_loads_and_resolves() -> None:
    manifest = load_suite_manifest(suites_root() / "smoke.yaml")
    assert manifest.suite_id == "smoke"
    assert manifest.cases, "smoke manifest references no cases"
    # Every referenced case exists in its suite's cases.json.
    known: dict[str, set[str]] = {}
    for suite in SUITE_NAMES:
        fixture = load_suite_fixture(suite, EVALS_ROOT, DATA_ROOT)
        known[suite] = {c.case_id for c in fixture.cases}
    for ref in manifest.cases:
        assert ref.suite in known, f"unknown suite {ref.suite}"
        assert ref.case_id in known[ref.suite], f"dangling {ref.suite}/{ref.case_id}"


# --------------------------------------------------------------------------- #
# Safety scan.
# --------------------------------------------------------------------------- #


def test_secret_scanner_flags_planted_credentials() -> None:
    # AWS's own documented example key id — synthetic, safe to test against.
    assert "aws_access_key" in scan_for_secrets("id=AKIAIOSFODNN7EXAMPLE")
    assert scan_for_secrets("Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345")
    assert "private_key" in scan_for_secrets("-----BEGIN RSA PRIVATE KEY-----")


def test_committed_fixtures_have_no_secrets() -> None:
    for suite in SUITE_NAMES:
        for path in (
            EVALS_ROOT / suite / "cases.json",
            EVALS_ROOT / suite / "expected.json",
            DATA_ROOT / suite / "baseline.jsonl",
            DATA_ROOT / suite / "candidate.jsonl",
        ):
            assert not scan_for_secrets(path.read_text(encoding="utf-8")), path


# --------------------------------------------------------------------------- #
# Validator catches injected problems (operating on a temp copy).
# --------------------------------------------------------------------------- #


def _copy_fixtures(tmp_path):
    import shutil

    root = evals_fixtures_root().parent.parent
    dst = tmp_path / "repo"
    (dst / "evals").mkdir(parents=True)
    (dst / "data").mkdir(parents=True)
    shutil.copytree(root / "evals" / "fixtures", dst / "evals" / "fixtures")
    shutil.copytree(root / "data" / "fixtures", dst / "data" / "fixtures")
    return dst


def test_validator_flags_missing_candidate(tmp_path) -> None:
    repo = _copy_fixtures(tmp_path)
    cand = repo / "data" / "fixtures" / "tool_use" / "candidate.jsonl"
    lines = cand.read_text(encoding="utf-8").splitlines()
    cand.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")  # drop one case
    report = validate_fixtures(repo / "evals" / "fixtures")
    assert not report.ok
    assert any(i.kind == "missing" for i in report.issues)


def test_validator_flags_uninjected_defect(tmp_path) -> None:
    repo = _copy_fixtures(tmp_path)
    # Make a candidate identical to its baseline while the expectation still
    # claims a regression: the "defect not injected" check must fire.
    base = repo / "data" / "fixtures" / "latency_cost" / "baseline.jsonl"
    cand = repo / "data" / "fixtures" / "latency_cost" / "candidate.jsonl"
    baseline_lines = base.read_text(encoding="utf-8").splitlines()
    # Rebuild candidate lines from baseline but relabel as candidate.
    patched = [ln.replace('"baseline"', '"candidate"') for ln in baseline_lines]
    cand.write_text("\n".join(patched) + "\n", encoding="utf-8")
    report = validate_fixtures(repo / "evals" / "fixtures")
    assert not report.ok
    assert any(i.kind == "no_defect" for i in report.issues)
