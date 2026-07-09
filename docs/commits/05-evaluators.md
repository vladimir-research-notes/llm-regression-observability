# Milestone 5 — Evaluators

## What changed
The scoring layer: a `RunRecord` plus its `EvalCase` now produce `MetricResult`s
along every dimension of the regression taxonomy. Milestone 6 consumes these.

- **`src/llm_regression_observability/evaluators/`** — new package, thirteen
  modules:
  - `base.py` — `rounded()` / `ratio()` / `metric()`, `parse_json()`,
    `canonical_json()`, `sequence_ratio()`, `EvaluatorError`; and the metric
    metadata tables `METRIC_DIRECTION`, `PLACEHOLDER_METRICS`,
    `NEVER_FLAG_METRICS`.
  - `exact.py` (`task_success`, opt-in `exact_match`), `json_schema.py`
    (parse success, schema validity, field diff), `tool_calls.py` (allowed-tool
    ratio, sequence similarity, missing/extra tools, argument diff),
    `evidence.py` (citation presence/correctness, coverage, unsupported claims),
    `refusal.py` (refusal match, risk label, explanation placeholder),
    `latency.py` (per-case latency, p50/p95), `cost.py` (per-case estimate, suite
    total), `errors.py` (app/infra classification, retries, tool error rate),
    `completeness.py` (trace completeness), `semantic_stub.py` (a declared
    stand-in, not a judge).
  - `registry.py` — `evaluate_case()` / `evaluate_run()` / `metrics_by_name()`.
- **`scripts/04_score_runs.py`** — scores `results/runs/<run_id>/run.jsonl`,
  prints a per-case table, writes `results/processed/<run_id>/metrics.jsonl`.
  Independent per run: no deltas, no flags — that is `llmreg compare`.
- **`tests/test_evaluators.py`** — 155 CPU-only tests, built around an `EXPECTED`
  table of exact scores for all 26 fixture cases × baseline and candidate.
- Docs/site: milestone doc → Complete; `docs/METRICS.md` rewritten against the
  implementation; `docs/RESULTS.md` gained per-case score tables;
  `docs/LIMITATIONS.md` gained four entries; `docs/REPRODUCIBILITY.md` adds the
  scoring step (and renumbers the planned compare/report helpers); `site/`
  metrics, index, milestones, results and limitations pages updated.

## Why / key decisions
- **A metric that cannot be computed is not emitted.** Scoring a case with no
  `json_schema` as `json_schema_validity == 0` would invent a failure. Each
  evaluator names its precondition and stays silent otherwise, so an absent metric
  means *not applicable*, never *zero*. The sharpest case: `field_diff_count` is
  silent when the output did not parse, because a field diff against an object
  that does not exist is a parse failure — counting both would make one defect
  look like two.
- **Direction is a table, not a convention.** `METRIC_DIRECTION` records whether
  each metric is better high, better low, or neither, so the comparison engine can
  flag "worse" without a hard-coded sign per metric. `NEVER_FLAG_METRICS` names
  what may not raise a regression at all: the two placeholders, and the infra
  counters. A test asserts every metric emitted on the fixtures declares a
  direction.
- **Placeholders are named, scored, and defanged.** `semantic_similarity_stub` is
  lexical token overlap; `explanation_quality_placeholder` always emits `0.0`.
  Both carry `detail["placeholder"] = true`, `passed = None`, and membership in
  `NEVER_FLAG_METRICS`. Inventing a semantic score would have been the one
  dishonest thing this milestone could do.
- **Properties a single record cannot answer are named, not dropped.**
  `expected_properties` is a closed vocabulary; anything outside it is *advisory*
  and reported in `detail`, and `final_output_stable` is *pairwise* and answered
  by `exact.final_output_stable(baseline, candidate)`. A case with nothing
  checkable scores `task_success = 1.0` with `passed = None` and
  `detail["vacuous"]`: nothing failed, and nothing was tested. A test pins the
  exact advisory set the fixtures produce (`{expected_source_for_color}`), so a
  new unchecked property cannot appear silently.
