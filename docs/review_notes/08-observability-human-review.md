Human review: Milestone 8 — Observability layer

TL;DR
Added `src/llm_regression_observability/observability/`: three modules that turn a
recorded run into (a) a nested, OpenTelemetry-*shaped* span tree and (b) a
suite-level summary that keeps **application scores, harness signals, and declared
placeholders in three separate maps**. `spans.py` projects `(EvalCase, RunRecord,
metrics)` into `request → model_call / tool_call / retrieval / evaluator` spans;
`metrics.py` names the suite metrics, their reducers (`mean`, `sum`, `p50`, `p95`,
`ratio`) and each one's signal class; `aggregation.py` reduces and writes
`results/runs/<run_id>/spans.jsonl` + `results/processed/<run_id>/aggregate.json`.
`llmreg aggregate` and `scripts/07_aggregate_runs.py` drive it. Full suite is 577
passed (70 new). Ruff clean apart from one pre-existing Milestone 0 line. Offline,
dependency-free (stdlib only, `statistics` and `hashlib` added), deterministic, no
clock anywhere. Nothing pushed.

What changed
- `observability/spans.py` — `Span` schema (`trace_id`, `span_id`, `parent_id`,
  `name`, `case_id`, `run_id`, `start_ms`, `end_ms`, `status`, `attributes`);
  content-addressed `trace_id_for()` / `span_id_for()`; `step_bounds()`;
  `build_case_spans()` / `build_run_spans()`; `validate_spans()` and `span_depths()`;
  `write_spans()` / `read_spans()`; `ObservabilityError`; `TIMING_SOURCES`.
- `observability/metrics.py` — `mean` / `total` / `p50` / `p95` / `nonzero_ratio`
  and the `REDUCERS` table; `MetricSpec` (source metric → suite metric, reducer,
  signal, decimals) and `SUITE_METRIC_SPECS` (28 suite metrics); `signal_of()`;
  `APPLICATION` / `HARNESS` / `PLACEHOLDER`.
- `observability/aggregation.py` — `SpanSummary`, `ReducedMetrics`, `RunAggregate`,
  `AggregateResult`; `reduce_metrics()`, `summarize_spans()`, `aggregate_run()`,
  `write_aggregate()`, `aggregate_run_dir()`.
- `cli.py` — `llmreg aggregate --run [--out] [--dry-run]`; `DEFAULT_AGGREGATE_OUT`.
- `scripts/07_aggregate_runs.py`.
- `tests/test_observability.py` — 70 CPU-only tests.
- Docs/site: `OBSERVABILITY.md` rewritten to describe what was built; milestone doc
  → Complete; `RESULTS.md`, `REPRODUCIBILITY.md`, `METRICS.md`, `TRACE_FORMAT.md`,
  `LIMITATIONS.md` updated; `site/` index, architecture, trace-format, metrics,
  results, limitations and milestones pages updated.

Decisions taken, each of which a reviewer may want to reverse
- **`llmreg aggregate` owns `spans.jsonl`, not `llmreg run-suite`.** An `evaluator`
  span needs a metric, and the executor has never scored anything. Because spans are
  a *pure projection* of `(case, record, metrics)`, deriving them one stage later
  costs nothing and buys one writer and a complete tree. `docs/PLAN.md` §4 puts
  `spans.jsonl` under `results/runs/<run_id>/`, which it is — but the diagram draws
  it coming out of the executor box. **Confirm the owner, or move the app-span subset
  into the executor and accept two writers.**
- **Child span timings are `derived`, not measured.** The trace records exactly one
  duration (`RunRecord.latency_ms`). The request span reproduces it; its application
  children get an equal partition of that interval in trace order. A derived width is
  `latency_ms / step_count` and means nothing on its own. **This is the single
  easiest thing in the repo to misread**, which is why `attributes["timing"]` labels
  every span and `test_derived_children_tile_the_request_interval` pins the tiling.
  **Decide whether a zero-width ordinal span would be more honest than an equal
  share** — it would break the "children tile the parent" invariant, which is why it
  was not chosen.
