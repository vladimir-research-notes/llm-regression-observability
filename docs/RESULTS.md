# Results

> **Status: the detector works, on synthetic ground truth, and reports what it
> found.** Milestones 4–9 are implemented, so the run artifacts, the per-case
> scores, the **regression flags**, the **rendered report**, the **spans and
> suite metrics**, and the **redaction** in front of all of them below are real and
> reproducible. On the six committed fixture suites the comparison engine detects
> **18 of 18 injected defects** with the category and severity their ground truth
> annotates, and raises **0 false positives** on the 8 negative controls.
> Everything on this page is measured on synthetic fixtures and does not
> generalize to production traffic — see [`LIMITATIONS.md`](LIMITATIONS.md).

## Headline: detection on ground truth (Milestone 6)

Each fixture suite ships `expected.json` — an oracle stating, per case, which
regression (if any) a correct detector must raise, at which severity, attributed
to app or infra. `tests/test_comparison.py` asserts the engine against it.

| | recorded candidate | scripted candidate |
| --- | --- | --- |
| injected defects | 18 | 18 |
| detected with the annotated category | **18** | **18** |
| detected with the annotated category *and* severity | **18** | 14 |
| missed | 0 | 0 |
| negative controls | 8 | 8 |
| false positives on controls | **0** | **0** |

Two candidate generators, two purposes. The **recorded** candidate is the
committed `data/fixtures/<suite>/candidate.jsonl` — the defect the fixture author
wrote, and the oracle's exact referent. The **scripted** candidate is built at run
time by `ScriptedRegressionAdapter`, which injects one *representative* defect per
regression category into the clean baseline.

The four severity divergences on the scripted path are a property of that adapter,
not a detector failure: its representative injector is harsher than the recorded
defect for `tu_arg_drift`, `tu_order_change` (it drops a tool rather than drifting
an argument or reordering) and for `re_missing_evidence`, `re_wrong_source` (it
fabricates a claim rather than dropping a citation). Category recall is 18/18 on
both paths. The divergence is pinned in
`tests/test_comparison.py::SCRIPTED_SEVERITY_DIVERGENCE` rather than described.

### Flags raised on the recorded fixture pairs

19 flags across 18 defect cases — one case raises two, and it should.

| Category | Flags | Cases |
| --- | --- | --- |
| `structured_output_regression` | 4 | `sj_invalid_json`, `sj_missing_required`, `sj_wrong_type`, `sj_extra_field` |
| `tool_call_regression` | 4 | `tu_wrong_tool`, `tu_missing_tool`, `tu_arg_drift`, `tu_order_change` |
| `evidence_grounding_regression` | 3 | `re_unsupported_claim`, `re_missing_evidence`, `re_wrong_source` |
| `refusal_behavior_shift` | 3 | `rb_overrefuse`, `rb_underrefuse`, `rb_risk_label` |
| `latency_regression` | 1 | `lc_latency_spike` |
| `cost_regression` | 1 | `lc_cost_spike` |
| `error_rate_regression` | 2 | `at_more_retries`, `at_failed_tool` |
| `final_answer_regression` | 1 | `at_failed_tool` |

`at_failed_tool` is the only case with two flags. Its candidate's `compute` tool
fails (`error_rate_regression`, warn) **and** its answer degrades from "done" to
"partial" (`final_answer_regression`, critical). The fixture note says exactly
that. Two things went wrong; clustering reports them as two findings on one case.

Every other defect raises exactly one flag, because a `task_success` drop is
attributed to the category that owns the property that failed. Without that rule
each of the 18 defects would also report a `final_answer_regression` — see
[`REGRESSION_DETECTION.md`](REGRESSION_DETECTION.md), "One defect, one signal".

### The three rows that carry the argument

- **`lc_latency_spike` scores `task_success = 1` on both sides.** The answer is
  correct and byte-identical; only the process got slower (150 → 420 ms). A
  string-diff test sees nothing. The engine raises `latency_regression`.
- **`at_infra_noise` has a failed tool call and a red trace, and raises nothing.**
  Its error is `origin=infra`, so the application is not blamed. The case is named
  in `comparison_meta.json` under `infra_attributed_case_ids`, not dropped —
  research question 9.
- **`sj_control_reorder` changes `final_output` and regresses nothing.** The JSON
  keys were reordered; every metric is identical on both sides;
  `changed_fields = ["final_output"]` and `regression_flags = []`. A regression is
  a behavioral property getting worse, not a string changing.

