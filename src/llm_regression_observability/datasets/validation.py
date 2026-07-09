"""Fixture validation: the guarantees Milestone 6 later relies on.

``validate_fixtures`` walks every suite and enforces, from
``docs/milestones/02-fixtures.md``:

1. **Everything parses against the schemas** — cases as :class:`EvalCase`,
   expectations as :class:`CaseExpectation`, runs as :class:`RunRecord` (loading
   already raises on malformed input; this collects those errors instead of
   stopping at the first).
2. **Referential integrity** — the same ``case_id`` set appears in ``cases``,
   ``expected``, ``baseline`` and ``candidate``; baseline/candidate carry the
   right ``app_version``; each suite uses its declared ``risk_category``.
3. **Every injected defect is annotated with its expected category** — a
   non-control expectation names a category allowed for the suite *and* the
   candidate actually differs from the baseline on some behavioral field (so the
   annotation cannot describe a defect that was never injected).
4. **Synthetic-only, no secrets** — no fixture text matches a credential-like
   pattern. The pattern table is the Milestone 9 one
   (:data:`~..traces.redaction.SECRET_PATTERNS`), not a copy of it: a fixture, a
   report, a span file and a source tree are all scanned against the same
   definition of "credential-like".

The checks are collected into a :class:`ValidationReport` rather than raised, so
one run reports every problem. The CLI (``llmreg validate-fixtures``) and
``scripts/01_validate_fixtures.py`` both render this report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..schemas import RunRecord, SchemaError
from ..traces.redaction import SECRET_PATTERNS, scan_for_secrets
from .fixtures import (
    NO_REGRESSION,
    SUITE_EXPECTED_CATEGORIES,
    SUITE_NAMES,
    SUITE_RISK_CATEGORY,
    resolve_fixture_roots,
    suites_root,
)
from .loader import (
    CaseExpectation,
    load_suite_fixture,
    load_suite_manifest,
)

__all__ = [
    "Issue",
    "ValidationReport",
    "validate_fixtures",
    "scan_for_secrets",
    "SECRET_PATTERNS",
]

# RunRecord fields that identify *the run*, not the model's behavior. Two records
# are compared on everything except these when checking a defect was injected.
_CONTEXT_FIELDS: frozenset[str] = frozenset(
    {
        "run_id",
        "app_version",
        "model_id",
        "prompt_version",
        "tool_version",
        "timestamp",
    }
)

@dataclass
class Issue:
    """A single validation problem, located as precisely as possible."""

    suite: str
    kind: str
    message: str
    case_id: str | None = None

    def render(self) -> str:
        where = f"{self.suite}" + (f"/{self.case_id}" if self.case_id else "")
        return f"[{self.kind}] {where}: {self.message}"


@dataclass
class ValidationReport:
    """Outcome of :func:`validate_fixtures`."""

    issues: list[Issue] = field(default_factory=list)
    suites_checked: list[str] = field(default_factory=list)
    case_count: int = 0
    control_count: int = 0
    defect_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.issues

    def add(self, suite: str, kind: str, message: str, case_id: str | None = None):
        self.issues.append(
            Issue(suite=suite, kind=kind, message=message, case_id=case_id)
        )


def _behavioral_fields(record: RunRecord) -> dict[str, Any]:
    return {k: v for k, v in record.to_dict().items() if k not in _CONTEXT_FIELDS}


def _index_by_case(records, kind, suite, report) -> dict[str, Any]:
    index: dict[str, Any] = {}
    for rec in records:
        if rec.case_id in index:
            report.add(suite, "duplicate", f"duplicate {kind} for case", rec.case_id)
        index[rec.case_id] = rec
    return index


def _check_suite(
    suite: str, evals_root: Path, data_root: Path, report: ValidationReport
) -> list[CaseExpectation]:
    try:
        fixture = load_suite_fixture(suite, evals_root, data_root)
    except (SchemaError, FileNotFoundError, OSError) as exc:
        report.add(suite, "load", str(exc))
        return []

    cases = _index_by_case(fixture.cases, "case", suite, report)
    expectations = _index_by_case(fixture.expectations, "expectation", suite, report)
    baseline = _index_by_case(fixture.baseline, "baseline run", suite, report)
    candidate = _index_by_case(fixture.candidate, "candidate run", suite, report)

    # Referential integrity: identical case_id sets across all four files.
    reference = set(cases)
    for label, other in (
        ("expected", set(expectations)),
        ("baseline", set(baseline)),
        ("candidate", set(candidate)),
    ):
        for missing in sorted(reference - other):
            report.add(suite, "missing", f"case absent from {label}.*", missing)
        for extra in sorted(other - reference):
            report.add(
                suite, "orphan",
                f"{label}.* has a case not in cases.json", extra,
            )

    risk = SUITE_RISK_CATEGORY[suite]
    allowed_categories = SUITE_EXPECTED_CATEGORIES[suite] | {NO_REGRESSION}

    for case_id in sorted(reference):
        case = cases[case_id]
        if case.risk_category != risk:
            report.add(
                suite, "risk_category",
                f"risk_category {case.risk_category!r}, expected {risk!r}", case_id,
            )
        base = baseline.get(case_id)
        cand = candidate.get(case_id)
        if base is not None and base.app_version != "baseline":
            report.add(suite, "app_version",
                       f"baseline run app_version={base.app_version!r}", case_id)
        if cand is not None and cand.app_version != "candidate":
            report.add(suite, "app_version",
                       f"candidate run app_version={cand.app_version!r}", case_id)

        exp = expectations.get(case_id)
        if exp is None:
            continue
        if exp.expected_category not in allowed_categories:
            report.add(
                suite, "category",
                f"expected_category {exp.expected_category!r} not allowed for suite "
                f"(allowed: {sorted(allowed_categories)})", case_id,
            )
        # A non-control expectation must have a real severity and a real,
        # injected difference between candidate and baseline.
        if exp.is_control:
            if exp.expected_severity != NO_REGRESSION:
                report.add(suite, "severity",
                           "control case must have expected_severity 'none'", case_id)
            report.control_count += 1
        else:
            report.defect_count += 1
            if exp.expected_severity == NO_REGRESSION:
                report.add(suite, "severity",
                           "non-control case needs a real severity", case_id)
            if base is not None and cand is not None:
                if _behavioral_fields(base) == _behavioral_fields(cand):
                    report.add(
                        suite, "no_defect",
                        "expected_category set but candidate is behaviorally "
                        "identical to baseline (defect not actually injected)",
                        case_id,
                    )
        report.case_count += 1

    return fixture.expectations


def _scan_files_for_secrets(paths, report: ValidationReport) -> None:
    for suite, path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for name in scan_for_secrets(text):
            report.add(suite, "secret",
                       f"credential-like pattern {name!r} in {path.name}")


def validate_fixtures(path: Path | str | None = None) -> ValidationReport:
    """Validate every fixture suite under ``path`` (default: repo fixtures)."""
    evals_root, data_root = resolve_fixture_roots(path)
    report = ValidationReport()

    for suite in SUITE_NAMES:
        sdir = evals_root / suite
        if not sdir.is_dir():
            report.add(suite, "missing_suite", f"no fixtures directory at {sdir}")
            continue
        report.suites_checked.append(suite)
        _check_suite(suite, evals_root, data_root, report)

    # Safety scan across every fixture file (raw text catches any nested value).
    scan_targets: list[tuple[str, Path]] = []
    for suite in SUITE_NAMES:
        for rel in (evals_root / suite / "cases.json",
                    evals_root / suite / "expected.json",
                    data_root / suite / "baseline.jsonl",
                    data_root / suite / "candidate.jsonl"):
            if rel.is_file():
                scan_targets.append((suite, rel))
    _scan_files_for_secrets(scan_targets, report)

    # The smoke manifest must load and every reference must resolve to a case.
    _validate_manifests(evals_root, report)
    return report


def _validate_manifests(evals_root: Path, report: ValidationReport) -> None:
    manifest_dir = suites_root()
    if not manifest_dir.is_dir():
        return
    for manifest_path in sorted(manifest_dir.glob("*.yaml")):
        try:
            manifest = load_suite_manifest(manifest_path)
        except (SchemaError, OSError, ValueError) as exc:
            report.add("(manifest)", "manifest", f"{manifest_path.name}: {exc}")
            continue
        for ref in manifest.cases:
            case_file = evals_root / ref.suite / "cases.json"
            if not case_file.is_file():
                report.add("(manifest)", "manifest",
                           f"{manifest_path.name} -> unknown suite {ref.suite!r}")
                continue
            from .loader import load_cases
            try:
                ids = {c.case_id for c in load_cases(case_file)}
            except SchemaError:
                continue
            if ref.case_id not in ids:
                report.add("(manifest)", "manifest",
                           f"{manifest_path.name} -> {ref.suite}/{ref.case_id} "
                           "not found")
