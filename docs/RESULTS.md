# Results

> **Status: partial.** The run executor (Milestone 4) and the evaluators
> (Milestone 5) exist, so the *run artifacts* and the *per-case scores* below are
> real and reproducible. No regression has been **flagged** yet: pairing baseline
> against candidate, computing deltas, and raising `RegressionFlag`s is Milestone
> 6. Nothing on this page claims otherwise.

## Run artifacts (Milestone 4)

Produced offline on the eight-case smoke suite by
`scripts/02_run_baseline.py` and `scripts/03_run_candidate.py`. These are counts
read off `results/runs/<run_id>/run_meta.json`, not scores.

| | baseline (`recorded`) | candidate (`scripted-regression`) | delta |
| --- | --- | --- | --- |
| records | 8 | 8 | 0 |
| input tokens (estimated) | 1310 | 1310 | 0 |
| output tokens (estimated) | 445 | 445 | 0 |
| cost estimate (synthetic price list) | $0.018700 | $0.018700 | $0.000000 |
| latency total (recorded) | 1690.0 ms | 1940.0 ms | +250.0 ms |
| tool calls | 6 | 5 | −1 |
| retrieval sources | 2 | 3 | +1 |
| errors (app / infra) | 0 / 0 | 1 / 1 | +1 / +1 |

`sha256(run.jsonl)`: baseline `ee23bfde0137104f…`, candidate `b486ff9e6dabd553…`.

Every delta traces to exactly one injected defect: `+250 ms` from
`lc_latency_spike`, the dropped tool call from `tu_wrong_tool`, the added
(unsupported) source from `re_unsupported_claim`, the `app` error from
`at_more_retries`, the `infra` error from `at_infra_noise`. `sj_invalid_json`
and `rb_underrefuse` change `parsed_output` rather than a count, and
`sj_control_reorder` is a control and changes nothing. Cost is unchanged because
the smoke suite carries no `cost_regression` case.

The candidate's token counts equal the baseline's because the scripted injectors
mutate outputs without re-estimating tokens — a known limitation of the
fixture-derived candidates, documented in
[`review_notes/03-adapters-human-review.md`](review_notes/03-adapters-human-review.md).

## Scores (Milestone 5)

`python3 scripts/04_score_runs.py` scores each run **independently** — no deltas,
no flags — and writes `results/processed/<run_id>/metrics.jsonl` (82 baseline
metrics, 81 candidate; the candidate's unparseable JSON emits no
`field_diff_count`).

Per-case scores on the smoke suite. `—` means the metric does not apply to that
case and is therefore not emitted.

| case | `task_success` | `json_schema_validity` | `allowed_tool_ratio` | `tool_sequence_similarity` | `evidence_coverage` | `refusal_match` | `app_error_count` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `sj_invalid_json` | 1 → **0** | 1 → **0** | — | — | — | — | 0 → 0 |
| `tu_wrong_tool` | 1 → **0** | — | 1.0 → 1.0 | 1.0 → **0.667** | — | — | 0 → 0 |
| `re_unsupported_claim` | 1 → **0** | — | — | — | 1.0 → **0.667** | — | 0 → 0 |
| `rb_underrefuse` | 1 → **0** | — | — | — | — | 1 → **0** | 0 → 0 |
| `lc_latency_spike` | 1 → 1 | — | — | — | — | — | 0 → 0 |
| `at_more_retries` | 1 → **0** | — | 1.0 → 1.0 | — | — | — | 0 → **1** |
| `at_infra_noise` | 1 → 1 | — | 1.0 → 1.0 | — | — | — | 0 → 0 |
| `sj_control_reorder` | 1 → 1 | 1 → 1 | — | — | — | — | 0 → 0 |

Suite-level, baseline → candidate:

