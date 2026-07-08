Human review: Milestone 0 — Scaffold and project contract

TL;DR
Added the code skeleton the rest of the milestones build on: a dependency-free
`src/` package, `pyproject.toml`, the `llmreg` CLI (`version`, `check-env`), a
`python3 -m` entry, a `conftest.py` path shim, and the first CPU-only tests.
All validation commands pass; nothing is pushed.

What changed
- `pyproject.toml` — `src/` layout, console script `llmreg`, dynamic version
  (read from `__init__.__version__`, single source of truth), empty runtime
  `dependencies`, optional `dev`/`extras` groups, pytest + ruff config.
- `src/llm_regression_observability/__init__.py` — `__version__` (0.1.0) and
  package metadata.
- `src/llm_regression_observability/cli.py` — argparse CLI: `version` and
  `check-env` subcommands, `build_parser()` and `main()`.
- `src/llm_regression_observability/__main__.py` — `python3 -m` entry.
- `conftest.py` — prepends `src/` to `sys.path` so tests run without an install.
- `tests/test_smoke.py`, `tests/test_scaffold_layout.py` — 14 tests: package
  import, CLI paths, module entry point, layout, and the no-runtime-deps /
  Python >=3.12 / console-script constraints.
- Docs/site: milestone doc flipped to Complete; `site/index.html` and
  `site/milestones.html` status updated to "Milestone 0 complete".

What should be checked later
- Whether `setuptools` is the desired build backend (vs hatchling) once the
  package is actually installed somewhere; today the suite runs via the
  `conftest.py` shim, not an install.
- `[project.urls] Documentation` is a placeholder (`example.invalid`); set a
  real URL if/when one exists.
- CLI `check-env` deliberately duplicates a small subset of
  `scripts/00_check_environment.py` (the script stays standalone so it works
  before the package exists). Confirm that duplication is acceptable.

Technical risks
- Low. No network, no paid API, no secrets, no ambient time/random. Core has
  zero runtime dependencies (enforced by a test). Determinism unaffected.

Commands to verify
    python3 -m pytest -q
    cd src && python3 -m llm_regression_observability --help   # then check-env / version
    python3 scripts/00_check_environment.py
    ruff check src tests conftest.py                            # optional (dev extra)

Human feedback
<!-- Human will fill this later. -->
