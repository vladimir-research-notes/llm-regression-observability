"""Fixture layout: canonical suite names, on-disk roots, and their contracts.

The fixture tree is split, per ``docs/milestones/02-fixtures.md``:

* ``evals/fixtures/<suite>/cases.json``    — a list of :class:`EvalCase` dicts.
* ``evals/fixtures/<suite>/expected.json`` — a list of ``CaseExpectation`` dicts,
  the *ground truth* that says which regression (if any) the candidate should
  trigger for each case.
* ``data/fixtures/<suite>/baseline.jsonl`` — recorded baseline ``RunRecord``s.
* ``data/fixtures/<suite>/candidate.jsonl`` — recorded candidate ``RunRecord``s,
  each carrying exactly one injected defect (or none, for control cases).

Every suite maps to one ``risk_category`` and to the closed set of regression
categories its candidate cases are allowed to claim, so a fixture cannot annotate
a defect that does not belong to its suite. Roots are resolved relative to the
repository root (located by walking up to ``pyproject.toml``) so the loader works
without an editable install.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "SUITE_NAMES",
    "SUITE_RISK_CATEGORY",
    "SUITE_EXPECTED_CATEGORIES",
    "NO_REGRESSION",
    "repo_root",
    "evals_fixtures_root",
    "data_fixtures_root",
    "suites_root",
    "resolve_fixture_roots",
]

# Sentinel used in a ``CaseExpectation`` for a control case: the candidate is
# changed in a benign way (or below threshold) and must *not* raise a flag.
NO_REGRESSION = "none"

# The six synthetic suites, in a stable order.
SUITE_NAMES: tuple[str, ...] = (
    "structured_json",
    "tool_use",
    "retrieval_evidence",
    "refusal_boundary",
    "latency_cost",
    "agent_trace",
)

# Each suite exercises one ``EvalCase.risk_category``.
SUITE_RISK_CATEGORY: dict[str, str] = {
    "structured_json": "structured",
    "tool_use": "tool_use",
    "retrieval_evidence": "retrieval",
    "refusal_boundary": "refusal",
    "latency_cost": "benign",
    "agent_trace": "agent",
}

# The regression categories a suite's candidate cases may legitimately claim
# (from ``docs/REGRESSION_DETECTION.md``). ``NO_REGRESSION`` is always allowed
# (control cases). A fixture that annotates anything outside this set for its
# suite is a validation error.
SUITE_EXPECTED_CATEGORIES: dict[str, frozenset[str]] = {
    "structured_json": frozenset({"structured_output_regression"}),
    "tool_use": frozenset({"tool_call_regression"}),
    "retrieval_evidence": frozenset({"evidence_grounding_regression"}),
    "refusal_boundary": frozenset({"refusal_behavior_shift"}),
    "latency_cost": frozenset({"latency_regression", "cost_regression"}),
    "agent_trace": frozenset({"error_rate_regression", "latency_regression"}),
}


def repo_root() -> Path:
    """Locate the repository root by walking up to the first ``pyproject.toml``."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    # Fallback: src/llm_regression_observability/datasets/ -> repo root.
    return here.parents[3]


def evals_fixtures_root() -> Path:
    return repo_root() / "evals" / "fixtures"


def data_fixtures_root() -> Path:
    return repo_root() / "data" / "fixtures"


def suites_root() -> Path:
    return repo_root() / "evals" / "suites"


def resolve_fixture_roots(path: Path | str | None) -> tuple[Path, Path]:
    """Return ``(evals_fixtures, data_fixtures)`` for a user-supplied ``path``.

    ``path`` may be ``None`` (use the repo defaults), the ``evals/fixtures``
    directory itself, or a repository root that contains ``evals/fixtures``.
    """
    if path is None:
        return evals_fixtures_root(), data_fixtures_root()
    p = Path(path).resolve()
    if (p / "evals" / "fixtures").is_dir():
        return p / "evals" / "fixtures", p / "data" / "fixtures"
    # Otherwise treat ``p`` as the evals/fixtures directory; its repo root is two
    # levels up (``<repo>/evals/fixtures``).
    return p, p.parent.parent / "data" / "fixtures"
