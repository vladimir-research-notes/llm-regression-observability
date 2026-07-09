# Trace format

All artifacts are JSON / JSONL and are defined as typed schemas in
`src/llm_regression_observability/schemas.py`. This document is the
human-readable contract; the code is the source of truth. Field types below use
Python typing shorthand.

Files are **line-delimited JSON (JSONL)** where a stream of records is natural
(one record per line, append-friendly, diff-friendly) and plain JSON for
single-object summaries.

## EvalCase

```
case_id: str                          # stable identifier
version: str                          # case schema/content version
prompt: str                           # synthetic prompt
input_payload: dict                   # structured inputs
expected_properties: dict             # properties the output must satisfy
json_schema: dict | None              # optional; output must validate
allowed_tools: list[str] | None       # optional; permitted tools
retrieval_docs: list[RetrievalDoc] | None
tags: list[str]
risk_category: str                    # benign | structured | tool_use |
                                      #   retrieval | refusal | agent
```

`EvalSuite`: `{ suite_id, description, thresholds: dict, cases: list[EvalCase] }`.

## RunRecord

One record per (case, run). Written to `results/runs/<run_id>/run.jsonl`.

```
run_id: str
case_id: str
app_version: str                      # baseline | candidate | <label>
model_id: str
prompt_version: str
tool_version: str
timestamp: str                        # ISO-8601, from run config (not ambient)
input_tokens_estimate: int
output_tokens_estimate: int
latency_ms: float
cost_estimate_usd: float
final_output: str
parsed_output: Any | None             # e.g. parsed JSON, or null on failure
tool_calls: list[ToolCallTrace]
retrieval_sources: list[RetrievalTrace]
errors: list[ErrorRecord]
redaction_status: str                 # clean | redacted | flagged
```

`redaction_status` is **derived** by `traces/redaction.py`, never believed from the
adapter that produced the record. The values form a lattice, `clean < redacted <
flagged`, and redaction takes the maximum of what it found and what the record already
claimed — so the status can only escalate, and redacting twice changes nothing.
`clean` means no rule fired; `redacted` means a rule fired in a content field
(`final_output`, `parsed_output`, tool-call `arguments`, an `error`, a `snippet`, a
`claim`, an error `message`) and was masked with `[REDACTED:<rule>]`; `flagged` means
a credential-like string sits in an **identity field** — `run_id`, `case_id`,
`model_id`, a `tool_calls[].name`, a `retrieval_sources[].source_id`, an
`errors[].kind` — which the redactor will not rewrite because the pipeline joins on
it. A `flagged` run is refused, so the value never appears in a file this project
wrote; it exists for traces imported from elsewhere.

### ToolCallTrace

```
name: str                             # tool name
arguments: dict
order_index: int                      # position in the call sequence
status: str                           # ok | failed | skipped
error: str | None
```

### RetrievalTrace

```
source_id: str                        # id of a document provided to the case
snippet: str                          # the text the model relied on
claim: str | None                     # the claim this source supports
supported: bool | None                # whether the snippet supports the claim
```

### ErrorRecord

```
kind: str                             # e.g. parse_error, tool_error, timeout
message: str
origin: str                           # app | infra   (drives noise filtering)
```

## RunMeta

Sidecar for one run. Written to `results/runs/<run_id>/run_meta.json` by the run
executor (`executor.py`), alongside the `run.jsonl` it describes.

```
run_id: str
suite_id: str
suite_path: str                       # repo-relative, so artifacts leak no paths
adapter: str                          # mock | recorded | scripted-regression | api-stub
app_version: str
model_id: str                         # "(mixed)" if not constant across records
prompt_version: str                   # "(mixed)" if not constant across records
tool_version: str                     # "(mixed)" if not constant across records
timestamp: str                        # "(mixed)" if not constant across records
case_count: int
input_tokens_total: int
output_tokens_total: int
cost_total_usd: float
latency_ms_total: float               # summed recorded latency, never measured
tool_call_count: int
retrieval_source_count: int
error_count: int
app_error_count: int
infra_error_count: int
tokens_estimated_cases: int           # records whose tokens the executor derived
cost_estimated_cases: int             # records whose cost the executor derived
identity_overrides: list[str]         # provenance fields the caller forced
cost_model: CostModel                 # { input_usd_per_1k, output_usd_per_1k }
records_sha256: str                   # SHA-256 of run.jsonl's bytes
llmreg_version: str
```

The totals are derived from the records, so the file is a self-check: recompute
them from `run.jsonl` and they must agree, and `records_sha256` must equal the
SHA-256 of that file. A `RunRecord`'s `run_id` always equals its run's `run_id`,
so `(run_id, case_id)` is the artifact key.

### Run identity vs. provenance

