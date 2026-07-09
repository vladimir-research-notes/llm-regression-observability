# Observability

The framework produces local, inspectable traces and metrics. It is **not** a
hosted observability backend and does not require one. The goal is clean,
deterministic, file-based telemetry that a human or a downstream tool can read.

Implemented in Milestone 8: `observability/spans.py`, `observability/metrics.py`,
`observability/aggregation.py`, driven by `llmreg aggregate`.

## Spans

A run is decomposed into one span tree per case (`observability/spans.py`). Each
span is a plain record with a name, a parent, ordered timing, and attributes — an
OpenTelemetry-*shaped* model without the OpenTelemetry dependency.

```
request span                 one per case invocation
├── model_call span          the (mock/recorded) model call
├── tool_call span (0..n)    each tool invocation, in trace order
├── retrieval span (0..n)    each retrieved source
└── evaluator span (1..n)    one per metric scored on the output
```

Span record:

```
trace_id: str                32 hex chars; one case is one trace
span_id: str                 16 hex chars
parent_id: str | None
name: str                    request | model_call | tool_call | retrieval | evaluator
case_id: str
run_id: str
start_ms: float              relative to the request start; from the trace, not the clock
end_ms: float
status: str                  ok | error
attributes: dict
```

Spans are written to `results/runs/<run_id>/spans.jsonl` alongside the
`RunRecord`s.

### Spans are derived, not emitted

Nothing here instruments a live call — there is no live call. `build_case_spans`
is a **pure function of `(EvalCase, RunRecord, metrics)`**, so the span tree is a
*projection* of the artifact on disk: rebuildable at any time, byte-identical
every time. That is why spans can be a derived artifact at all, and why the stage
that owns `spans.jsonl` is the one holding both the trace *and* the scores
(`llmreg aggregate`), not the executor, which has never scored anything. An
`evaluator` span needs a metric; the executor has none.

Ids are content-addressed: `span_id = sha256(run_id | case_id | path)[:16]`, where
`path` is the span's position in the tree (`request/tool_call/1`). An id is a
function of *where the span sits*, never of when it was generated.

### Only one duration was ever recorded

`RunRecord.latency_ms` is the single timing the trace carries. `ToolCallTrace` and
`RetrievalTrace` carry none. So:

| Span | Interval | `attributes["timing"]` |
| --- | --- | --- |
| `request` | `[0, latency_ms]` — the value the trace records | `recorded` |
| `model_call`, `tool_call`, `retrieval` | a contiguous, non-overlapping partition of the request interval, in trace order | `derived` |
| `evaluator` | zero-width at the request's end | `none` |

A derived span's *width* is `latency_ms / step_count` and **means nothing on its
own**. The partition asserts **ordering**, which the trace records; it does not
assert **duration**, which the trace does not. Evaluator spans have no place on
the application's timeline at all: scoring happens afterwards, in the harness.

Reading a derived width as a measurement is the one misuse this design cannot
prevent, so it is labeled on every span and tested (`tests/test_observability.py`).
Cross-category interleaving (a model call between two tool calls) is not recorded
either, so the child order — model call, then tools, then sources — is
**canonical, not observed**.

### Spans carry symbols, never free text

A span attribute holds an identifier (`tool_name`, `source_id`, `metric`,
`error_kind`), a count, a status, or a boolean. It never holds a prompt, a model
output, a tool argument *value*, a retrieved snippet, or an error *message*.
Redaction lands in Milestone 9; until a redactor has seen a string, no telemetry
artifact may copy it. Tool argument **keys** are carried, because they are schema,
not content. A test scans every span attribute of every committed fixture record
against the free text of the record it came from.

### Status is about the operation, not the score

An `evaluator` span that reports `passed=false` has `status="ok"`: the scorer ran
fine and found a real failure. A `tool_call` span is `error` when the trace says
the call failed. Errors attach to the `request` span with their origin (`app` /
`infra`) and their kinds, because `ErrorRecord` does not name the step it occurred
in — inventing a parent for it would be a guess the trace cannot support.

## Metrics

`observability/metrics.py` defines the suite metric names, their reducers, and
what each number is allowed to mean.

