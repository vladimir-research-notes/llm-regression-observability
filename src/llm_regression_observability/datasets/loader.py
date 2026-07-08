"""Loading fixtures and suite manifests off disk.

Everything here is deterministic and offline: JSON / JSONL are read with the
standard library, restricted-YAML manifests through :mod:`._yaml`, and every
record is validated against the contracts in :mod:`..schemas` (or
:class:`CaseExpectation` below) as it is read, so a malformed fixture fails at
load time with a path-qualified :class:`SchemaError`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from ..schemas import EvalCase, RunRecord, Schema, SchemaError
from ._yaml import load_yaml
from .fixtures import (
    NO_REGRESSION,
    SUITE_EXPECTED_CATEGORIES,
    resolve_fixture_roots,
)

__all__ = [
    "CaseExpectation",
    "SuiteFixture",
    "load_cases",
    "load_run_records",
    "load_expectations",
    "load_suite_fixture",
    "load_suite_manifest",
    "SuiteManifest",
    "ManifestCaseRef",
]

# The regression taxonomy (docs/REGRESSION_DETECTION.md) plus the control
# sentinel. A candidate case either claims one of these or ``NO_REGRESSION``.
_REGRESSION_CATEGORIES: frozenset[str] = frozenset().union(
    *SUITE_EXPECTED_CATEGORIES.values()
)


@dataclass(kw_only=True)
class CaseExpectation(Schema):
    """Ground truth for one case: what was injected into the candidate and which
    regression (if any) a correct detector must raise.

    This is the oracle Milestone 6 asserts against. ``expected_category`` is
    ``"none"`` for a *control* case — a candidate that changed benignly or stayed
    below threshold and must **not** flag. ``expected_attribution`` records
    whether a real degradation is the application's fault (``app``, flagged) or
    harness noise (``infra``, surfaced but not flagged; see research question 9).
    """

    case_id: str
    injected_defect: str
    expected_category: str
    expected_severity: str
    expected_attribution: str = "app"
    note: str = ""

    _ALLOWED: ClassVar[dict[str, frozenset[str]]] = {
        "expected_category": _REGRESSION_CATEGORIES | {NO_REGRESSION},
        "expected_severity": frozenset({"info", "warn", "critical", NO_REGRESSION}),
        "expected_attribution": frozenset({"app", "infra"}),
    }

    @property
    def is_control(self) -> bool:
        return self.expected_category == NO_REGRESSION


@dataclass
class SuiteFixture:
    """Everything loaded for one suite: cases, expectations, and paired runs."""

    suite: str
    cases: list[EvalCase] = field(default_factory=list)
    expectations: list[CaseExpectation] = field(default_factory=list)
    baseline: list[RunRecord] = field(default_factory=list)
    candidate: list[RunRecord] = field(default_factory=list)


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _read_jsonl(path: Path) -> list[Any]:
    records: list[Any] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:  # pragma: no cover - message path
                raise SchemaError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
    return records


def _load_list(path: Path, model: type[Schema], reader) -> list[Any]:
    data = reader(path)
    if not isinstance(data, list):
        raise SchemaError(f"{path}: expected a JSON list of {model.__name__}")
    out: list[Any] = []
    for i, item in enumerate(data):
        try:
            out.append(model.from_dict(item))
        except SchemaError as exc:
            raise SchemaError(f"{path}[{i}]: {exc}") from exc
    return out


def load_cases(path: Path) -> list[EvalCase]:
    """Load ``cases.json`` (a JSON list of :class:`EvalCase`)."""
    return _load_list(path, EvalCase, _read_json)


def load_expectations(path: Path) -> list[CaseExpectation]:
    """Load ``expected.json`` (a JSON list of :class:`CaseExpectation`)."""
    return _load_list(path, CaseExpectation, _read_json)


def load_run_records(path: Path) -> list[RunRecord]:
    """Load a ``*.jsonl`` stream of :class:`RunRecord`."""
    return _load_list(path, RunRecord, _read_jsonl)


def load_suite_fixture(
    suite: str, evals_root: Path, data_root: Path
) -> SuiteFixture:
    """Load and schema-validate all four fixture files for one suite."""
    sdir = evals_root / suite
    ddir = data_root / suite
    return SuiteFixture(
        suite=suite,
        cases=load_cases(sdir / "cases.json"),
        expectations=load_expectations(sdir / "expected.json"),
        baseline=load_run_records(ddir / "baseline.jsonl"),
        candidate=load_run_records(ddir / "candidate.jsonl"),
    )


# --------------------------------------------------------------------------- #
# Suite manifests (restricted YAML)
# --------------------------------------------------------------------------- #


@dataclass
class ManifestCaseRef:
    """A ``suite/case_id`` reference from a suite manifest."""

    suite: str
    case_id: str


@dataclass
class SuiteManifest:
    """A parsed ``evals/suites/<id>.yaml`` manifest."""

    suite_id: str
    description: str = ""
    thresholds: dict[str, Any] = field(default_factory=dict)
    cases: list[ManifestCaseRef] = field(default_factory=list)


def load_suite_manifest(path: Path | str) -> SuiteManifest:
    """Load a suite manifest from a restricted-YAML file.

    Each ``cases`` entry is a ``"<suite>/<case_id>"`` string; the referenced
    fixtures are resolved and checked by the fixture validator, not here.
    """
    path = Path(path)
    data = load_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SchemaError(f"{path}: manifest must be a mapping")
    suite_id = data.get("suite_id")
    if not isinstance(suite_id, str) or not suite_id:
        raise SchemaError(f"{path}: manifest needs a non-empty 'suite_id'")
    thresholds = data.get("thresholds", {}) or {}
    if not isinstance(thresholds, dict):
        raise SchemaError(f"{path}: 'thresholds' must be a mapping")
    refs: list[ManifestCaseRef] = []
    for entry in data.get("cases", []) or []:
        if not isinstance(entry, str) or "/" not in entry:
            raise SchemaError(
                f"{path}: each case must be a '<suite>/<case_id>' string, "
                f"got {entry!r}"
            )
        suite, _, case_id = entry.partition("/")
        refs.append(ManifestCaseRef(suite=suite, case_id=case_id))
    return SuiteManifest(
        suite_id=suite_id,
        description=str(data.get("description", "")),
        thresholds=thresholds,
        cases=refs,
    )


def default_roots(path: Path | str | None = None) -> tuple[Path, Path]:
    """Convenience re-export of :func:`.fixtures.resolve_fixture_roots`."""
    return resolve_fixture_roots(path)
