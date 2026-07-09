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

All seven steps are implemented (Milestones 2, 4, 5, 6, 7 and 8).

```bash
# 1. validate fixtures
llmreg validate-fixtures evals/fixtures

# 2. baseline run (known-good, via recorded adapter)
llmreg run-suite --suite evals/suites/smoke.yaml --adapter recorded \
    --app-version baseline  --out results/runs/

# 3. candidate run (with injected regressions, via scripted adapter)
llmreg run-suite --suite evals/suites/smoke.yaml --adapter scripted-regression \
    --app-version candidate --out results/runs/

# 4. score each run independently        (no deltas, no flags — that is step 6)
python3 scripts/04_score_runs.py

# 5. observability: project spans, reduce to suite metrics (per run)
llmreg aggregate --run results/runs/smoke-baseline-recorded
llmreg aggregate --run results/runs/smoke-candidate-scripted-regression

# 6. compare: pair the runs, raise regression flags, cluster
llmreg compare --baseline results/runs/smoke-baseline-recorded \
    --candidate results/runs/smoke-candidate-scripted-regression \
    --out results/comparisons/

# 7. report: render the comparison as Markdown (and/or self-contained HTML)
llmreg report --comparison results/comparisons/smoke-baseline-vs-candidate \
    --out results/reports/regression_report.md --format both
```

Step 5 writes `spans.jsonl` next to `run.jsonl` and `aggregate.json` under
`--out` (default `results/processed`). It keeps **application scores**, **harness
signals** (`trace_completeness`, `infra_error_rate`) and declared **placeholders**
in three separate maps; `--dry-run` summarizes without writing. It is independent
of steps 6–7: the comparison engine does not read its artifacts.

Step 6 detects six regressions and no false positives; add `--fail-on critical`
to turn it into a CI gate (exit 1), or `--dry-run` to compare without writing.

Step 7 renders those records. `--top N` bounds the worst-regressions table (`0`
shows every flag); `--dry-run` renders and runs the redaction gate without writing.
Free text is masked on the way in; the report is refused rather than written if any
credential-like string survives — which can only happen in an identifier, because the
redactor does not rewrite join keys. See [`SAFETY.md`](SAFETY.md).

Redaction is not a step of its own: it runs inside step 4 (before `run.jsonl` is
hashed), step 5 (before `spans.jsonl` is written) and step 7 (before and after the
report is rendered). The one command it does add is a repository scan:

```bash
llmreg scan-secrets                    # or: python3 scripts/08_check_repo_hygiene.py
```

Without an editable install (the default on the build host), use the numbered
helper scripts, which prepend `src/` to `sys.path`:

```bash
python3 scripts/01_validate_fixtures.py evals/fixtures
python3 scripts/02_run_baseline.py     # -> results/runs/smoke-baseline-recorded/
python3 scripts/03_run_candidate.py    # -> results/runs/smoke-candidate-scripted-regression/
python3 scripts/04_score_runs.py       # -> results/processed/<run_id>/metrics.jsonl
python3 scripts/07_aggregate_runs.py   # -> spans.jsonl + results/processed/<run_id>/aggregate.json
python3 scripts/05_compare_runs.py     # -> results/comparisons/smoke-baseline-vs-candidate/
python3 scripts/06_generate_report.py  # -> results/reports/regression_report.{md,html}
python3 scripts/08_check_repo_hygiene.py  # exits 1 on any committed credential
```

`02`, `03`, `05` and `06` accept extra flags verbatim, e.g. `--dry-run` (execute
and summarize, write nothing) or `--out /tmp/runs`; `04` accepts `--run`,
`--runs-root`, `--out`, and `--no-write`; `07` accepts `--run` / `--runs-root` and
forwards the rest to `llmreg aggregate`. A script's number is a filename, not a
pipeline position: each sits one slot later than its milestone doc names it, from
`05` onward (`04_score_runs.py` (Milestone 5) took the `04` slot that
`docs/milestones/06-comparison.md` wanted for the compare script, and the shift
carried into `06_generate_report.py`), and `07_aggregate_runs.py` (Milestone 8)
ran out of slots entirely although aggregation happens before comparison.

## All of it at once

```bash
python3 scripts/10_run_all_gates.py            # the ten gates of PLAN.md §11
python3 scripts/10_run_all_gates.py --json     # deterministic, diffable
python3 scripts/10_run_all_gates.py --only report --keep-workdir
```

Exits 1 if any required gate fails; `lint` is the one optional gate, because
`ruff` is a dev extra. `--only` pulls in a gate's prerequisites, so `--only report`
runs the whole pipeline that produces one. Selecting `tests` from inside `pytest`
raises rather than forking the suite into itself.