**A reducer is named, not assumed:** `mean`, `sum`, `p50`, `p95`, `ratio`.
`ratio` is the fraction of cases with a **non-zero** value, which is what makes
`app_error_count → error_rate` mean "how many cases errored" rather than "how many
errors, averaged" — two errors on one case out of eight is a rate of `0.125`, and a
mean of `0.25`. `p50`/`p95` reuse the type-7 percentile already pinned in
`evaluators/latency.py`, so a percentile means one thing in this repository.

**Direction is inherited, never restated.** Every reducer is monotone, so a suite
metric is worse in the same direction its per-case metric is worse.
`MetricSpec.direction` reads `evaluators.base.METRIC_DIRECTION`; a second table
that could silently disagree with the first never exists.

`tool_arg_diff` and `risk_label_delta` are **pairwise** metrics (baseline against
candidate). A single run never emits them, so no suite metric reduces them — the
comparison engine owns that dimension.

## Aggregation

`observability/aggregation.py` reduces per-case `MetricResult`s and spans into a
`RunAggregate`, written to `results/processed/<run_id>/aggregate.json`.

**The summary has three maps, not one:**

| Map | Contents | Meaning |
| --- | --- | --- |
| `application_metrics` | `success_rate`, `json_validity_rate`, `tool_sequence_similarity_mean`, `evidence_coverage_mean`, `refusal_match_rate`, `latency_p50`/`p95`, `cost_total_usd`, `error_rate`, `retry_total`, … | scores for the system under test |
| `harness_metrics` | `trace_completeness`, `infra_error_rate`, `infra_error_total`, `incomplete_trace_rate` | whether the *observation* can be trusted |
| `placeholder_metrics` | `explanation_quality_mean`, `semantic_similarity_stub_mean` | computed, claimed for nothing |

A single flat map would let a broken exporter and a broken model look alike at a
glance. The split is **structural**, so a reader cannot miss it and a downstream
consumer cannot accidentally average across it. Nothing in
`NEVER_FLAG_METRICS` reaches `application_metrics`; a test asserts it.

`metric_coverage` counts how many cases contributed to each suite number, for the
same reason: `evidence_coverage_mean = 1.0` over 2 of 8 cases is a fact about two
cases, and a summary that does not say so is inviting a misreading. A metric no
case emitted produces no suite metric at all — absent is *not applicable*, never
zero.

Two case-id lists are the audit trail. `incomplete_trace_case_ids` names the cases
whose *trace* failed a completeness check; `infra_attributed_case_ids` names those
whose every error was infra-origin. Both are harness findings. Neither is a
regression.

## How it fits together

```
adapter output ──▶ RunRecord ──┬──▶ evaluators ──▶ MetricResults ──┐
                               │                                    ├──▶ aggregation
                               └────────── spans (projection) ──────┘        │
                                                                             ▼
                                                        spans.jsonl + aggregate.json
```

Observability is what makes the *distinction between application regression and
harness noise* possible: `trace_completeness` and `infra`-origin errors are
observability signals, not application scores, so a degraded harness shows up as a
telemetry problem rather than a false regression.

On the smoke suite, `at_infra_noise` has a failed tool call and a red `request`
span, and it moves `infra_error_rate` to `0.125` while leaving `error_rate` at the
`0.125` that `at_more_retries` — a genuine application error — put there. The two
cases are counted in different maps.

## Known blur

`tool_error_rate` is *descriptive*: an infra timeout fails a tool call too. Its
suite mean (`tool_error_rate_mean`) therefore sits in `application_metrics` while
mixing origins — on the smoke candidate it is `0.125`, raised equally by
`at_more_retries` (app) and `at_infra_noise` (infra). It cannot cause a false
regression, because the flag rule reads `app_error_count` and never this. But the
number does mix origins. It is pinned by a test
(`test_tool_error_rate_mean_mixes_origins_and_is_pinned_here`) rather than
described, so it cannot drift silently. See [`LIMITATIONS.md`](LIMITATIONS.md).

## Non-goals

- No live dashboards, no metrics push, no sampling, no distributed tracing across
  processes. (An optional low-friction local viewer may render the JSONL
  artifacts, but the artifacts are the product.)
- No vendor lock-in: the span shape is compatible with OpenTelemetry concepts so
  a future exporter is possible, but none is required.
- No measured wall-clock timing anywhere. Latency is a recorded input.