- **Wrong-source ≠ fake-source.** `citation_correctness` asks whether a cited
  `source_id` exists; `unsupported_claim_count` asks whether the snippet supports
  the claim. `re_wrong_source` cites a *real* document for the *wrong* claim, so
  correctness stays `1.0` and coverage falls `1.0 → 0.0`. Splitting the two is
  what makes the defect visible.
- **Trace completeness is a harness signal, not an application score.** A
  candidate whose JSON genuinely broke has `parsed_output = null` — a correct
  trace of a real failure, `trace_completeness = 1.0`. The executor's
  `--continue-on-error` record has no latency and no output tokens —
  `trace_completeness = 0.777778`. Keeping them apart is what lets Milestone 6
  exclude harness failures instead of scoring their zeros. This answers the
  question the Milestone 4 review note left open.
- **Attribution is asymmetric on purpose.** `errors.attribution()` returns `infra`
  only when a record errored and *every* error was infra-origin. One app error
  makes the whole record `app`. Under uncertainty it flags, because a missed
  regression costs more than a noisy one. `at_infra_noise` is the proof: a failed
  tool call, a red trace, `task_success = 1.0`, `app_error_count = 0`.
- **The JSON Schema validator is a documented subset, and says so.** The core is
  dependency-free, so `jsonschema` is not an option. `SUPPORTED_KEYWORDS` lists
  the 14 constraint keywords implemented; anything else lands in
  `detail["unsupported_keywords"]` rather than being silently ignored, because
  ignoring a keyword lets an invalid document score as valid. A test asserts every
  fixture schema stays inside the subset.
- **The percentile method is pinned.** `statistics.quantiles` cannot answer p95 on
  a sample of eight, and different conventions give different answers, so
  `latency.percentile()` implements type-7 (linear interpolation between closest
  ranks) explicitly. A threshold is only meaningful if p95 means one thing.
- **Values are rounded at the boundary.** Six decimals (`cost_estimate_usd`:
  eight), so `2/3` is `0.666667` and a `MetricResult` serializes to identical
  bytes anywhere. `tests/test_evaluators.py` can then assert `== 0.666667` rather
  than `pytest.approx`.

## Validation run
- `python3 -m pytest -q` → **345 passed** (14 scaffold + 54 schema + 25 fixtures +
  46 adapters + 51 executor + 155 evaluators).
- `python3 -m pytest -q tests/test_evaluators.py` → 155 passed.
- `python3 scripts/01_validate_fixtures.py evals/fixtures` → PASS (unchanged).
- `ruff check src tests conftest.py scripts/04_score_runs.py` → clean (optional
  dev extra).
- `python3 scripts/04_score_runs.py` → baseline 82 metrics, candidate 81 (the
  unparseable candidate emits no `field_diff_count`); latency p50/p95
  `190.0/300.0 ms` → `210.0/365.0 ms`; app error rate `0.0` → `0.125`; infra error
  rate `0.0` → `0.125`; `trace_completeness = 1.0` on all sixteen records.
- Cross-process determinism: re-scoring into a second output root in a fresh
  process; `diff -r` reports no difference.
- Every one of the 26 fixture cases is asserted against an exact score map on both
  sides. Each of the eighteen injected defects moves exactly the metric it targets.
  Seven of the eight negative controls score *identically* baseline vs candidate;
  the eighth, `at_infra_noise`, is infra-attributed — its trace really does change
  (a tool call fails), so `tool_error_rate` moves `0.0 → 0.5` while `task_success`
  stays `1.0` and `app_error_count` stays `0`. That distinction is the point of
  the case, and of `errors.attribution()`.

Next: Milestone 6 — the comparison engine (pair by `case_id`, compute deltas,
raise `RegressionFlag`s with severity and app/infra attribution, cluster).
