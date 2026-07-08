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

## MetricResult

Emitted by evaluators; one per (case, metric).

```
case_id: str
metric: str                           # e.g. json_schema_validity
value: float                          # normalized where meaningful (0..1)
passed: bool | None                   # against the metric's own criterion
detail: dict                          # scorer-specific breakdown
```

## ComparisonRecord

One record per paired case. Written to
`results/comparisons/<id>/comparison.jsonl`.

```
case_id: str
baseline_run_id: str
candidate_run_id: str
changed_fields: list[str]
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

### RegressionFlag

```
category: str                         # see REGRESSION_DETECTION.md
severity: str                         # info | warn | critical
metric: str                           # metric that triggered the flag
observed: float
threshold: float
explanation: str
attribution: str                      # app | infra
```

## ReportSummary

Single JSON object per comparison. Written to
`results/comparisons/<id>/summary.json` and consumed by the report generator.

```
comparison_id: str
suite_id: str
baseline_version: str
candidate_version: str
case_count: int
regressed_case_count: int
flags_by_category: dict[str, int]
latency_p50_delta: float
latency_p95_delta: float
cost_total_delta: float
clusters: list[FailureCluster]        # grouped failures
```

## Conventions

- **Sorted keys, stable ordering.** JSON is serialized with sorted keys and a
  fixed field order so diffs are meaningful and outputs are byte-stable.
- **No ambient values.** Timestamps and seeds come from inputs, never from the
  clock or RNG at write time.
- **Redaction before persistence.** Any text field that could contain a
  secret/PII-like string passes through the redactor (Milestone 9) before it is
  written to `results/` or a report.