### Clustering

16 clusters over the 19 flags on the recorded pairs. Grouping is by
`(category, failure signature)`; the signature carries the offending symbol.

| Cluster | Cases |
| --- | --- |
| `structured_output_regression__schema_invalid` | `sj_extra_field`, `sj_missing_required`, `sj_wrong_type` |
| `structured_output_regression__parse_failure` | `sj_invalid_json` |
| `evidence_grounding_regression__coverage_drop` | `re_missing_evidence`, `re_wrong_source` |
| `tool_call_regression__disallowed_tool-shell_exec` | `tu_wrong_tool` |
| `tool_call_regression__missing_tool-lookup` | `tu_missing_tool` |
| `error_rate_regression__app_error-tool_retry` | `at_more_retries` |
| `error_rate_regression__app_error-tool_error` | `at_failed_tool` |
| `final_answer_regression__property-final_answer` | `at_failed_tool` |

Three cases that each fail schema validation are one finding, not three — even
though they fail different keywords (`required`, `type`, `additionalProperties`),
which the flag explanation names. A parse failure is a *different* root cause from
a validity failure, so `sj_invalid_json` clusters alone. `at_failed_tool` appears
in two clusters: it really did fail in two ways.

## The smoke pipeline, end to end

`scripts/02_run_baseline.py` → `scripts/03_run_candidate.py` →
`scripts/04_score_runs.py` → `scripts/05_compare_runs.py`, all offline.

The eight-case smoke suite is the cross-section where the scripted adapter's
injected defect and the fixture's recorded one agree, so category **and** severity
are both asserted:

| case | flag | severity |
| --- | --- | --- |
| `sj_invalid_json` | `structured_output_regression` | critical |
| `tu_wrong_tool` | `tool_call_regression` | critical |
| `re_unsupported_claim` | `evidence_grounding_regression` | critical |
| `rb_underrefuse` | `refusal_behavior_shift` | critical |
| `lc_latency_spike` | `latency_regression` | warn |
| `at_more_retries` | `error_rate_regression` | warn |
| `at_infra_noise` | — (infra-attributed, surfaced not flagged) | — |
| `sj_control_reorder` | — (control) | — |

6 cases regressed, 6 flags (4 critical, 2 warn), 0 false positives, 1
infra-attributed case, 6 clusters. Suite-level: latency p50 `+20.0 ms`, p95
`+65.0 ms`, cost total delta `$0.000000` (the smoke suite carries no
`cost_regression` case, and the scripted injectors mutate outputs without
re-estimating tokens).

`llmreg compare --fail-on critical` exits 1 here (4 critical flags), which is the
CI regression gate. The default `--fail-on none` exits 0 and reports.

## Run artifacts (Milestone 4)

Counts read off `results/runs/<run_id>/run_meta.json`, not scores.

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

Every delta traces to exactly one injected defect: `+250 ms` from
`lc_latency_spike`, the dropped tool call from `tu_wrong_tool`, the added
(unsupported) source from `re_unsupported_claim`, the `app` error from
`at_more_retries`, the `infra` error from `at_infra_noise`. `sj_invalid_json`
and `rb_underrefuse` change `parsed_output` rather than a count, and
`sj_control_reorder` is a control and changes nothing.

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

### Exact scores on the fixture suites

`tests/test_evaluators.py` asserts the full metric map for **all 26 cases × both
sides** of the six fixture suites. A sample of the literals it pins:

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
visible — and comparing claim *texts* is what keeps it a `warn` (the claim lost
its support) rather than the `critical` reserved for a claim the model invented.

**Reproducibility.** Two runs on the same fixtures, in separate processes and
into different output roots, produce byte-identical `run.jsonl`, `run_meta.json`,
`metrics.jsonl`, `spans.jsonl`, `aggregate.json`, `comparison.jsonl`,
`summary.json`, `comparison_meta.json`, and `regression_report.md` / `.html`
(verified by the digest check each writer performs, and by
`tests/test_run_executor.py::test_two_runs_on_the_same_inputs_are_byte_identical`,
`tests/test_evaluators.py::test_scoring_is_byte_stable_across_calls`,
`tests/test_comparison.py::test_two_comparisons_of_the_same_runs_are_byte_identical`,
`tests/test_reports.py::test_writing_twice_produces_byte_identical_files`, and
`tests/test_observability.py::test_two_aggregations_of_the_same_run_are_byte_identical`).

## The report (Milestone 7)