The gate run writes **nothing into the repository**: the pipeline gates run the
commands above into a scratch directory outside the tree (`--workdir` must be
outside it, and the script only ever removes a directory it created). That is what
lets the `clean_tree` gate inspect the repository rather than the leavings of the
gate before it. `--json` carries no clock and no scratch path, so two runs on one
commit are byte-identical.

Two claims that gate run makes good on, which a weaker check would fake:
`smoke_offline` executes the run scripts in a child interpreter whose
`socket.connect`, `connect_ex` and `create_connection` raise — so a run that
reached for the network fails even on a connected host; and `redaction` plants a
synthetic credential in a scratch file and requires the scanner to find *that*
before it accepts an empty finding list for the repository, because an inert
scanner reports zero findings on every tree.

Each run writes two artifacts, scoring writes a third, aggregation writes two more,
the comparison writes three, and the report writes one per format:

- `results/runs/<run_id>/run.jsonl` — one `RunRecord` per case, in suite order.
- `results/runs/<run_id>/run_meta.json` — identity, totals, the cost model
  applied, and the SHA-256 of `run.jsonl`.
- `results/processed/<run_id>/metrics.jsonl` — one `MetricResult` per
  (case, metric), sorted by metric name within each case.
- `results/runs/<run_id>/spans.jsonl` — the span tree: one `request` span per case
  with its `model_call` / `tool_call` / `retrieval` / `evaluator` children. Written
  by `llmreg aggregate`, not by `run-suite`: an `evaluator` span needs a metric.
- `results/processed/<run_id>/aggregate.json` — the `RunAggregate`: suite metrics
  split into `application_metrics`, `harness_metrics` and `placeholder_metrics`,
  plus `metric_coverage`, the span summary, and the cases whose traces were
  incomplete or infra-attributed.
- `results/comparisons/<comparison_id>/comparison.jsonl` — one `ComparisonRecord`
  per paired case.
- `results/comparisons/<comparison_id>/summary.json` — the `ReportSummary`: what
  regressed.
- `results/comparisons/<comparison_id>/comparison_meta.json` — the
  `ComparisonMeta`: unpaired cases, infra-attributed cases, withheld flags, the
  thresholds applied, and the SHA-256 of `comparison.jsonl`.
- `results/reports/regression_report.md` / `.html` — the rendered report. The HTML
  is one self-contained file: no stylesheet, script, image, or web font is
  requested over the network.

Generated `results/` contents are gitignored; regenerate them with the commands
above. A committed copy of the Markdown report lives at
[`examples/regression_report.md`](examples/regression_report.md); a test
regenerates it and compares byte for byte, so it cannot drift from the code.

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
diff results/processed/smoke-baseline-recorded/metrics.jsonl \
     results/processed/_check/smoke-baseline-recorded/metrics.jsonl

python3 scripts/07_aggregate_runs.py --out results/processed/_check
diff results/processed/smoke-baseline-recorded/aggregate.json \
     results/processed/_check/smoke-baseline-recorded/aggregate.json

python3 scripts/05_compare_runs.py --out results/processed/_check
diff -r results/comparisons/smoke-baseline-vs-candidate \
        results/processed/_check/smoke-baseline-vs-candidate

python3 scripts/06_generate_report.py --out results/processed/_check/report.md
diff results/reports/regression_report.md results/processed/_check/report.md
diff results/reports/regression_report.html results/processed/_check/report.html
```

The report carries **no generation timestamp** — a pure function of the comparison
artifacts. Provenance comes from `records_sha256` and the `llmreg` version stamped
into `comparison_meta.json`, both printed in the report's header table.

`run_meta.json` carries `records_sha256`, which must equal
`sha256sum results/runs/<run_id>/run.jsonl`; `comparison_meta.json` carries the
same digest for its `comparison.jsonl`. Both writers assert it after every write,
and `tests/test_run_executor.py` / `tests/test_comparison.py` assert it
independently.

`aggregate.json` carries two digests. `spans_sha256` is the digest of the
`spans.jsonl` it just wrote. `metrics_sha256` digests the metric stream it reduced,
which is exactly the `metrics.jsonl` that `scripts/04_score_runs.py` writes
independently — two stages, one number, so they can be checked against each other
rather than trusted:

```bash
sha256sum results/processed/smoke-baseline-recorded/metrics.jsonl
python3 -c "import json; print(json.load(open('results/processed/smoke-baseline-recorded/aggregate.json'))['metrics_sha256'])"
```

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
