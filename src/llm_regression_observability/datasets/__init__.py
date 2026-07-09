"""Dataset loading, fixture layout, fixture validation, and the trace contract.

Public surface used by the CLI (``llmreg validate-fixtures``, ``llmreg import-trace``,
``llmreg export-run``), the validation script, and later milestones (adapters replay
these recorded runs; the comparison engine asserts against the ``CaseExpectation``
ground truth).

:mod:`.versioning` — the versioned import/export envelope (Milestone 10) — is
exported here. The :mod:`.importers` subpackage is **not**: :mod:`.importers.runs`
imports :mod:`...executor`, which imports this package, so importing it here would
close the cycle. Import it as ``from ...datasets.importers import import_run``.
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
from .versioning import (
    CONTRACT_VERSION,
    DEFAULT_TIMESTAMP,
    NATIVE_SCHEMA,
    NATIVE_SOURCE,
    SUPPORTED_CONTRACT_VERSIONS,
    TraceBundle,
    VersioningError,
    check_contract_version,
    check_record_schema,
    export_bundle,
    is_envelope,
    native_payload,
    payload_digest,
    read_bundle,
    read_document,
    record_schema_fingerprint,
    write_bundle,
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
    # Import/export contract (Milestone 10)
    "VersioningError",
    "CONTRACT_VERSION",
    "SUPPORTED_CONTRACT_VERSIONS",
    "NATIVE_SOURCE",
    "NATIVE_SCHEMA",
    "DEFAULT_TIMESTAMP",
    "TraceBundle",
    "record_schema_fingerprint",
    "check_contract_version",
    "check_record_schema",
    "payload_digest",
    "is_envelope",
    "native_payload",
    "export_bundle",
    "write_bundle",
    "read_bundle",
    "read_document",
]