`python3 scripts/06_generate_report.py` renders the comparison above into
`results/reports/regression_report.md` and `.html`. A copy of the Markdown is
committed at [`examples/regression_report.md`](examples/regression_report.md), and
`tests/test_reports.py::test_the_committed_example_report_is_not_stale` regenerates
it from the fixtures and compares byte for byte — a sample report in the docs that
drifts from what the code emits is a claim nobody re-checks.

The document opens with the verdict and closes with what it could not see:

| Section | What it answers |
| --- | --- |
| Verdict + provenance table | Did anything regress? Against which two runs, under which digest? |
| Regressions by category | What kind, how many flags, how many cases, worst severity — all eight categories, silent ones included |
| Worst regressions | Severity-ordered flags with metric, observed, threshold, and the rule's explanation |
| Representative examples | One worked case each for structured output, tool-call diff, evidence grounding |
| Structured-output validity | `json_validity_delta` per case that moved or flagged |
| Latency / Cost | p50/p95 and totals, **plus every case that moved without flagging** |
| Failure clusters | Correlated failures as one finding each |
| What the engine declined to say | Unpaired cases, infra-attributed cases, withheld flags, comparability notes, thresholds applied |
| Limitations | What the report cannot see |

On the smoke comparison that is: verdict `REGRESSED`, 6 flags over 6 of 8 paired
cases (4 critical, 2 warn), 6 clusters, `lc_latency_spike` in the latency table at
`+250.0 ms`, `at_infra_noise` in the disclosures as *surfaced, excluded from
flags* — and nowhere in the worst-regressions table.

Three properties, each with a test:

- **Deterministic.** No clock, no RNG, no generation timestamp. Two renders of one
  comparison are byte-identical, and an AST scan fails if any module under
  `reports/` imports `time`, `datetime`, `random`, `secrets`, `socket`, `urllib`,
  `http`, or `requests`.
- **Redaction-masked, then gated fail-closed.** Free text is masked on the way into
  the report model (Milestone 9); the fully rendered document is then scanned for
  credential-like strings immediately before it is written, and one that survived —
  necessarily in an identifier the redactor will not rewrite — is refused, not
  written. The error names the pattern and line without echoing the value. Tested by
  splicing a fake key into a copy of the smoke comparison twice: into a flag
  explanation, where the write succeeds and the document holds `[REDACTED:openai_key]`;
  and into a run id, where the write *and* the `--dry-run` both refuse.
- **Sourced only from the artifacts.** The report reads `comparison.jsonl`,
  `summary.json` and `comparison_meta.json`, and nothing else. Every string in it
  is a case id, a metric name, a threshold, or a rule-authored explanation. No
  model-generated output text is copied into it.

The HTML rendering is one self-contained file: no `<link>`, no `<script>`, no
`<img>`, no `url()`, no external host. A report attached to a pull request should
not tell a third party that someone is reading it.

## Observability (Milestone 8)

`python3 scripts/07_aggregate_runs.py` projects each run into a span tree and
reduces it to suite metrics, writing `results/runs/<run_id>/spans.jsonl` and
`results/processed/<run_id>/aggregate.json`.

| | baseline (`recorded`) | candidate (`scripted-regression`) |
| --- | --- | --- |
| spans / traces / depth | 106 / 8 / 2 | 105 / 8 / 2 |
| `request` | 8 | 8 (**2 error**) |
| `model_call` | 8 | 8 |
| `tool_call` | 6 | 5 (**2 error**) |
| `retrieval` | 2 | 3 |
| `evaluator` | 82 | 81 |
| recorded duration (sum of `request` spans) | 1690.0 ms | 1940.0 ms |

The counts are ground truth, not a smoke check: `tu_wrong_tool` drops a tool call
(6 → 5), `re_unsupported_claim` adds an uncited source (2 → 3), and the candidate's
unparseable JSON emits one metric fewer, hence one `evaluator` span fewer. The
recorded duration equals `run_meta.json`'s `latency_ms_total` — two independent
paths to one number.

### The separation this milestone exists for

`aggregate.json` keeps three maps. Nothing in `NEVER_FLAG_METRICS` reaches the
application map; a test asserts it.