`run_id` and `app_version` are **identity**: the executor forces them onto every
record in a run. `model_id`, `prompt_version`, `tool_version` and `timestamp` are
**provenance**: preserved exactly as the adapter reported them (the recorded
adapter replays committed fixture values) unless the caller overrides them, in
which case the field name appears in `identity_overrides`.

`cost_estimate_usd` and the two token fields are recorded inputs when the adapter
supplies them, and derived by the executor otherwise; `tokens_estimated_cases` /
`cost_estimated_cases` say how many records took the derived path.

## MetricResult

Emitted by evaluators (Milestone 5); one per (case, metric). Written to
`results/processed/<run_id>/metrics.jsonl` by `scripts/04_score_runs.py`, sorted
by metric name within each case.

```
case_id: str
metric: str                           # e.g. json_schema_validity
value: float                          # normalized where meaningful (0..1)
passed: bool | None                   # against the metric's own criterion
detail: dict                          # scorer-specific breakdown
```

Conventions the scorers hold to:

- `value` is rounded to six decimals (`cost_estimate_usd` to eight) so a result
  serializes to identical bytes on any platform.
- `passed` is `None` when the metric has **no criterion** — a raw `latency_ms`
  has a delta, not a verdict — and also when a `task_success` check was vacuous
  (no property was decidable from one record).
- A metric that cannot be computed is **not emitted at all**. An absent metric
  means *not applicable*; it never means zero.
- `evaluators.METRIC_DIRECTION` maps each `metric` name to
  `higher_better | lower_better | neutral`, and `evaluators.NEVER_FLAG_METRICS`
  names those that may never raise a `RegressionFlag` (the two placeholders and
  the infra counters). The comparison engine reads both rather than hard-coding a
  sign per metric.

## ComparisonRecord

One record per **paired** case, in the baseline's run order. Written to
`results/comparisons/<comparison_id>/comparison.jsonl` by `llmreg compare`.

```
case_id: str
baseline_run_id: str
candidate_run_id: str
changed_fields: list[str]             # behavioral fields only (see below)
score_delta: float                    # final-answer score change
latency_delta: float                  # ms
cost_delta: float                     # usd
json_validity_delta: float
tool_call_delta: float
refusal_delta: float
evidence_delta: float
regression_flags: list[RegressionFlag]
human_readable_summary: str
```

`changed_fields` lists only the fields that describe *what the application did*
(`comparison.BEHAVIORAL_FIELDS`). Run identity (`run_id`, `app_version`) and
provenance (`model_id`, `prompt_version`, `tool_version`, `timestamp`) differ
between every baseline and candidate — that is what a comparison *is* — and
reporting them would bury the one field that matters.

Each delta is `candidate - baseline` on that dimension's headline metric, and
`0.0` when the case never declared the dimension. **A zero delta is not a claim
that nothing changed**; `regression_flags` is the claim.

| Field | Metric |
| --- | --- |
| `score_delta` | `task_success` |
| `json_validity_delta` | `json_schema_validity`, else `json_parse_success` |
| `tool_call_delta` | `tool_sequence_similarity`, else `allowed_tool_ratio` |
| `refusal_delta` | `refusal_match`, else `risk_label_match` |
| `evidence_delta` | `evidence_coverage` |
| `latency_delta` | recorded `latency_ms` (native units) |
| `cost_delta` | recorded `cost_estimate_usd` (native units) |

Tool-argument drift and a refusal-decision flip move no delta — they are pairwise
signals, and they surface as flags.

### RegressionFlag

```
category: str                         # see REGRESSION_DETECTION.md
severity: str                         # info | warn | critical
metric: str                           # metric that triggered the flag
observed: float                       # the measured value the rule tested
threshold: float                      # the value it had to stay within
explanation: str
attribution: str                      # app | infra
```

At most **one flag per (case, category)**: when several conditions fire in one
category, they collapse into the strongest, which names the rest in its
`explanation`. `observed` and `threshold` are in the rule's own units — a fraction
for latency and cost (relative change), a metric value otherwise — and are always
finite (`comparison.regression._finite` rejects `inf`/`nan`, which `json.dumps`
would render as the non-standard `Infinity`).

The comparison engine emits only `warn` and `critical`; `info` is reserved for the
report generator to annotate sub-threshold movement.

## ReportSummary

Single JSON object per comparison. Written to
`results/comparisons/<comparison_id>/summary.json` and consumed by the report
generator. Percentiles and totals are computed over the **paired** cases only.

```
comparison_id: str
suite_id: str
baseline_version: str
candidate_version: str
case_count: int                       # paired cases
regressed_case_count: int             # paired cases with >= 1 flag
flags_by_category: dict[str, int]
latency_p50_delta: float
latency_p95_delta: float
cost_total_delta: float
clusters: list[FailureCluster]        # grouped failures
```

### FailureCluster

