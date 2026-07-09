# Milestone 8 — Observability layer

## What changed
A run is now inspectable as a nested span tree and reducible to suite-level
metrics — with the **harness kept out of the application's scores**. This is the
mechanism behind research question 9 (`docs/PLAN.md` §2): a degraded harness must
surface as a telemetry problem, not as a false regression.

- **`src/llm_regression_observability/observability/`** — new package, three
  modules:
  - `spans.py` — the `Span` schema and the projection. `build_case_spans()` turns
    `(EvalCase, RunRecord, metrics)` into `request → model_call / tool_call /
    retrieval / evaluator`, with content-addressed ids (`span_id =
    sha256(run_id | case_id | path)[:16]`). `validate_spans()` enforces the tree
    invariants on both the write and the read path; `write_spans()` / `read_spans()`
    persist it. `ObservabilityError` on anything that is not a tree.
  - `metrics.py` — the reducers (`mean`, `sum`, `p50`, `p95`, `ratio`) and
    `SUITE_METRIC_SPECS`: 28 suite metrics, each naming its source metric, its
    reducer, and its **signal class** (`application` / `harness` / `placeholder`).
    `MetricSpec.direction` inherits from `evaluators.base.METRIC_DIRECTION`.
  - `aggregation.py` — `reduce_metrics()`, `summarize_spans()`, `aggregate_run()`,
    `aggregate_run_dir()`; the `RunAggregate` and `SpanSummary` schemas; writes
    `results/runs/<run_id>/spans.jsonl` and
    `results/processed/<run_id>/aggregate.json`.
- **`cli.py`** — `llmreg aggregate --run [--out] [--dry-run]`.
- **`scripts/07_aggregate_runs.py`** — both smoke runs, no editable install required.
- **`tests/test_observability.py`** — 70 CPU-only tests.
- Docs/site: `OBSERVABILITY.md` rewritten around what was built; milestone doc →
  Complete; `RESULTS.md`, `REPRODUCIBILITY.md`, `METRICS.md`, `TRACE_FORMAT.md` and
  `LIMITATIONS.md` updated; `site/` index, architecture, trace-format, metrics,
  results, limitations and milestones pages updated.

## Why / key decisions
- **Spans are derived, not emitted.** Nothing here instruments a live call — there
  is no live call. The projection is a pure function of the artifact on disk, so the
  span tree is rebuildable at any time and byte-identical every time. That is the
  property that lets spans be a *derived* artifact instead of a side effect, and it
  is why ids are content-addressed rather than random: a span id is a function of
  *where the span sits*, never of when it was generated.
- **`llmreg aggregate` owns `spans.jsonl`, not the executor.** An `evaluator` span
  needs a metric, and the executor has never scored anything. Deriving the tree one
  stage later costs nothing (the projection is pure) and buys one writer and a
  complete tree instead of two writers and two partial ones.
- **Only one duration was ever recorded, and the spans say so.** `RunRecord.latency_ms`
  is the trace's single timing. The `request` span reproduces it
  (`timing="recorded"`); its application children get a contiguous, non-overlapping
  partition of that interval in trace order (`timing="derived"`); evaluator spans are
  zero-width at the request's end (`timing="none"`), because scoring happens
  afterwards, in the harness, and is not part of the application's latency. A derived
  width is `latency_ms / step_count` and means nothing on its own. Inventing per-step
  durations was the easy lie available here; labelling every span with its timing
  source and testing the tiling is what was done instead. Cross-category interleaving
  is not recorded either, so the child order is documented as **canonical, not
  observed**.
- **Spans carry symbols, never free text.** An attribute holds an identifier
  (`tool_name`, `source_id`, `metric`, `error_kind`), a count, a status, or a
  boolean — never a prompt, an output, a tool argument *value*, a snippet, or an
  error *message*. Redaction lands in Milestone 9; until a redactor has seen a
  string, no telemetry artifact may copy it. Tool argument **keys** are carried
  because they are schema, not content. A test scans every attribute of every
  committed fixture record against the free text of the record it came from.
- **Span status is about the operation, not the score.** An `evaluator` span that
  reports `passed=false` has `status="ok"`: the scorer ran fine and found a real
  failure. Errors attach to the `request` span with their origin and kinds, because
  `ErrorRecord` names no step and inventing a parent for it would be a guess the
  trace cannot support.
