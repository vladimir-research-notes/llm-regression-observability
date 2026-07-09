Human review: Milestone 5 — Evaluators

TL;DR
Added `src/llm_regression_observability/evaluators/`: ten deterministic scorer
modules plus `base.py` (rounding, metric metadata) and `registry.py`
(`evaluate_case` / `evaluate_run`). Every scorer is a pure function of
`(EvalCase, RunRecord)` — no clock, no RNG, no network, no learned model — so
`tests/test_evaluators.py` asserts **exact** expected scores for all 26 fixture
cases × both sides, not approximate ones. `scripts/04_score_runs.py` scores the
Milestone 4 run artifacts into `results/processed/<run_id>/metrics.jsonl`. Full
suite is 345 passed (155 new). Ruff clean. Offline, dependency-free,
deterministic. Nothing pushed.

What changed
- `evaluators/base.py` — `rounded()` / `ratio()` / `metric()`, `parse_json()`,
  `canonical_json()`, `sequence_ratio()`; `METRIC_DIRECTION` (higher/lower/neutral
  per metric), `PLACEHOLDER_METRICS`, `NEVER_FLAG_METRICS`, `EvaluatorError`.
- `evaluators/exact.py` — `PROPERTY_CHECKS` (15 structural assertions),
  `check_properties()`, `task_success`, opt-in `exact_match`,
  `final_output_stable()` (pairwise helper for Milestone 6).
- `evaluators/json_schema.py` — a stdlib JSON Schema validator over
  `SUPPORTED_KEYWORDS`, `unsupported_keywords()`, `field_diff()`; emits
  `json_parse_success`, `json_schema_validity`, `field_diff_count`.
- `evaluators/tool_calls.py` — `allowed_tool_ratio`, `tool_sequence_similarity`,
  `missing_tools`, `extra_tools`, `tool_arg_diff`; `sequence_similarity()`,
  `argument_distance()`, `tool_argument_diff()`, `tool_arg_diff_between()`.
- `evaluators/evidence.py` — `citation_presence`, `citation_correctness`,
  `evidence_coverage`, `unsupported_claim_count`.
- `evaluators/refusal.py` — `refusal_match`, `risk_label_match`,
  `risk_label_delta()` (pairwise), `explanation_quality_placeholder`,
  `refusal_decision()` with a labelled lexical fallback.
- `evaluators/latency.py` — `latency_ms`, `percentile()` (type-7),
  `latency_stats()`.
- `evaluators/cost.py` — `cost_estimate_usd`, `cost_stats()`.
- `evaluators/errors.py` — `app_error_count`, `infra_error_count`, `retry_count`,
  `tool_error_rate`; `attribution()`, `classify_errors()`, `error_rate()`,
  `infra_error_rate()`, `retry_total()`.
- `evaluators/completeness.py` — `trace_completeness` over a named checklist
  (9 base + 2 conditional checks).
- `evaluators/semantic_stub.py` — `semantic_similarity_stub()`,
  `output_similarity()`. Declared placeholders.
- `evaluators/registry.py` — `EVALUATORS`, `evaluate_case()`, `evaluate_run()`,
  `metrics_by_name()`.
- `scripts/04_score_runs.py` — scores run artifacts, prints a per-case table,
  writes `results/processed/<run_id>/metrics.jsonl`.
- `tests/test_evaluators.py` — 155 CPU-only tests.
- Docs/site: milestone doc → Complete; `docs/METRICS.md` rewritten to match the
  implementation; `docs/RESULTS.md` gained the per-case score tables;
  `docs/LIMITATIONS.md` gained four entries (placeholders, recorded-not-judged
  evidence support, advisory properties, the JSON Schema subset);
  `docs/REPRODUCIBILITY.md` adds the scoring step and renumbers the planned
  compare/report scripts; `site/` metrics, index, milestones, results and
  limitations pages updated.

What should be checked later
- **Scripts renumbered.** `04_score_runs.py` takes the number
  `docs/REPRODUCIBILITY.md` previously reserved for `04_compare_runs.py`. The
  planned compare/report helpers are now `05_compare_runs.py` and
  `06_generate_report.py`. **Confirm** before Milestone 6 writes its script, or
  rename this one.
- **`task_success` is vacuously 1.0 for two fixture suites.** `latency_cost` (all
  four cases) and `retrieval_evidence/re_wrong_source` declare only pairwise or
  advisory properties, so nothing is checked and `passed` is `None`. That is the
  honest reading, but it means `score_delta` in Milestone 6 will be `0.0` for
  those cases and the regression must come from another metric (latency
  percentiles; `evidence_coverage` / `unsupported_claim_count`). **Confirm the
  comparison engine does not treat `score_delta == 0` as "no regression".**
