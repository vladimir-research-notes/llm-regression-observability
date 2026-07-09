Human review: Milestone 6 — Baseline vs candidate comparison

TL;DR
Added `src/llm_regression_observability/comparison/`: seven modules that pair two
runs, diff every case, raise `RegressionFlag`s against suite thresholds, attribute
each degradation to app or infra, cluster correlated failures, and write three
byte-stable artifacts. On the six committed fixture suites the engine detects
**18 of 18** injected defects with the category *and* severity `expected.json`
annotates, and raises **0 false positives** on the 8 negative controls. `llmreg
compare` (with `--dry-run` and a `--fail-on` CI gate) and
`scripts/05_compare_runs.py` drive it. Full suite is 441 passed (96 new). Ruff
clean. Offline, dependency-free, deterministic. Nothing pushed.

What changed
- `comparison/base.py` — `ComparisonError`; `RunSide` (a loaded, scored side, built
  from records or from disk); `delta()` / `relative()`; `BEHAVIORAL_FIELDS` vs
  `IDENTITY_FIELDS` / `PROVENANCE_FIELDS`; `metric_value()` / `metric_detail()`
  (absent means not-applicable, never zero).
- `comparison/baseline.py` — `load_run_side()` / `load_baseline()`,
  `resolve_suite_path()`. Rejects a record stream whose `run_id` disagrees with
  `run_meta.json`.
- `comparison/candidate.py` — `load_candidate()`, `check_comparable()`,
  `Comparability`. Suite mismatch is fatal; a cost-model mismatch disables the cost
  rule and records why.
- `comparison/diff.py` — `pair_cases()` (total: `Pairing` carries both unpaired
  sets), `changed_fields()`, `CaseDiff` (per-metric deltas, dropped/new metrics, and
  the five pairwise signals), four `has_*_dimension` gates.
- `comparison/regression.py` — `Thresholds` (read from the suite manifest; unknown
  key is an error), the eight rule functions, `PROPERTY_CATEGORY` ownership table,
  `TRIGGER_METRICS`, `evaluate_flags()`, one flag per (case, category),
  `_finite()` guard against `inf`/`nan` reaching an artifact.
- `comparison/clustering.py` — `cluster_failures()` grouping by
  `(category, failure signature)`; content-derived `cluster_id`s.
- `comparison/aggregate.py` — `compare()`, `write_comparison()`, `compare_runs()`,
  `ComparisonMeta`, `ComparisonResult`; `ReportSummary` aggregation over paired
  cases only.
- `cli.py` — `llmreg compare --baseline --candidate [--out] [--comparison-id]
  [--dry-run] [--fail-on none|warn|critical]`.
- `scripts/05_compare_runs.py`, `evals/suites/smoke.yaml` (adds `cost_abs_usd`).
- `tests/test_comparison.py` — 96 CPU-only tests.
- Docs/site: milestone doc → Complete; `REGRESSION_DETECTION.md` rewritten to match
  the implementation; `RESULTS.md` gained the detection/false-positive/cluster
  tables; `TRACE_FORMAT.md` documents `ComparisonMeta`, `FailureCluster`, and the
  seven delta fields; `LIMITATIONS.md` gained three entries; `REPRODUCIBILITY.md`
  adds the compare step; `site/` index, architecture, trace-format,
  regression-categories, example-comparison, results, limitations and milestones
  pages updated.

Answers to the questions Milestones 4 and 5 left open
- **"Confirm the comparison engine does not treat `score_delta == 0` as 'no
  regression'."** It does not. All four `latency_cost` cases score `task_success`
  vacuously (their only property is pairwise), so `score_delta == 0.0` for every
  one — and `lc_latency_spike` / `lc_cost_spike` still flag, from `latency_ms` and
  `cost_estimate_usd`. Pinned by
  `test_a_zero_score_delta_does_not_mean_no_regression`.
- **"`field_diff_count` reports `extra` fields regardless of
  `additionalProperties` … Confirm."** Confirmed as a real false-positive risk, so
  it is **not** a trigger. A rise while `json_schema_validity` holds at 1.0 means
  the schema *permits* the field. An illegal extra field drops validity, which
  `schema_invalid` catches. Pinned by
  `test_a_permitted_extra_field_is_not_a_structured_output_regression`.
- **"The executor's `--continue-on-error` record … Milestone 6 has to decide whether
  to exclude them from aggregates or score them."** They are scored, and then their
  flags are withheld: the record's only error is `origin=infra`, so
  `errors.attribution()` returns `infra` and every category is suppressed and
  recorded. No special case was needed. Pinned by
  `test_an_adapter_failure_record_is_attributed_to_infra_and_raises_nothing`.
- **"`NEVER_FLAG_METRICS` is a contract, not a comment: Milestone 6 must consult
  it. Check that it lands there."** It lands twice: an import-time `AssertionError`
  in `regression.py` if `TRIGGER_METRICS` ever intersects it, and
  `test_no_rule_can_flag_on_a_never_flag_metric`.

What should be checked later
- **Infra attribution withholds *every* flag on the record, not just the error
  one.** A harness timeout inflates latency, fails tool calls, and truncates
  output; blaming the app for any of it would be wrong. The cost: a real regression
  co-occurring with an infra error on the same case is masked. It is recorded, not
  dropped (`comparison_meta.json` → `infra_attributed_case_ids`,
  `infra_suppressed_categories`) and documented in `LIMITATIONS.md`. **Decide
  whether the masking should instead be a `warn`-severity "unverifiable" marker.**
