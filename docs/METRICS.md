# Metrics

Every metric is computed by a deterministic evaluator in
`src/llm_regression_observability/evaluators/` (Milestone 5). Metrics are
normalized to `[0, 1]` where a ratio is meaningful and reported in native units
(ms, USD, counts) otherwise. Each emits a `MetricResult`
(see [`TRACE_FORMAT.md`](TRACE_FORMAT.md)).

Two rules govern the whole package:

- **A metric that cannot be computed is not emitted.** Scoring a case that
  declares no `json_schema` as `json_schema_validity == 0` would invent a
  failure. Each evaluator states its precondition and stays silent otherwise; the
  comparison engine reads an absent metric as *not applicable*, never as zero.
- **Direction is metadata, not convention.** `evaluators.METRIC_DIRECTION` says
  whether a metric is better high, better low, or neither. A regression is a move
  in the bad direction, so no scorer hard-codes a sign.
  `evaluators.NEVER_FLAG_METRICS` names the metrics that may never raise a flag:
  the two placeholders, and the infra counters.

## Per-case and suite-level

Most metrics are **per case**, emitted by `evaluate_case(case, record)` and keyed
by `(case_id, metric)`. A few are **suite-level** functions over a run's records:
`latency_stats()` (`latency_p50` / `latency_p95`), `cost_stats()`
(`cost_total_usd`), `error_rate()`, `infra_error_rate()`, `retry_total()`.

Eight metrics appear on **every** case: `task_success`, `latency_ms`,
`cost_estimate_usd`, `app_error_count`, `infra_error_count`, `retry_count`,
`tool_error_rate`, `trace_completeness`. The rest depend on what the case
declares.

## Final-answer success

- **task_success** ∈ {0, 1} — did the output satisfy the case's
  `expected_properties`? Property checks are structural (contains, equals,
  matches, numeric-range, tool/evidence/refusal assertions), never raw string
  equality unless the case explicitly declares an exact-match property. The
  closed vocabulary is `evaluators.exact.PROPERTY_CHECKS`.
- **exact_match** ∈ {0, 1} — only for cases that opt in via
  `expected_properties["exact_match"]`. No fixture suite uses it.

A property a single record cannot answer is not silently dropped. It is either
**pairwise** (`final_output_stable`, decided by `exact.final_output_stable()`
against a baseline) or **advisory** (prose this vocabulary does not encode).
Both are listed in the metric's `detail`, and a case whose properties are all
pairwise or advisory scores `task_success = 1.0` with `passed = None` and
`detail["vacuous"] = true` — nothing failed, and nothing was tested.

## Structured-output validity

- **json_parse_success** ∈ {0, 1} — did `final_output` parse as JSON? The raw
  string is the source of truth, not `parsed_output`; when they disagree,
  `detail["parsed_output_mismatch"]` records it.
- **json_schema_validity** ∈ {0, 1} — does the parsed object validate against
  `json_schema`? `0` when the output did not parse. The validator is a stdlib
  subset (`evaluators.json_schema.SUPPORTED_KEYWORDS`); no network, no
  `jsonschema` dependency. Any keyword outside that subset is reported in
  `detail["unsupported_keywords"]` rather than silently ignored.
- **field_diff_count** — fields missing / type-changed / added versus the shape
  the schema declares. `detail` names each. Emitted **only when the output
  parsed**: a diff against an object that does not exist is a parse failure, and
  counting it twice would make one defect look like two.

## Tool-call drift

- **allowed_tool_ratio** ∈ [0, 1] — fraction of calls naming an `allowed_tools`
  entry. A run with no calls scores `1.0` (it used no disallowed tool).
- **tool_sequence_similarity** ∈ [0, 1] — order-sensitive `difflib` similarity of
  the expected and observed tool-name sequences. `[search, lookup]` against
  `[lookup, search]` scores `0.5`.
- **missing_tools / extra_tools** — multiset difference between expected and
  observed names. Order-insensitive, so a reordering registers only in the
  similarity score: one defect, one signal.
- **tool_arg_diff** ∈ [0, 1] — normalized edit distance between expected and
  observed argument dicts (sorted-key compact JSON), mean over calls matched by
  `(name, occurrence)`. Emitted per case only when the case declares
  `expected_tool_arguments`. Argument *drift* is usually pairwise, so
  `tool_calls.tool_arg_diff_between(baseline, candidate)` is what the comparison
  engine calls.

## Retrieval / evidence coverage

- **citation_presence** ∈ [0, 1] — fraction of claims that carry a `source_id`.
- **citation_correctness** ∈ [0, 1] — fraction of citations whose `source_id`
  exists among the case's `retrieval_docs`. This catches a *fabricated* source
  id; it cannot catch a real source cited for the wrong claim — that is what
  `unsupported_claim_count` is for.
- **evidence_coverage** ∈ [0, 1] — fraction of required claims backed by a
  supporting snippet. "Required" means `expected_properties["required_sources"]`
  when the case declares it, and otherwise the claims the answer actually made.
  `detail["basis"]` says which reading applied.
- **unsupported_claim_count** — claims whose cited snippet does not support them.
  An unmarked claim (`supported = null`) counts as unsupported: the conservative
  direction.

`supported` is a **recorded field of the trace**, not a judgment this framework
makes. Nothing reads a snippet and decides whether it entails a claim; see
[`LIMITATIONS.md`](LIMITATIONS.md).

## Refusal / boundary

- **refusal_match** ∈ {0, 1} — did the refuse/answer decision match the case's
  `should_refuse`? `detail["shift"]` names the direction (`over_refusal` /
  `under_refusal`), and `detail["decision_source"]` says whether the decision came
  from `parsed_output["refused"]` or from the documented lexical fallback.