- **One fixture property is unchecked.** `expected_source_for_color`
  (`re_wrong_source`) is prose, not a structural assertion, so it lands in
  `detail["advisory"]`. A test pins the advisory set to exactly that one property,
  so this cannot grow silently — but the alternative was to encode a
  `expected_source_for_claim: {claim: source_id}` property and edit a Milestone 2
  fixture. Deferred deliberately. Decide whether the fixture should change.
- **`citation_presence` is 1.0 on every committed fixture.** With the current
  trace model an uncited claim is only representable as a `RetrievalTrace` with an
  empty `source_id`, which no fixture does. The metric is exercised by a synthetic
  test, not by the fixtures. It exists for imported traces (Milestone 10). Decide
  whether a fixture should carry an uncited claim.
- **`refusal_decision()` has a lexical fallback.** When `parsed_output["refused"]`
  is absent it matches a ten-entry substring list against `final_output` and
  labels the result `decision_source = "lexical_fallback"`. No fixture takes that
  path. It is approximate by construction and will misread "I can't confirm that,
  but here is the answer". Milestone 10's importers are where it matters.
- **`retry_count` is structural.** A retry is a call repeating the *name and
  arguments* of an earlier **failed** call. This ignores the error `kind` string,
  which is right for imported traces, but it will not count a retry that varied a
  nonce or a request id in its arguments. Worth revisiting if a real trace format
  does that.
- **`trace_completeness` denominators vary per case** (9 base checks, plus
  `tool_calls_present` when the case declares `allowed_tools` and
  `retrieval_sources_present` when it declares `retrieval_docs`). So `1.0` means
  "complete for this case", and two cases' completeness scores are comparable only
  as ratios. All 52 committed fixture records score exactly `1.0`.
- **The executor's `--continue-on-error` record scores `trace_completeness =
  0.777778`** (no latency, no output tokens) and `app_error_count = 0`. That
  answers the question the Milestone 4 review left open — such records are
  *visibly* incomplete rather than silently zero — but Milestone 6 still has to
  decide whether to **exclude** them from aggregates or score them. The signal is
  now available either way.
- **`json_schema_validity` uses a hand-written validator.** It implements the 14
  constraint keywords in `SUPPORTED_KEYWORDS` and reports anything else in
  `detail["unsupported_keywords"]`. It has no `$ref`, `allOf`, `oneOf`, or
  `format`. A test asserts the committed fixture schemas stay inside the subset.
  If a real schema arrives via Milestone 10, this is the first thing that breaks —
  loudly, by design.
- **`field_diff_count` reports `extra` fields regardless of
  `additionalProperties`.** It is a diff, not a verdict; legality is
  `json_schema_validity`'s answer. A schema that permits extra properties will
  therefore show a nonzero `field_diff_count` for a legal document. Milestone 6
  flags structured-output regressions on validity, per
  `docs/REGRESSION_DETECTION.md`, so this is currently harmless. Confirm.
- **Percentiles are type-7 (linear interpolation).** `latency.percentile()` is
  pinned and documented rather than delegated to `statistics.quantiles`, which
  cannot answer p95 on a sample of eight. Different percentile conventions give
  different p95s; the threshold in `docs/REGRESSION_DETECTION.md` is only
  meaningful against this one.

Technical risks
- Low. Pure stdlib (`json`, `difflib`, `re`, `dataclasses`, `collections`,
  `functools`/`ast` in tests); no network, clock, RNG, paid API, or secrets.
  Enforced rather than asserted:
  `test_no_ambient_time_randomness_or_network_in_scored_paths` parses each of the
  thirteen evaluator modules' ASTs and fails if any imports `time`, `datetime`,
  `random`, `secrets`, `socket`, `urllib`, `http`, or `requests`.
- No evaluator mutates its inputs (`test_scoring_does_not_mutate_the_case_or_the_record`
  compares `to_json()` before and after).
- `NEVER_FLAG_METRICS` is a contract, not a comment: Milestone 6 must consult it.
  Nothing enforces that yet — the enforcement belongs in the comparison engine's
  own tests. **Check that it lands there.**
- `evaluate_run()` raises on a record whose `case_id` names no case, so an
  unscored record cannot silently vanish from an aggregate.
- Generated `results/processed/` contents stay gitignored; nothing large or
  private is committed.

Commands to verify
    python3 -m pytest -q                                   # 345 passed
    python3 -m pytest -q tests/test_evaluators.py          # 155 passed
    python3 scripts/01_validate_fixtures.py evals/fixtures # PASS (unchanged)
    python3 scripts/02_run_baseline.py                     # -> results/runs/...
    python3 scripts/03_run_candidate.py                    # -> results/runs/...
    python3 scripts/04_score_runs.py                       # -> results/processed/...
    python3 scripts/04_score_runs.py --out results/processed/_check
    diff -r results/processed/smoke-baseline-recorded \
            results/processed/_check/smoke-baseline-recorded   # byte-identical
    ruff check src tests conftest.py scripts/04_score_runs.py  # clean (dev extra)

Human feedback
<!-- Human will fill this later. -->
