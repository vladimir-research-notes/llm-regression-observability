# Metrics

Every metric is computed by a deterministic evaluator in
`src/llm_regression_observability/evaluators/`. Metrics are normalized to `[0, 1]`
where a ratio is meaningful (higher = better) and reported in native units
(ms, USD, counts) otherwise. Each metric emits a `MetricResult`
(see [`TRACE_FORMAT.md`](TRACE_FORMAT.md)).

## Final-answer success

- **task_success** ∈ {0, 1} — did the output satisfy the case's
  `expected_properties`? Property checks are structural (contains, equals,
  matches, numeric-range), never raw string equality unless the case explicitly
  declares an exact-match property.
- **exact_match** ∈ {0, 1} — only for cases that opt in; used sparingly.

## Structured-output validity

- **json_parse_success** ∈ {0, 1} — did `final_output` parse as JSON?
- **json_schema_validity** ∈ {0, 1} — does the parsed object validate against
  `json_schema`? (Stdlib validator; no network, no jsonschema dependency.)
- **field_diff_count** — number of fields added / removed / type-changed vs the
  expected shape. Detail lists each field and its disposition.

## Tool-call drift

- **allowed_tool_ratio** ∈ [0, 1] — fraction of calls that used an
  `allowed_tools` entry.
- **tool_arg_diff** — normalized edit distance between expected and observed
  argument dicts, per matched call.
- **tool_sequence_similarity** ∈ [0, 1] — similarity of the tool-call *order*
  (sequence ratio over tool-name sequences).
- **missing_tools / extra_tools** — counts of expected-but-absent and
  present-but-unexpected calls.

## Retrieval / evidence coverage

- **citation_presence** ∈ [0, 1] — fraction of claims that carry a citation.
- **citation_correctness** ∈ [0, 1] — fraction of citations whose `source_id`
  exists among the case's `retrieval_docs`.
- **evidence_coverage** ∈ [0, 1] — fraction of required claims backed by a
  supporting snippet.
- **unsupported_claim_count** — claims with no supporting evidence.

## Refusal / boundary

- **refusal_match** ∈ {0, 1} — did the refuse/answer decision match the case's
  expected behavior?
- **risk_label_delta** — change in assigned risk label vs baseline
  (`0` = same, `1` = shifted).
- Explanation-quality is a **placeholder** metric (documented as such) — the
  scaffold records a stub score; no automated judgment of explanation quality is
  claimed.

## Latency

- **latency_ms** — per-case latency (from the trace).
- **latency_p50 / latency_p95** — suite-level percentiles.
- **latency_delta_p50 / _p95** — candidate minus baseline.

## Cost

- **cost_estimate_usd** — per-case, from a transparent token estimator
  (`tokens ≈ chars / k`, with a documented `k`) × a fixture price table. This is
  an **estimate**, never a bill.
- **cost_total_delta** — suite-level candidate minus baseline.

## Error rate

- **error_rate** ∈ [0, 1] — fraction of cases with an `app`-origin error.
- **retry_count** — total retries across a run (agent suite).
- **infra_error_rate** — reported separately; does **not** raise regression
  flags.

## Trace completeness

- **trace_completeness** ∈ [0, 1] — fraction of required trace fields present and
  well-formed. A drop indicates a harness/observability problem, not necessarily
  an application regression.

## Aggregation

Suite-level metrics are means / percentiles over per-case `MetricResult`s,
produced by `observability/aggregation.py`. All aggregation is deterministic and
uses only `statistics` from the standard library (`pandas` is an optional
convenience for ad-hoc summaries, never required).

## Thresholds

Which metric deltas raise a regression flag, and by how much, is governed by the
suite's `thresholds` and described in
[`REGRESSION_DETECTION.md`](REGRESSION_DETECTION.md).
