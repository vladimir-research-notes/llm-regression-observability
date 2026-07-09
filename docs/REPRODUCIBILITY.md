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

## End-to-end smoke

Steps 1–5 are implemented (Milestones 2, 4, 5 and 6); step 6 arrives with
Milestone 7.

```bash
# 1. validate fixtures
llmreg validate-fixtures evals/fixtures

# 2. baseline run (known-good, via recorded adapter)
llmreg run-suite --suite evals/suites/smoke.yaml --adapter recorded \
    --app-version baseline  --out results/runs/

# 3. candidate run (with injected regressions, via scripted adapter)
llmreg run-suite --suite evals/suites/smoke.yaml --adapter scripted-regression \
    --app-version candidate --out results/runs/

# 4. score each run independently        (no deltas, no flags — that is step 5)
python3 scripts/04_score_runs.py

# 5. compare: pair the runs, raise regression flags, cluster
llmreg compare --baseline results/runs/smoke-baseline-recorded \
    --candidate results/runs/smoke-candidate-scripted-regression \
    --out results/comparisons/

# 6. report                                         (Milestone 7, not yet built)
llmreg report --comparison results/comparisons/<comparison_id> \
    --out results/reports/regression_report.md
```

Step 5 detects six regressions and no false positives; add `--fail-on critical`
to turn it into a CI gate (exit 1), or `--dry-run` to compare without writing.

Without an editable install (the default on the build host), use the numbered
helper scripts, which prepend `src/` to `sys.path`:

```bash
python3 scripts/01_validate_fixtures.py evals/fixtures
python3 scripts/02_run_baseline.py     # -> results/runs/smoke-baseline-recorded/
python3 scripts/03_run_candidate.py    # -> results/runs/smoke-candidate-scripted-regression/
python3 scripts/04_score_runs.py       # -> results/processed/<run_id>/metrics.jsonl
python3 scripts/05_compare_runs.py     # -> results/comparisons/smoke-baseline-vs-candidate/
```

`02`, `03` and `05` accept extra flags verbatim, e.g. `--dry-run` (execute and
summarize, write nothing) or `--out /tmp/runs`; `04` accepts `--run`,
`--runs-root`, `--out`, and `--no-write`. `06_generate_report.py` lands with its
milestone. (`05_compare_runs.py` takes the `04` slot named in
`docs/milestones/06-comparison.md`; that number was already used by the
Milestone 5 scorer.)

Each run writes two artifacts, scoring writes a third, and the comparison writes
three more:

- `results/runs/<run_id>/run.jsonl` — one `RunRecord` per case, in suite order.
- `results/runs/<run_id>/run_meta.json` — identity, totals, the cost model
  applied, and the SHA-256 of `run.jsonl`.
- `results/processed/<run_id>/metrics.jsonl` — one `MetricResult` per
  (case, metric), sorted by metric name within each case.
- `results/comparisons/<comparison_id>/comparison.jsonl` — one `ComparisonRecord`
  per paired case.
- `results/comparisons/<comparison_id>/summary.json` — the `ReportSummary`: what
  regressed.
- `results/comparisons/<comparison_id>/comparison_meta.json` — the
  `ComparisonMeta`: unpaired cases, infra-attributed cases, withheld flags, the
  thresholds applied, and the SHA-256 of `comparison.jsonl`.

Generated `results/` contents are gitignored; regenerate them with the commands
above.

## Determinism guarantee

Re-running any step on the same inputs produces **byte-identical** artifacts
except for an explicit `run_id`. Timestamps and seeds are inputs, not ambient
values. This is what makes "the scripted regression is detected" a stable,
testable assertion rather than a flaky one.

To check it yourself:

```bash
python3 scripts/02_run_baseline.py --out results/processed/_check
diff -r results/runs/smoke-baseline-recorded \
        results/processed/_check/smoke-baseline-recorded

python3 scripts/04_score_runs.py --out results/processed/_check
diff -r results/processed/smoke-baseline-recorded \
        results/processed/_check/smoke-baseline-recorded

python3 scripts/05_compare_runs.py --out results/processed/_check
diff -r results/comparisons/smoke-baseline-vs-candidate \
        results/processed/_check/smoke-baseline-vs-candidate
```

`run_meta.json` carries `records_sha256`, which must equal
`sha256sum results/runs/<run_id>/run.jsonl`; `comparison_meta.json` carries the
same digest for its `comparison.jsonl`. Both writers assert it after every write,
and `tests/test_run_executor.py` / `tests/test_comparison.py` assert it
independently.

Latency is a **recorded input**, not a wall-clock measurement — timing the
adapter would make artifacts differ run to run. See
[`LIMITATIONS.md`](LIMITATIONS.md).

Metric values are rounded to six decimals (`cost_estimate_usd` to eight), so a
score cannot differ in the last bit of a float between platforms.
`tests/test_evaluators.py` asserts every fixture score against an exact literal —
if a metric definition drifts, a literal breaks.

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
agent failure, no new commit, dirty working tree, or a `pytest` failure.

**Permissions.** The runner is non-interactive but never uses
`--dangerously-skip-permissions`. `.claude/settings.json` (committed) allowlists
the safe dev commands the milestones need and denies network / `git push` /
`sudo` / installs / `rm -r*`; the runner defaults to `--permission-mode
acceptEdits` for file writes. Override with `CLAUDE_EXTRA_ARGS`; tune cost/turn
guards with `WEEKLY_BUDGET_USD` / `MAX_TURNS` (see the script header).
