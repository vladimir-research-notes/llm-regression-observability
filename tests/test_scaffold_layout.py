"""Structural tests for the Milestone 0 scaffold.

These assert the repository layout the rest of the milestones build on, and
enforce the load-bearing constraints from ``CLAUDE.md`` / ``docs/PLAN.md``:
the core has no runtime dependencies, targets Python >= 3.12, and ships the
``llmreg`` console script.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG = REPO_ROOT / "src" / "llm_regression_observability"

REQUIRED_DIRS = [
    "src/llm_regression_observability",
    "tests",
    "docs",
    "docs/milestones",
    "docs/review_notes",
    "docs/commits",
    "scripts",
    "site",
    "results",
]

REQUIRED_FILES = [
    "pyproject.toml",
    "conftest.py",
    "CLAUDE.md",
    "README.md",
    "docs/PLAN.md",
    "src/llm_regression_observability/__init__.py",
    "src/llm_regression_observability/__main__.py",
    "src/llm_regression_observability/cli.py",
]


def test_required_directories_exist() -> None:
    for rel in REQUIRED_DIRS:
        assert (REPO_ROOT / rel).is_dir(), f"missing directory: {rel}"


def test_required_files_exist() -> None:
    for rel in REQUIRED_FILES:
        assert (REPO_ROOT / rel).is_file(), f"missing file: {rel}"


def _load_pyproject() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def test_core_has_no_runtime_dependencies() -> None:
    project = _load_pyproject()["project"]
    assert project.get("dependencies", []) == [], (
        "core must be dependency-free (stdlib only)"
    )


def test_requires_python_312() -> None:
    project = _load_pyproject()["project"]
    assert project["requires-python"].startswith(">=3.12")


def test_declares_llmreg_console_script() -> None:
    scripts = _load_pyproject()["project"]["scripts"]
    assert scripts.get("llmreg") == "llm_regression_observability.cli:main"


def test_optional_extras_do_not_leak_into_core() -> None:
    # pydantic / pandas may appear ONLY under optional-dependencies, never as
    # required runtime deps.
    project = _load_pyproject()["project"]
    core = " ".join(project.get("dependencies", [])).lower()
    assert "pydantic" not in core
    assert "pandas" not in core
