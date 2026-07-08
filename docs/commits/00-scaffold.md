# Milestone 0 — Scaffold and project contract

## What changed
Established the code skeleton and packaging so later milestones have a base to
build on (the project contract, plan, docs, website, and runner already existed
from the bootstrap commits).

- **`pyproject.toml`** — `src/` layout; console script `llmreg`; `requires-python
  >=3.12`; **empty runtime `dependencies`** (stdlib-only core); optional `dev`
  (`pytest`, `ruff`) and `extras` (`pydantic`, `pandas`) groups; pytest and ruff
  config. Version is dynamic, read from the package so there is one source of
  truth.
- **`src/llm_regression_observability/__init__.py`** — `__version__ = "0.1.0"`
  plus title/summary metadata.
- **`src/llm_regression_observability/cli.py`** — argparse CLI with `version`
  and `check-env` subcommands, `build_parser()`, and `main()`.
- **`src/llm_regression_observability/__main__.py`** — `python3 -m` entry point.
- **`conftest.py`** — prepends `src/` to `sys.path` so the suite runs without an
  install (the build host has minimal disk and no guaranteed network).
- **`tests/test_smoke.py`**, **`tests/test_scaffold_layout.py`** — 14 CPU-only,
  offline tests.

## Why / key decisions
- **Dependency-free core is a hard constraint** (CLAUDE.md, PLAN §3), so it is
  encoded as a test (`test_core_has_no_runtime_dependencies`) rather than left
  to convention — the build can no longer silently acquire a runtime dep.
- The CLI `check-env` is a small, self-contained check (interpreter + required
  stdlib) so the CLI is testable offline. It intentionally does not import
  `scripts/00_check_environment.py`, which stays standalone so it works before
  the package exists.
- `conftest.py` shim (over an editable install) keeps `python3 -m pytest`
  runnable with only `pytest` present.

## Validation run
- `python3 -m pytest -q` → 14 passed.
- `python3 -m llm_regression_observability --help` / `check-env` / `version` →
  all work (run with `src/` on the path).
- `python3 scripts/00_check_environment.py` → PASS.
- `ruff check src tests conftest.py` → clean (optional; ruff is a dev extra).

Next: Milestone 1 — core schemas and trace model.