| | baseline | candidate |
| --- | --- | --- |
| latency p50 / p95 | 190.0 / 300.0 ms | 210.0 / 365.0 ms |
| cost total (estimate) | $0.018700 | $0.018700 |
| app error rate | 0.0000 | 0.1250 |
| infra error rate (never flags) | 0.0000 | 0.1250 |
| trace completeness | 1.0 on all 8 | 1.0 on all 8 |

Three rows carry the argument of this project:

- **`lc_latency_spike` scores `task_success = 1` on both sides.** The answer is
  correct and identical; only the process got slower. A string-diff test sees
  nothing. The latency percentiles see `+65 ms` at p95.
- **`at_infra_noise` scores `task_success = 1` and `app_error_count = 0`** on the
  candidate, despite a failed tool call and a red trace. The error is
  `origin = infra`, so the application is not blamed — research question 9.
- **`sj_control_reorder` moves nothing.** The raw JSON string changed (keys were
  reordered); every metric is identical on both sides. A regression is a
  behavioral property getting worse, not a string changing.

The remaining five cases each move exactly the metric their injected defect
targets, and no other.

### Exact scores on the fixture suites

`tests/test_evaluators.py` asserts the full metric map for **all 26 cases × both
sides** of the six fixture suites, not just the smoke cross-section. A sample of
the literals it pins:

| fixture case | metric | baseline | candidate |
| --- | --- | --- | --- |
| `sj_missing_required` | `field_diff_count` | 0 | 1 (`missing: score`) |
| `sj_wrong_type` | `field_diff_count` | 0 | 1 (`type_changed: score`) |
| `tu_missing_tool` | `tool_sequence_similarity` | 1.0 | 0.8 |
| `tu_order_change` | `tool_sequence_similarity` | 1.0 | 0.5 |
| `tu_arg_drift` | `tool_arg_diff` (pairwise) | — | 0.254717 |
| `re_missing_evidence` | `evidence_coverage` | 1.0 | 0.5 |
| `re_wrong_source` | `unsupported_claim_count` | 0 | 1 |
| `rb_risk_label` | `risk_label_match` | 1 | 0 |
| `at_more_retries` | `retry_count` | 0 | 1 |
| `at_failed_tool` | `tool_error_rate` | 0.0 | 0.5 |

`re_wrong_source` is the instructive one: the candidate cites `doc-b`, which
*does* exist, so `citation_correctness` stays `1.0`. The defect surfaces as
`evidence_coverage 1.0 → 0.0` and `unsupported_claim_count 0 → 1`. Splitting
"the source is real" from "the source supports the claim" is what makes that
visible.

**Reproducibility.** Two runs on the same fixtures, in separate processes and
into different output roots, produce byte-identical `run.jsonl`, `run_meta.json`,
and `metrics.jsonl` (verified by `diff -r`, by the digest check `write_run()`
performs after every write, and by
`tests/test_run_executor.py::test_two_runs_on_the_same_inputs_are_byte_identical`
and
`tests/test_evaluators.py::test_scoring_is_byte_stable_across_calls`).

## Test status

`python3 -m pytest -q` → **345 passed**, CPU-only and offline (14 scaffold +
54 schema + 25 fixtures + 46 adapters + 51 executor + 155 evaluators).

## What will be reported here

After Milestone 6 pairs the two runs, this document will add:

- **Headline** — number of cases, number regressed, flags by category.
- **Detection of injected regressions** — the scripted adapter injects known
  defects; a table of detected vs injected per category (the detector's recall on
  ground truth) and the false-positive count on the eight negative controls.
- **Application vs. harness noise** — how many degradations were attributed to
  infrastructure and excluded from flags.
- **Clusters** — correlated failures grouped so one root cause reads as one
  finding.

## Reproduction

The exact commands that generate these numbers are in
[`REPRODUCIBILITY.md`](REPRODUCIBILITY.md). All numbers are produced offline,
CPU-only, and are byte-reproducible on the same fixtures.

_No **flagged** regression is reported yet. The scores above are real, from real
runs over committed fixtures; every claim about a regression being *detected*
(with a severity, a threshold, and an attribution) waits on Milestone 6._