- **risk_label_match** ∈ {0, 1} — did the assigned label match
  `expected_risk_label`?
- **risk_label_delta** ∈ {0, 1} — pairwise: `0` same, `1` shifted. A label can
  drift without the refusal decision flipping.
- **explanation_quality_placeholder** — a **placeholder**. It always emits `0.0`
  and claims nothing; no automated judgment of explanation quality is made. It is
  in `NEVER_FLAG_METRICS`.

## Latency

- **latency_ms** — per-case latency, read from the trace. Recorded, never
  measured (see [`LIMITATIONS.md`](LIMITATIONS.md)).
- **latency_p50 / latency_p95** — suite-level percentiles by **linear
  interpolation between closest ranks** (the "type 7" definition: position
  `q * (n - 1)` in the sorted sample, interpolating between neighbours). Pinning
  the method matters: a threshold is only meaningful if p95 means one thing.
- **latency_delta_p50 / _p95** — candidate minus baseline (Milestone 6).

## Cost

- **cost_estimate_usd** — per-case, from a transparent token estimator
  (`tokens ≈ chars / 4`) × a synthetic price table, computed upstream by the
  executor. This is an **estimate, never a bill**. The evaluator reads the
  recorded value rather than re-pricing; re-pricing is
  `llmreg run-suite --recompute-cost`, so a `cost_regression` can never be an
  artifact of two different price lists.
- **cost_total_usd** — suite-level total; **cost_total_delta** is the candidate
  minus baseline (Milestone 6). Cost is flagged on the total, not per case.

## Error rate

- **app_error_count / infra_error_count** — per case, split by
  `ErrorRecord.origin`. Only the first can raise `error_rate_regression`; the
  second is in `NEVER_FLAG_METRICS`.
- **retry_count** — calls repeating the name and arguments of an earlier
  **failed** call. Structural, not a label: an imported trace that never writes
  `"tool_retry"` is still counted correctly.
- **tool_error_rate** ∈ [0, 1] — failed calls over total calls. Descriptive: an
  infra timeout fails a tool call too, so `detail["attribution"]` says which kind
  of failure it was.
- **error_rate** ∈ [0, 1] — suite-level fraction of cases with an app-origin
  error. **infra_error_rate** is reported separately and does **not** raise flags.

`errors.attribution(record)` returns `infra` only when the record errored and
*every* error was infra-origin. One app error makes the whole record `app`: under
uncertainty it flags, because a missed regression costs more than a noisy one.

## Trace completeness

- **trace_completeness** ∈ [0, 1] — fraction of a named checklist of required
  trace fields present and well-formed (nine base checks, plus two that apply
  when the case declares `allowed_tools` / `retrieval_docs`). `detail["failed"]`
  names the failing checks.

A drop indicates a **harness/observability problem, not an application
regression**. The executor's `--continue-on-error` failure record scores well
below `1.0` here, which is the intended signal.

Note the one asymmetry: a candidate whose JSON output genuinely broke has
`parsed_output = null`, which is a *correct trace of a real failure*. It scores
`trace_completeness = 1.0` and `json_schema_validity = 0`. Conflating the two
would make a structured-output regression indistinguishable from a harness bug.

## Semantic similarity — a placeholder

- **semantic_similarity_stub** ∈ [0, 1] — lowercase alphanumeric tokens, then
  `difflib` similarity. A **deterministic stand-in, not a learned judge**: it
  rates a paraphrase low and a fluent contradiction high. It is in
  `NEVER_FLAG_METRICS` and exists to point a human at a case whose answer moved,
  not to decide whether the move was bad.

## Aggregation

Suite-level metrics are means / sums / percentiles over per-case `MetricResult`s.
All aggregation is deterministic and stdlib-only (`pandas` is an optional
convenience for ad-hoc summaries, never required). Every metric value is rounded
to six decimals (`cost_estimate_usd` to eight) so a `MetricResult` serializes to
identical bytes on any platform and a threshold comparison cannot hinge on the
last bit of a float.

Milestone 8 implements it in `observability/metrics.py` and
`observability/aggregation.py`. Three rules govern the result; see
[`OBSERVABILITY.md`](OBSERVABILITY.md) for the full table.

**A reducer is named, not assumed.** `mean`, `sum`, `p50`, `p95`, `ratio`.
`ratio` is the fraction of cases with a **non-zero** value, which is what makes
`app_error_count → error_rate` mean "how many cases errored" rather than "how many
errors, averaged": two errors on one case out of eight is a rate of `0.125` and a
mean of `0.25`. `p50`/`p95` reuse the type-7 percentile pinned above.

**Direction is inherited, never restated.** Every reducer is monotone, so a suite
metric is worse in the same direction its per-case metric is worse.
`MetricSpec.direction` reads `METRIC_DIRECTION` rather than duplicating it.

**The suite summary keeps three maps, not one.** `application_metrics` are scores
for the system under test; `harness_metrics` (`trace_completeness`,
`infra_error_rate`, `infra_error_total`, `incomplete_trace_rate`) describe whether
the *observation* can be trusted; `placeholder_metrics` are computed and claimed
for nothing. A flat map would let a broken exporter and a broken model look alike
at a glance. `metric_coverage` records how many cases contributed to each number,
because `evidence_coverage_mean = 1.0` over 1 of 8 cases is a fact about one case.

`tool_arg_diff` and `risk_label_delta` are **pairwise** (baseline against
candidate). A single run never emits them, so no suite metric reduces them — the
comparison engine owns that dimension.

## Thresholds

Which metric deltas raise a regression flag, and by how much, is governed by the
suite's `thresholds` and described in
[`REGRESSION_DETECTION.md`](REGRESSION_DETECTION.md).