```
cluster_id: str                       # "<category>__<failure signature>"
label: str                            # human phrase + case count
category: str
case_ids: list[str]                   # sorted
```

`cluster_id` is derived from content, not from an ordinal, so adding a case to a
run never renumbers the clusters an earlier report referred to.

## ComparisonMeta

Sidecar for one comparison. Written to
`results/comparisons/<comparison_id>/comparison_meta.json` by `llmreg compare`,
alongside the `comparison.jsonl` it describes.

```
comparison_id: str
suite_id: str
suite_path: str                       # repo-relative
baseline_run_id: str
candidate_run_id: str
baseline_version: str
candidate_version: str
baseline_adapter: str
candidate_adapter: str
paired_case_count: int
unpaired_baseline_case_ids: list[str]
unpaired_candidate_case_ids: list[str]
infra_attributed_case_ids: list[str]
infra_suppressed_categories: dict[str, list[str]]   # case_id -> withheld flags
cost_comparable: bool                 # false when the runs used different CostModels
comparability_notes: list[str]
thresholds: dict[str, float]          # the values every rule compared against
flag_count: int
critical_count: int
warn_count: int
baseline_latency_p50: float
baseline_latency_p95: float
candidate_latency_p50: float
candidate_latency_p95: float
baseline_cost_total_usd: float
candidate_cost_total_usd: float
records_sha256: str                   # SHA-256 of comparison.jsonl's bytes
llmreg_version: str
```

`summary.json` answers "what regressed". This file answers "what did the engine
decline to say, and why". An **unpaired** case and an **infra-suppressed** case
are both invisible in the summary's counts, and both are exactly the kind of
silence that makes an evaluation harness lie — so they get a file. A comparison
with an empty summary and a populated sidecar has found nothing and hidden a lot,
and the two files together make that impossible to miss.

## Span

Written to `results/runs/<run_id>/spans.jsonl` by `llmreg aggregate` (Milestone 8).
An OpenTelemetry-*shaped* record with no OpenTelemetry dependency.

```
trace_id       32 hex chars; one case is one trace
span_id        16 hex chars
parent_id      str | None
name           request | model_call | tool_call | retrieval | evaluator
case_id
run_id
start_ms       relative to the request start; from the trace, never the clock
end_ms
status         ok | error
attributes     dict of identifiers, counts, statuses, booleans
```

Ids are **content-addressed**: `span_id = sha256(run_id | case_id | path)[:16]`,
where `path` is the span's position in the tree (`request/tool_call/1`). An id is a
function of where the span sits, never of when it was generated, so two projections
of one record are byte-identical.

`attributes["timing"]` names where the interval came from. The trace records exactly
one duration — `RunRecord.latency_ms` — so the `request` span is `recorded`, its
application children get a contiguous partition of that interval in trace order
(`derived`, which asserts ordering and **not** duration), and `evaluator` spans are
zero-width at the request's end (`none`, because scoring happens afterwards in the
harness).

`status` is about the operation, not the score: an `evaluator` span reporting
`passed=false` is `status="ok"`. Errors attach to the `request` span with their
origin and kinds, because `ErrorRecord` names no step.

**Spans carry symbols, never free text.** An attribute holds an identifier
(`tool_name`, `source_id`, `metric`, `error_kind`), a count, a status, or a boolean
— never a prompt, an output, a tool argument *value*, a retrieved snippet, or an
error *message*. Tool argument **keys** are carried; they are schema, not content.
See [`OBSERVABILITY.md`](OBSERVABILITY.md).

## RunAggregate

Written to `results/processed/<run_id>/aggregate.json`. One object per run: the
suite metrics in three maps (`application_metrics`, `harness_metrics`,
`placeholder_metrics`), `metric_coverage`, a `SpanSummary`, the cases whose traces
were incomplete or infra-attributed, and two digests (`spans_sha256`,
`metrics_sha256`).

## Conventions

- **Sorted keys, stable ordering.** JSON is serialized with sorted keys and a
  fixed field order so diffs are meaningful and outputs are byte-stable. The one
  place this is implemented is
  `src/llm_regression_observability/traces/storage.py`: sorted keys, compact
  separators for JSONL, `ensure_ascii=False`, explicit UTF-8, and an explicit
  `\n` line ending including a trailing one.
- **No ambient values.** Timestamps and seeds come from inputs, never from the
  clock or RNG at write time.
- **Redaction before persistence.** Every content field of a `RunRecord` passes
  through `traces/redaction.py` inside `execute_suite` — before tokens are accounted
  and before `run.jsonl` is hashed — so `records_sha256` digests the redacted bytes.
  Identity fields are scanned, never rewritten: a credential-like string in one of
  them sets `redaction_status="flagged"` and the run is refused. Reports mask their
  free text on the way in and gate the rendered document on the way out. See
  [`SAFETY.md`](SAFETY.md).