- **Child order is canonical, not observed.** `model_call`, then `tool_call`s, then
  `retrieval`s. The trace does not record cross-category interleaving, so a RAG trace
  (retrieve → generate) and an agent trace (generate → call → generate) get the same
  layout. **If Milestone 10's imported traces carry step ordering, `RunRecord` needs
  a step index and this layout should follow it.**
- **Evaluator spans are children of `request`, zero-width at its end.** They did not
  happen during the request — scoring is post hoc, in the harness. The alternative,
  a second root `evaluation` span per case, is arguably more correct and was rejected
  to keep the tree the one `docs/OBSERVABILITY.md` already drew. **Confirm.**
- **One evaluator span per *metric*, not per scorer module.** The doc's tree said
  "each scorer that ran"; a `MetricResult` does not name the module that emitted it,
  and a metric→module table would be a third place to keep in sync. The doc was
  updated to match. **Confirm.**

What should be checked later
- **`tool_error_rate_mean` sits in `application_metrics` and mixes origins.** On the
  smoke candidate it is `0.125`, raised equally by `at_more_retries` (app) and
  `at_infra_noise` (infra). It cannot cause a false regression — the flag rule reads
  `app_error_count`, never this — but the suite mean does blur the very distinction
  this milestone is about. Pinned by
  `test_tool_error_rate_mean_mixes_origins_and_is_pinned_here` and recorded in
  `LIMITATIONS.md` rather than quietly fixed. **Decide: make it origin-aware (split
  into `app_tool_error_rate` / `infra_tool_error_rate`), move it to
  `harness_metrics`, or leave it and keep the note.**
- **Spans carry symbols, and a symbol can still be a leak.** No span copies a prompt,
  an output, a tool argument *value*, a snippet, or an error *message* — asserted
  across every committed fixture record by
  `test_no_span_copies_free_text_out_of_the_trace`. But `tool_name` and `source_id`
  *are* copied, and a `tool_name` is model-chosen. Milestone 9's redactor should
  decide whether `spans.jsonl` is a redaction target. **Today no report reads spans,
  so nothing reaches a human document through this path — confirm that stays true.**
- **Milestone 7's note said `SECRET_PATTERNS` moves to `traces/redaction.py` in M9
  and "both callers must follow".** There are now potentially three consumers of
  "what may be copied": the fixture validator, the report gate, and (if it becomes a
  redaction target) the span writer. **One table, three callers.**
- **`aggregate.json` is a fourth artifact not named in `PLAN.md` §5.** Same situation
  `ComparisonMeta` was in after Milestone 6. It is the only place the three signal
  maps and `metric_coverage` exist. **Confirm the artifact, or fold it into
  `run_meta.json` and give the executor a scoring dependency it currently does not
  have.**
- **`metrics_sha256` digests a stream this stage does not write.** It equals the
  SHA-256 of `results/processed/<run_id>/metrics.jsonl`, which
  `scripts/04_score_runs.py` writes independently — asserted by
  `test_the_metrics_digest_matches_the_metrics_file_score_runs_writes`. Two writers
  for one path was the alternative. **Confirm the cross-check beats the convenience.**
- **`incomplete_trace_rate` is computed in `aggregation.py`, not by a reducer.** The
  spec named five reducers and none of them expresses "fraction of cases whose
  `passed` is false". Adding a sixth reducer that reads `MetricResult.passed` rather
  than `.value` would have widened the `Reducer` type for one metric. **Confirm the
  special case, or widen the type.**
- **No suite metric reduces `tool_arg_diff` or `risk_label_delta`**, because both are
  pairwise (baseline against candidate) and a single run cannot emit them.
  `test_no_suite_metric_reduces_a_pairwise_metric` and
  `test_every_per_case_metric_the_fixtures_emit_has_a_suite_metric` pin both
  directions, so a new per-case metric without a spec fails a test. **This is the
  guard against a metric being scored and then silently vanishing from the suite.**