- **`final_answer_regression` fires only on answer-level property failures.**
  Without this attribution (`PROPERTY_CATEGORY`), all 18 fixture defects would raise
  a second, redundant flag, because `task_success` is the union of properties other
  scorers already own. The ownership table is a design decision, not a derived
  fact: `max_tool_errors` → `error_rate_regression` and `expected_query` →
  `tool_call_regression` are judgment calls. **Confirm the assignments.**
- **`at_failed_tool` is the one case that raises two flags** (`error_rate_regression`
  warn + `final_answer_regression` critical) and this is intended — its candidate
  both errors and answers "partial". But it means "flags" ≠ "regressed cases" in
  general (19 flags over 18 defect cases on the recorded pairs). Reports must not
  conflate the two.
- **`cost_abs_usd = 0.0005` is a new, invented default.** The other six thresholds
  came from `REGRESSION_DETECTION.md`. Half a tenth of a cent is defensible against
  an 8-decimal estimator, but it is not derived from anything. **Confirm, or set it
  from the cost model's resolution.**
- **A refusal-decision flip can flag without a declared expectation.**
  `rb_risk_label` has no `should_refuse`, so on the scripted path (`FLIP_REFUSAL`)
  the only signal is that the decision moved. The rule is gated on
  `risk_category == "refusal"` or a trace that explicitly records `refused`, so the
  weak lexical fallback cannot flag a benign case (`test_a_refusal_flip_outside_
  the_refusal_suite_does_not_flag`). Still, this is the one rule that fires on a
  *change* rather than a *worsening*. **Decide whether that belongs at `warn`.**
- **The scripted adapter's severities diverge from the recorded fixtures on four
  cases** (`tu_arg_drift`, `tu_order_change`, `re_missing_evidence`,
  `re_wrong_source`): its representative injector per category is harsher than the
  defect the fixture author wrote. Category recall is 18/18 on both paths. Pinned in
  `SCRIPTED_SEVERITY_DIVERGENCE`. **Decide whether Milestone 3's
  `_CATEGORY_DEFAULT` should become per-case instead of per-category.**
- **One flag per (case, category) loses the runners-up as structured data.** They
  survive only in the flag's `explanation` string, because `RegressionFlag` has no
  `detail` field. If Milestone 7's report wants to render "wrong tool *and* missing
  tool *and* sequence drift" as separate rows, the schema needs a field.
- **`ComparisonMeta` is a new artifact not named in `PLAN.md` §5.** No Milestone 1
  schema changed; the sidecar carries what `ReportSummary` has nowhere to put
  (unpaired cases, withheld flags, thresholds applied, comparability notes).
  Milestone 7 should read it. **Confirm the split.**
- **`scripts/05_compare_runs.py`, not `04_compare_runs.py`.** The `04` slot went to
  the Milestone 5 scorer, exactly as that milestone's review note predicted. The
  milestone spec's Deliverables list was updated to `05` and the deviation recorded
  in its "Deviations from this spec" section and in `REPRODUCIBILITY.md`.
- **`test_no_control_case_raises_a_flag_on_either_path` runs both candidate
  generators over all six suites** — the slowest test in the file (~0.4 s). It is
  the false-positive claim, so it is worth it.

Technical risks
- Low. Pure stdlib (`json`, `re`, `math`, `dataclasses`, `collections`, `pathlib`);
  no network, clock, RNG, paid API, or secrets. Nothing in `comparison/` imports
  `time`, `datetime`, or `random`.
- Two comparisons of the same two runs produce byte-identical
  `comparison.jsonl` / `summary.json` / `comparison_meta.json`
  (`test_two_comparisons_of_the_same_runs_are_byte_identical`), and
  `write_comparison()` re-hashes the file it just wrote against
  `ComparisonMeta.records_sha256`.
- `relative()` returns `inf` from a zero baseline. `_finite()` rejects it before it
  can reach a `RegressionFlag`, because `json.dumps(inf)` emits the non-standard
  `Infinity`; the latency and cost rules fall back to their absolute floors instead.
  `test_no_flag_serializes_a_non_finite_number` parses every artifact with a
  `parse_constant` hook that raises.
- `human_readable_summary` and every cluster label are built from metric names,
  numbers, tool names and property names — never from model-generated prose. That
  keeps them safe to print before the Milestone 9 redactor exists. `RunRecord` text
  fields are untouched by this milestone.
- Generated `results/comparisons/` contents stay gitignored; nothing large or
  private is committed.
- One pre-existing `ruff` E501 remains in `scripts/00_check_environment.py`
  (Milestone 0). Left alone: out of this milestone's scope.

Commands to verify
    python3 -m pytest -q                                   # 441 passed
    python3 -m pytest -q tests/test_comparison.py          # 96 passed
    python3 scripts/02_run_baseline.py                     # -> results/runs/...
    python3 scripts/03_run_candidate.py                    # -> results/runs/...
    python3 scripts/05_compare_runs.py                     # 6 flags, 0 false positives
    python3 scripts/05_compare_runs.py --fail-on critical  # exit 1 (CI gate)
    python3 scripts/05_compare_runs.py --out results/processed/_check
    diff -r results/comparisons/smoke-baseline-vs-candidate \
            results/processed/_check/smoke-baseline-vs-candidate   # byte-identical
    ruff check src tests scripts/05_compare_runs.py        # clean (dev extra)

Human feedback
<!-- Human will fill this later. -->
