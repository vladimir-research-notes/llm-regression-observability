# Regression detection

A regression is raised when a candidate's measured behavior is **worse than the
baseline by more than a threshold** on a given dimension. Detection is
deterministic and threshold-driven; nothing is learned or fitted.

Implemented in `src/llm_regression_observability/comparison/` (Milestone 6) and
exercised by `llmreg compare`. Tested in `tests/test_comparison.py`.

## Inputs

- Paired `RunRecord`s (baseline, candidate) for the same `case_id`.
- Per-case `MetricResult`s from the evaluators, plus five *pairwise* signals no
  single record can produce: tool-argument drift, a refusal-decision flip, a
  risk-label shift, a claim that lost its support, and final-output stability.
- A `thresholds` table, read from the suite manifest (`evals/suites/*.yaml`).

## Regression categories

| Category | Trigger metric(s) | Default threshold |
| --- | --- | --- |
| `structured_output_regression` | `json_parse_success` drop, `json_schema_validity` drop | any drop to 0 on a case valid at baseline |
| `tool_call_regression` | new disallowed tool, `missing_tools` / `extra_tools` rise, `tool_sequence_similarity` drop, `tool_arg_diff` | seq_sim < 0.8, or any disallowed/missing tool, or any argument drift |
| `latency_regression` | `latency_ms` | +25% **and** +50 ms |
| `cost_regression` | `cost_estimate_usd` | +20% **and** +$0.0005 |
| `refusal_behavior_shift` | `refusal_match` drop, refusal-decision flip, `risk_label_match` drop / `risk_label_delta` | any flip vs expected, or any label move |
| `evidence_grounding_regression` | new unsupported claim, `evidence_coverage` drop, `citation_correctness` / `citation_presence` drop, a claim that lost support | coverage drop > 0.2, or any new unsupported claim |
| `final_answer_regression` | `task_success` drop on an **answer-level** property | success 1 → 0 |
| `error_rate_regression` | `error_rate` / `app_error_count` rise, `retry_count` rise (app-origin only) | error_rate delta > +0.1 |

Thresholds are the documented defaults in `comparison.Thresholds`; the
implementation reads them from the suite manifest so they can be tuned without
code changes. An **unknown threshold key is an error**, not a shrug: a typo'd
threshold that is silently ignored looks exactly like one being honored.

### Latency and cost need two floors

Each requires a **relative** move *and* an **absolute** one. A relative-only rule
flags 2 ms → 3 ms on a fast case; an absolute-only rule flags a slow case that got
proportionally no worse. The fixture controls sit deliberately between the two:
`lc_latency_control` moves +30 ms / +20% and `lc_cost_control` moves +$0.0002 /
+10%, and neither flags.

`latency_p95_rel` governs the per-case latency flag *and* the suite-level p95
delta in `ReportSummary`. Two names for one tolerance would let a suite pass one
and fail the other.

A cost delta is only computed when both runs were priced under the **same**
`CostModel`. Comparing a baseline priced at one rate against a candidate priced at
another reports the price list, not the change; the rule is disabled and the
reason is recorded in `comparison_meta.json`.

## Severity

- **critical** — correctness lost: success 1→0 on an answer-level property, schema
  valid→invalid, a parse that stopped parsing, a disallowed tool used, an expected
  tool call that disappeared, a *new* unsupported claim, under-refusal.
- **warn** — quality or efficiency degraded past a threshold: latency, cost, tool
  sequence or argument drift, partial evidence drop, an existing claim that lost
  its support, over-refusal, a risk-label move, a new app-origin error or retry.
- **info** — reserved. A *flag* means a threshold was crossed, so the comparison
  engine emits only `warn` and `critical`. `info` exists for the report generator
  to annotate sub-threshold movement.

Two severity distinctions carry real weight and are worth naming.

**Under-refusal is critical; over-refusal is a warning.** Answering something the
baseline declined is a correctness failure. Declining something benign is a
quality failure. Neither is visible in a string diff — "I'm sorry, but I can't
help with that" is a perfectly well-formed answer.

**A *new* unsupported claim is critical; a claim that *lost* its support is a
warning.** The fixtures make the difference concrete. `re_unsupported_claim`'s
candidate asserts something the baseline never said ("waterproof") and cites
nothing that backs it: the model invented a fact. `re_wrong_source`'s candidate
makes the *same* claim as the baseline but cites the wrong document: the model
degraded its grounding. Both raise `unsupported_claim_count` from 0 to 1, so the
count alone cannot separate them. The rule compares **claim texts**: an unsupported
claim whose text is absent from the baseline is new, and critical.

## One defect, one signal

`task_success` is the union of a case's declared `expected_properties`, and most
of those properties belong to another dimension: `json_schema_valid` is the
structured-output scorer's business, `expected_tools` is the tool scorer's,
`should_refuse` is the refusal scorer's. Flagging every `task_success` drop as a
`final_answer_regression` would report **every** defect twice and turn
`flags_by_category` into a histogram of itself.

So a task-success drop is *attributed*. `comparison.PROPERTY_CATEGORY` assigns
each checkable property to the category that owns it, and `final_answer_regression`
fires only when a newly-failed property is answer-level (`final_answer`,
`contains`, `not_contains`, `exact_match`) or has no owner. A property failure that
no metric noticed still raises its owner's flag — the property is the case author
saying "this must hold", and the evidence is that it stopped holding.

The visible consequence, on the fixtures:

