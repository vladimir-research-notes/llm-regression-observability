# Reproducibility

Everything here runs **CPU-only and offline** on a stock Python 3.12. The build
target is an AWS `m5.2xlarge`, but nothing requires that specific machine.

## Environment

- Python **3.12** (`python3`; there is no bare `python` on the target host).
- The **core package needs no third-party packages** — Python standard library
  only. This is intentional: the build host has minimal free disk and no
  guaranteed network access.
- Tests run with `pytest` (already available on the target host). If missing:
  `pip install --user pytest`.
- Optional, never required: `ruff` (lint), `pydantic` (alt. schema validation),
  `pandas` (ad-hoc summaries). Install only if you want them:
  `pip install --user ruff pydantic pandas`.

## Preflight

```bash
python3 scripts/00_check_environment.py
```

Checks the Python version, required standard-library modules, tooling
availability (reported, not required), free disk, and that no network is needed.
Add `--json` for machine-readable output.

## Run the tests

```bash
python3 -m pytest -q
```

The suite is CPU-only and offline. No test performs network I/O or calls a paid
API.

## End-to-end smoke (after the relevant milestones are implemented)

```bash
# 1. validate fixtures
llmreg validate-fixtures evals/fixtures

# 2. baseline run (known-good, via recorded adapter)
llmreg run-suite --suite evals/suites/smoke.yaml --adapter recorded \
    --app-version baseline  --out results/runs/

# 3. candidate run (with injected regressions, via scripted adapter)
llmreg run-suite --suite evals/suites/smoke.yaml --adapter scripted-regression \
    --app-version candidate --out results/runs/

# 4. compare
llmreg compare --baseline results/runs/<baseline_run_dir> \
    --candidate results/runs/<candidate_run_dir> --out results/comparisons/

# 5. report
llmreg report --comparison results/comparisons/<comparison_id> \
    --out results/reports/regression_report.md
```

Before the CLI exists (early milestones), the same steps are available as the
numbered helper scripts in `scripts/` (`02_run_baseline.py`,
`03_run_candidate.py`, `04_compare_runs.py`, `05_generate_report.py`), added in
their respective milestones.

## Determinism guarantee

Re-running any step on the same inputs produces **byte-identical** artifacts
except for an explicit `run_id`. Timestamps and seeds are inputs, not ambient
values. This is what makes "the scripted regression is detected" a stable,
testable assertion rather than a flaky one.

## The docs website

```bash
./scripts/serve_site.sh        # http://localhost:9001/index.html
# stop with: kill $(cat logs/serve_site.pid)
```

## Milestone execution (autonomous)

```bash
./scripts/run_all_milestones_with_claude.sh          # milestones 0..13
./scripts/run_all_milestones_with_claude.sh 5 7      # a subrange
```

The runner drives one milestone per `claude -p` invocation and stops on any of:
agent failure, no new commit, dirty working tree, or a `pytest` failure. See the
script header for cost-guard and permission options.
