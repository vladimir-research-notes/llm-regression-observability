# Milestone 0 — Scaffold and project contract

**Status: planned.**

## Goal

Establish the package skeleton and packaging so the rest of the milestones have
something to build on. The project contract (CLAUDE.md, PLAN, docs, website,
scripts) already exists from the bootstrap; this milestone adds the *code*
skeleton and the first passing tests.

## Deliverables

- `pyproject.toml` — `src/` layout, console script `llmreg`, `pytest` and `ruff`
  config, **no runtime dependencies** (stdlib-only core).
- `src/llm_regression_observability/__init__.py` — version + metadata.
- `src/llm_regression_observability/cli.py` — argparse CLI exposing at least
  `check-env` and `version` subcommands; `main()` entry point.
- `src/llm_regression_observability/__main__.py` — `python3 -m` entry.
- `conftest.py` — path shim so tests run without an install.
- `tests/test_smoke.py`, `tests/test_scaffold_layout.py` — CPU-only tests
  (package imports, CLI `--help`/`version`, required directories exist).

## Validation

```bash
python3 -m pytest -q
PYTHONPATH=src python3 -m llm_regression_observability --help
python3 scripts/00_check_environment.py
```

## Rationale

The core package is intentionally dependency-free so default tests stay free,
CPU-only, and offline (the build host has little free disk and no guaranteed
network). `src/` + `conftest.py` keeps tests runnable with just an installed
`pytest`.

## Definition of done

Tests pass; `llmreg --help` works; environment check passes; committed tree is
clean. Next: Milestone 1 — core schemas and trace model.
