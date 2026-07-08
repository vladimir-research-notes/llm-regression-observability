"""Dataset loading, fixture layout, and fixture validation.

Public surface used by the CLI (``llmreg validate-fixtures``), the validation
script, and later milestones (adapters replay these recorded runs; the
comparison engine asserts against the ``CaseExpectation`` ground truth).
"""

from __future__ import annotations

from ._yaml import YamlError, load_yaml
from .fixtures import (
    NO_REGRESSION,
    SUITE_EXPECTED_CATEGORIES,
    SUITE_NAMES,
    SUITE_RISK_CATEGORY,
    data_fixtures_root,
    evals_fixtures_root,
    repo_root,
    resolve_fixture_roots,
    suites_root,
)
from .loader import (
    CaseExpectation,
    ManifestCaseRef,
    SuiteFixture,
    SuiteManifest,
    load_cases,
    load_eval_suite,
    load_expectations,
    load_run_records,
    load_suite_fixture,
    load_suite_manifest,
)
from .validation import (
    Issue,
    ValidationReport,
    scan_for_secrets,
    validate_fixtures,
)

__all__ = [
    "YamlError",
    "load_yaml",
    "NO_REGRESSION",
    "SUITE_NAMES",
    "SUITE_RISK_CATEGORY",
    "SUITE_EXPECTED_CATEGORIES",
    "repo_root",
    "evals_fixtures_root",
    "data_fixtures_root",
    "suites_root",
    "resolve_fixture_roots",
    "CaseExpectation",
    "SuiteFixture",
    "SuiteManifest",
    "ManifestCaseRef",
    "load_cases",
    "load_eval_suite",
    "load_expectations",
    "load_run_records",
    "load_suite_fixture",
    "load_suite_manifest",
    "Issue",
    "ValidationReport",
    "validate_fixtures",
    "scan_for_secrets",
]