| | baseline | candidate |
| --- | --- | --- |
| `application_metrics.success_rate` | 1.0 | **0.375** |
| `application_metrics.error_rate` | 0.0 | **0.125** |
| `application_metrics.latency_p50 / p95` | 190.0 / 300.0 ms | 210.0 / 365.0 ms |
| `harness_metrics.trace_completeness` | 1.0 | 1.0 |
| `harness_metrics.infra_error_rate` | 0.0 | **0.125** |
| `harness_metrics.incomplete_trace_rate` | 0.0 | 0.0 |
| `placeholder_metrics.explanation_quality_mean` | 0.0 | 0.0 |

Two cases errored on the candidate, and they are counted in **different maps**.
`at_more_retries` is a genuine application error and moves `error_rate` alone.
`at_infra_noise` has a failed tool call and a red `request` span, and moves
`infra_error_rate` alone; it is named in `infra_attributed_case_ids`. A single flat
map would have shown "2 errors out of 8" and let a broken harness look exactly like
a broken model.

Harness degradation is not a regression, and there is a test for that too: zeroing
one record's recorded latency drives `incomplete_trace_rate` to `0.125` and
`trace_completeness` below `1.0` while `success_rate` stays at `1.0`.

`metric_coverage` records how many cases answered each metric, because
`evidence_coverage_mean = 1.0` over **1 of 8 cases** is a fact about one case.

### What a span does and does not claim

The trace records exactly one duration per case, `RunRecord.latency_ms`. So the
`request` span's interval is `recorded`; its children get a contiguous partition of
that interval in trace order, labeled `derived`, which asserts **ordering** and not
**duration**; `evaluator` spans are zero-width at the request's end, labeled `none`,
because scoring happens afterwards in the harness. A derived width is
`latency_ms / step_count` and means nothing on its own.

Spans carry identifiers, counts and statuses — `tool_name`, `source_id`, `metric`,
`error_kind`, argument **keys** — and never a prompt, an output, a tool argument
value, a snippet, or an error message. A test scans every span attribute of all 52
committed fixture records against the free text of the record it came from.

## Test status

`python3 -m pytest -q` → **577 passed**, CPU-only and offline (6 scaffold +
8 smoke + 54 schema + 25 fixtures + 46 adapters + 51 executor + 155 evaluators +
96 comparison + 66 reports + 70 observability).

## What is not claimed

- These are **synthetic fixtures with injected defects**. 18/18 recall on a suite
  built to be detected is evidence that the rules fire as specified, not evidence
  of recall on real regressions.
- The eight controls test the specific false positives the fixtures encode (a key
  reorder, sub-threshold drift, harness noise). They are not a false-positive rate
  on real traffic.
- No live model was called. No cost was incurred. Latency is recorded, not
  measured.

## Redaction and safety hygiene (Milestone 9)

`traces/redaction.py` holds the one pattern table and the masking redactor. It runs
before the executor hashes a run, before the report generator renders, and before the
span writer writes. Twelve rules across two tiers; see [`SAFETY.md`](SAFETY.md).

| Property | Result |
| --- | --- |
| Committed fixture records unchanged by redaction | **52 / 52**, all `clean` |
| Smoke pipeline byte-identical to before the milestone | yes (`run.jsonl`, `run_meta.json`, `regression_report.md`) |
| `llmreg scan-secrets` on this repository | **0 findings** |
| `redaction_status` values reachable and tested | 3 / 3 (`clean`, `redacted`, `flagged`) |
| Tests | 73 in `tests/test_redaction.py` (74 new; 651 total) |

The first row is the load-bearing one. Redaction rewrites `final_output` *before* an
evaluator ever reads it, so a false-positive mask would silently change a score. That
all 52 records pass through untouched is what says the rules are tuned for this
corpus; the assertion lives in
`test_the_committed_fixtures_pass_through_redaction_unchanged`, so a future rule that
starts rewriting scored text fails a test rather than producing evaluator drift.

The three statuses are each reachable, which the schema's vocabulary would otherwise
only claim: a clean record stays `clean`; a credential in `final_output` yields
`redacted` and `[REDACTED:openai_key]` in the artifact; a credential in `model_id` —
a join key — yields `flagged` and the executor **refuses the run**, naming the field
and the pattern without echoing the value.

## What will be reported here next

Milestone 10 defines the integration contracts: trace import/export formats so a
sibling repository can hand this one a run and get a comparison back. An imported
trace is the first thing that can legitimately arrive `flagged`.

## Reproduction

The exact commands that generate these numbers are in
[`REPRODUCIBILITY.md`](REPRODUCIBILITY.md). All numbers are produced offline,
CPU-only, and are byte-reproducible on the same fixtures.
