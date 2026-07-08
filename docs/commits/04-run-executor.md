# Milestone 4 — Run-suite executor

## What changed
The "traces in, traces out" core: a suite plus an adapter now produces
reproducible `RunRecord` artifacts on disk. Everything downstream (evaluators,
comparison, reports, observability) reads these two files.

- **`src/llm_regression_observability/executor.py`** — new module:
  - `RunConfig` (suite path, adapter, `app_version`, optional identity overrides,
    cost model, `recompute_cost`, `strict`), `RunMeta` (the `run_meta.json`
    sidecar), `RunResult`, `CostModel`, `ExecutorError`.
  - `execute_suite()` — pure: cases → adapter → re-stamped, accounted records.
    `write_run()` — persists. `run_suite()` — load, build, execute, write;
    `out_root=None` is the dry-run path.
  - `build_adapter()` maps the CLI's `--adapter` name onto Milestone 3's
    adapters; `api-stub` is constructible but raises before any network call.
- **`src/llm_regression_observability/traces/`** — new package. `storage.py`
  centralizes byte-stable persistence: sorted keys, compact JSONL separators,
  `ensure_ascii=False`, explicit UTF-8 and `\n` (including a trailing one),
  schema-validating readers that report `path:lineno`, and `sha256` helpers.
- **`datasets/loader.py`** — `load_eval_suite()` resolves a manifest's
  `"<suite>/<case_id>"` references into an ordered `EvalSuite`, failing loudly on
  an unknown suite, a dangling reference, or a duplicated case.
- **`cli.py`** — `llmreg run-suite`, with `--dry-run`, `--recompute-cost`,
  `--continue-on-error`, identity overrides, and cost-model flags.
- **`scripts/02_run_baseline.py`, `scripts/03_run_candidate.py`** — thin wrappers
  over the CLI, runnable without an editable install.
- **`tests/test_run_executor.py`** — 51 CPU-only tests.
- Docs/site: milestone doc → Complete; `docs/TRACE_FORMAT.md` gained `RunMeta`
  and the identity-vs-provenance rule; `docs/RESULTS.md` reports its first real
  (non-detection) numbers; `docs/LIMITATIONS.md` gained the recorded-latency and
  synthetic-price-list entries; `docs/REPRODUCIBILITY.md` now shows the working
  steps; `site/` overview, architecture, trace-format, results and milestones
  pages updated.

## Why / key decisions
- **The executor owns run identity; the adapter owns provenance.** Milestone 3's
  recorded adapter replays fixtures *verbatim*, so each record arrives carrying
  its own `run_id` (`<case>-baseline`) and recorded `timestamp`. The Milestone 3
  review note explicitly deferred re-identification to here. The executor forces
  `run_id` and `app_version` onto every record — making `(run_id, case_id)` the
  artifact key and `ComparisonRecord.baseline_run_id` mean exactly one run — and
  leaves `model_id` / `prompt_version` / `tool_version` / `timestamp` as the
  adapter reported them unless the caller overrides them, recording which fields
  were forced in `RunMeta.identity_overrides`.
- **A synthetic run names its adapter, not a model it never called.** Absent
  `--model-id`, `model_id` becomes `scripted-regression-adapter`, not
  `mock-model`. The recorded adapter keeps its recorded `recorded-mock-v1`.
- **Latency is recorded, never measured.** Timing the adapter would put an
  ambient value into a scored field; byte-identical artifacts and non-flaky
  latency assertions both depend on not doing it. The real cost of this choice is
  written down in `docs/LIMITATIONS.md` rather than glossed.
- **Tokens/cost are derived only where the adapter left zeros**, and the artifact
  says how often that happened (`tokens_estimated_cases`, `cost_estimated_cases`).
  Recorded fixture values are inputs, not defaults to overwrite. `--recompute-cost`
  exists for the one case where overwriting is the point: pricing two runs under
  a single cost model.
- **Byte-stability lives in one module.** `traces/storage.py` is the only place
  that decides how JSON reaches disk, so "two runs are byte-identical" is a
  property of one function rather than a convention every caller must remember.
- **`run_id` is validated as a path segment.** It names a directory, so
  `--run-id ../../etc` raises `ExecutorError` instead of writing outside `--out`.
- **Adapter failure is infra, not app.** With `--continue-on-error` a failing
  case yields a record carrying an `origin="infra"` `ErrorRecord`, so a broken
  harness can never be counted as an `error_rate_regression` (research question 9).
- **The determinism rule is now enforced by a test, not a habit.**
  `test_no_ambient_time_randomness_or_network_in_scored_paths` parses each new
  module's AST and fails if it imports `time`, `datetime`, `random`, `secrets`,
  `socket`, `urllib`, `http`, or `requests`. Parsing the AST rather than grepping
  the text means prose *about* `datetime.now()` does not trip it.

## Validation run
- `python3 -m pytest -q` → **190 passed** (14 scaffold + 54 schema + 25 fixtures +
  46 adapters + 51 executor).
- `python3 -m pytest -q tests/test_run_executor.py` → 48 passed.
- `python3 scripts/01_validate_fixtures.py evals/fixtures` → PASS (unchanged).
- `ruff check src tests conftest.py` → clean (optional; ruff is a dev extra).
- `python3 scripts/02_run_baseline.py` → 8 records, 1310/445 tokens, $0.018700,
  1690.0 ms, 6 tool calls, 2 sources, 0 errors, `sha256 ee23bfde…`.
- `python3 scripts/03_run_candidate.py` → 8 records, 1940.0 ms, 5 tool calls,
  3 sources, 2 errors (1 app, 1 infra), `sha256 b486ff9e…`.
- Cross-process determinism: both scripts re-run into a second output root in
  fresh processes; `diff -r --exclude=.gitkeep` reports no difference.
- `--adapter api-stub` exits 1 with the "disabled by default" message, before any
  network or paid-API call.

Next: Milestone 5 — evaluators (deterministic scorers with exact expected
scores), which consume `results/runs/<run_id>/run.jsonl`.