- **Nothing consumes the observability layer yet.** The comparison engine (M6) and
  the report (M7) predate it and still compute their own `latency_stats` /
  `cost_stats`. `aggregate.json` is written and read by tests, and by a human. **A
  later milestone should decide whether the report's latency/cost tables should be
  sourced from `aggregate.json` instead — and whether a run's harness metrics belong
  in the report's "what the engine declined to say" section.**
- **`scripts/07_aggregate_runs.py`, numbered after the report script**, even though
  aggregation runs before comparison in the pipeline. The `04`–`06` slots were taken
  by earlier milestones. The number is a filename, not an order; `REPRODUCIBILITY.md`
  says so.

Technical risks
- Low. Pure stdlib (`dataclasses`, `pathlib`, `hashlib`, `statistics`,
  `collections.abc`, `typing`); no network, clock, RNG, paid API, or secrets. Two AST
  scans enforce it: `test_no_ambient_time_randomness_or_network_in_the_span_path`
  (no `time`, `datetime`, `random`, `secrets`, `socket`, `urllib`, `http`,
  `requests`) and `test_the_observability_package_is_dependency_free` (every
  top-level import is in an allowlist of eight stdlib modules).
- `aggregate.json` carries no timestamp, so two aggregations of one run are
  byte-identical — verified in-process
  (`test_two_aggregations_of_the_same_run_are_byte_identical`) and by re-running
  `python3 scripts/07_aggregate_runs.py` plus `diff`.
- `validate_spans()` runs on the write path *and* the read path, so an invalid tree is
  never written and a corrupt `spans.jsonl` fails at load rather than halfway down a
  consumer. Six violations get a test each: escaping child, missing parent, cycle,
  duplicate id, backwards interval, gap between derived siblings.
- Span ids are content-addressed SHA-256 prefixes (16 hex for spans, 32 for traces).
  They are **not** random and are not intended to be globally unique across
  repositories — two runs with the same `run_id` and `case_id` produce the same trace
  id, which is the point (reproducibility), not a bug.
- `spans.jsonl` for the smoke suite is ~106 lines and stays gitignored with the rest
  of `results/`.
- One pre-existing `ruff` E501 remains in `scripts/00_check_environment.py`
  (Milestone 0). Left alone: out of this milestone's scope.

Commands to verify
    python3 -m pytest -q                                    # 577 passed
    python3 -m pytest -q tests/test_observability.py        # 70 passed
    python3 scripts/02_run_baseline.py                      # -> results/runs/...
    python3 scripts/03_run_candidate.py
    python3 scripts/07_aggregate_runs.py                    # -> spans.jsonl + aggregate.json
    python3 scripts/07_aggregate_runs.py --dry-run          # summarizes, writes nothing

    # determinism, across processes:
    python3 scripts/07_aggregate_runs.py --out results/processed/_check
    diff results/processed/smoke-baseline-recorded/aggregate.json \
         results/processed/_check/smoke-baseline-recorded/aggregate.json

    # the digest cross-check between two independent writers:
    python3 scripts/04_score_runs.py
    sha256sum results/processed/smoke-baseline-recorded/metrics.jsonl
    python3 -c "import json;print(json.load(open('results/processed/smoke-baseline-recorded/aggregate.json'))['metrics_sha256'])"

    # the separation the milestone exists for:
    python3 -c "import json;a=json.load(open('results/processed/smoke-candidate-scripted-regression/aggregate.json'));print(a['application_metrics']['error_rate'], a['harness_metrics']['infra_error_rate'], a['infra_attributed_case_ids'])"

    ruff check src tests scripts/07_aggregate_runs.py       # clean (dev extra)

Human feedback
<!-- Human will fill this later. -->