- **The summary has three maps, not one.** `application_metrics` are scores for the
  system under test; `harness_metrics` (`trace_completeness`, `infra_error_rate`,
  `infra_error_total`, `incomplete_trace_rate`) describe whether the *observation*
  can be trusted; `placeholder_metrics` are computed and claimed for nothing. A flat
  map lets a broken exporter and a broken model look alike at a glance, so the
  separation is **structural** rather than a naming convention. A test asserts no
  metric in `NEVER_FLAG_METRICS` reaches the application map.
- **A reducer is named, not assumed.** `ratio` is the fraction of cases with a
  non-zero value, which is what makes `app_error_count → error_rate` mean "how many
  cases errored" rather than "how many errors, averaged": two errors on one case out
  of eight is a rate of `0.125` and a mean of `0.25`. `p50`/`p95` reuse the type-7
  percentile already pinned in `evaluators/latency.py`.
- **Direction is inherited, never restated.** Every reducer is monotone, so
  `MetricSpec.direction` reads `METRIC_DIRECTION` instead of duplicating it. A second
  table that could silently disagree with the first never exists.
- **`metric_coverage` counts contributing cases.** `evidence_coverage_mean = 1.0`
  over 1 of 8 cases is a fact about one case, and a summary that does not say so is
  inviting a misreading.
- **An absent metric yields no suite metric.** Consistent with Milestone 5: absent is
  *not applicable*, never zero.
- **A pairwise metric gets no suite metric.** `tool_arg_diff` and `risk_label_delta`
  compare two records; a single run cannot emit them. Two tests pin both directions,
  so a new per-case metric without a spec fails rather than silently vanishing from
  the suite.
- **A known blur is pinned, not hidden.** `tool_error_rate` is descriptive — an infra
  timeout fails a tool call too — so `tool_error_rate_mean` sits in
  `application_metrics` while mixing origins (`0.125` on the smoke candidate, raised
  equally by an app failure and an infra one). It cannot cause a false regression, as
  the flag rule reads `app_error_count` and never this. It gets a test and a line in
  `LIMITATIONS.md` rather than a quiet fix.
- **`metrics_sha256` digests a stream this stage does not write.** It equals the
  SHA-256 of the `metrics.jsonl` that `scripts/04_score_runs.py` writes
  independently, so the two artifacts can be checked against each other instead of
  two writers racing for one path.

## Validation run
- `python3 -m pytest -q` → **577 passed** (6 scaffold + 8 smoke + 54 schema +
  25 fixtures + 46 adapters + 51 executor + 155 evaluators + 96 comparison +
  66 reports + 70 observability).
- `python3 -m pytest -q tests/test_observability.py` → 70 passed.
- `ruff check src tests scripts` → one pre-existing E501 in
  `scripts/00_check_environment.py` (Milestone 0); nothing new.
- `python3 scripts/07_aggregate_runs.py` → baseline: 106 spans over 8 traces, depth
  2, 0 error spans, 1690.0 ms recorded; candidate: 105 spans, 4 error spans
  (2 `request`, 2 `tool_call`), 1940.0 ms recorded.
- The separation the milestone exists for: on the candidate,
  `application_metrics.error_rate = 0.125` (from `at_more_retries`) and
  `harness_metrics.infra_error_rate = 0.125` (from `at_infra_noise`), with
  `infra_attributed_case_ids = ["at_infra_noise"]`. Neither number contains the
  other's case.
- Harness degradation is not a regression: zeroing one record's recorded latency
  drives `incomplete_trace_rate` to `0.125` and `trace_completeness` below `1.0`
  while `success_rate` stays at `1.0`
  (`test_a_degraded_trace_surfaces_as_a_harness_finding_not_a_regression`).
- Cross-process determinism: aggregating into a second output root in a fresh process
  produces byte-identical `spans.jsonl` and `aggregate.json`.
- Structural validation: an escaping child, a missing parent, a cycle, a duplicate
  span id, a backwards interval and a gap between derived siblings each get a test
  asserting the refusal — on the write path *and* the read path.
- Two AST scans: no module under `observability/` imports `time`, `datetime`,
  `random`, `secrets`, `socket`, `urllib`, `http` or `requests`, and every top-level
  import is in an eight-module stdlib allowlist.

Next: Milestone 9 — redaction and safety hygiene.