- `sj_missing_required` has `score_delta = -1.0` and raises exactly one flag,
  `structured_output_regression`.
- `at_more_retries` keeps its answer ("done") but violates `max_tool_errors: 0`.
  `score_delta = -1.0`; the flag is `error_rate_regression`, which owns that
  property — not `final_answer_regression`, whose answer-level properties all hold.
- `at_failed_tool` is the one fixture case that legitimately raises two flags. Its
  candidate both errors *and* answers "partial" instead of "done". Two things went
  wrong; clustering groups them under one root cause.

Within a category, several conditions can fire at once — a wrong tool is also a
missing tool; an unparseable output is also an invalid one. They collapse into
**one flag per (case, category)** carrying the strongest severity and naming the
rest in its explanation, so `flags_by_category` counts findings, not symptoms.

## Attribution: application vs harness noise

Every candidate degradation is attributed:

- **app** — caused by the model/prompt/tool change. Raises a flag.
- **infra** — caused by the harness (timeout on a flaky mock, transport error,
  malformed fixture, incomplete trace). Recorded and surfaced in the report, but
  **does not** raise a regression flag.

Attribution uses the `origin` field on `ErrorRecord`. `errors.attribution()`
returns `infra` only when a record errored and **every** error was infra-origin;
a single app error makes the whole record `app`. It is deliberately conservative:
when in doubt it flags, because a missed regression is worse than a noisy one.

When a record is `infra`-attributed, the categories that *would* have fired are
withheld and written to `comparison_meta.json` under `infra_suppressed_categories`,
alongside the case id in `infra_attributed_case_ids`. Nothing is silently dropped.
This is the mechanism behind research question 9, and it has a cost: a real
application regression that co-occurs with an infra error on the same case is
masked. See [`LIMITATIONS.md`](LIMITATIONS.md).

`at_infra_noise` is the fixture control for this. Its candidate has a failed tool
call and a red trace; the error is `origin=infra`; `task_success` stays 1 and
`app_error_count` stays 0; **no flag is raised**, and the case is named in the
metadata sidecar.

## False positives and how they are controlled

- **Deterministic runs.** Mock/recorded/scripted adapters remove sampling noise,
  so a flagged delta reflects the change, not variance.
- **Thresholds with minimum absolute deltas.** See "two floors", above.
- **Direction-aware.** Only *worse* moves flag. A candidate that is faster,
  cheaper, more valid, more grounded, or less error-prone raises nothing.
- **Absent means not applicable.** A metric emitted on neither side (or on one
  side only) is never read as a zero. A case that declares no `json_schema` has no
  structured-output dimension to regress.
- **Never-flag metrics.** The two placeholders (`semantic_similarity_stub`,
  `explanation_quality_placeholder`) and the infra counters cannot raise a flag.
  `comparison.TRIGGER_METRICS ∩ evaluators.NEVER_FLAG_METRICS = ∅`, asserted at
  import and again in the tests.
- **`field_diff_count` is not a trigger.** It reports extra fields regardless of
  `additionalProperties`, so a rise while `json_schema_validity` holds at 1.0 means
  the candidate added a field the schema *permits* — legal, and not a property
  getting worse. An *illegal* extra field drops validity, which `schema_invalid`
  already catches. The count is reported for a human, and raises nothing.
- **Pair completeness.** A case missing from either run is reported as `unpaired`,
  not silently scored — an absent candidate is not a "success".
- **Clustering.** Correlated failures are grouped so one root cause reads as one
  finding, not N.

Measured on the committed fixtures: **18 injected defects, 18 detected, 0 missed,
0 false positives on the 8 negative controls.** See [`RESULTS.md`](RESULTS.md).

## Clustering

Flags group by **failure signature** — the specific way the candidate broke, as
recorded on the trigger that raised the flag: `schema_invalid`,
`disallowed_tool:shell_exec`, `coverage_drop`, `app_error:tool_retry`. The
signature carries the offending *symbol* where one exists, because "the model
started calling `shell_exec`" is a root cause and "a tool was wrong" is not. It
never carries model-generated prose, so a cluster label was safe to print even before
the redactor existed; the report now masks it anyway, on the way in.

Two flags cluster when they share `(category, signature)` — no distance metric, no
fitted grouping, nothing that could not be reproduced from the artifact alone. A
case that raised flags in two categories appears in two clusters: it really did
fail in two ways. Cluster ids are content-derived (`<category>__<signature>`), so
adding a case to a run never renumbers the clusters an earlier report referred to.

## Output

Flags are attached to each `ComparisonRecord` and aggregated into a
`ReportSummary` (`flags_by_category`, per-suite counts, clusters, latency
percentiles, cost total). `llmreg compare` writes three files to
`results/comparisons/<comparison_id>/`:

| File | Contents |
| --- | --- |
| `comparison.jsonl` | one `ComparisonRecord` per **paired** case, in baseline run order |
| `summary.json` | one `ReportSummary` — what regressed |
| `comparison_meta.json` | one `ComparisonMeta` — what the engine declined to say, and why |

The split is deliberate. An unpaired case and an infra-suppressed case are both
invisible in the summary's counts, and both are exactly the kind of silence that
makes an evaluation harness lie. They get a file.

`llmreg compare --fail-on critical` turns the comparison into a CI regression gate
(exit 1 when a flag reaches that severity). The default is `--fail-on none`: a
comparison is an observation, and only the caller decides what is unacceptable.
