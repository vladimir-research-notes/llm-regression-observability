# Observability

The framework produces local, inspectable traces and metrics. It is **not** a
hosted observability backend and does not require one. The goal is clean,
deterministic, file-based telemetry that a human or a downstream tool can read.

## Spans

A run is decomposed into nested spans (`observability/spans.py`). Each span is a
plain record with a name, a parent, ordered timing, and attributes — an
OpenTelemetry-*shaped* model without the OpenTelemetry dependency.

```
request span                 one per case invocation
├── model_call span          the (mock/recorded) model call
├── tool_call span (0..n)    each tool invocation
├── retrieval span (0..n)    each retrieval/grounding step
└── evaluator span (1..n)    each scorer that ran on the output
```

Span record:

```
span_id: str
parent_id: str | None
name: str                    # request | model_call | tool_call | retrieval | evaluator
case_id: str
run_id: str
start_ms: float              # relative to run start; from trace, not the clock
end_ms: float
attributes: dict
status: str                  # ok | error
```

Spans are written to `results/runs/<run_id>/spans.jsonl` alongside the
`RunRecord`s. Timings come from the trace/fixture, so spans are reproducible.

## Metrics

`observability/metrics.py` defines metric names and their reducers (mean, sum,
p50, p95, ratio). Evaluators emit per-case `MetricResult`s; the aggregation layer
reduces them to suite-level numbers.

## Aggregation

`observability/aggregation.py` turns per-case metrics and spans into suite
summaries: success rate, validity rate, tool-drift rate, evidence coverage,
latency percentiles, cost totals, error/retry rates, and trace completeness. All
reducers use the standard library only.

## How it fits together

```
adapter output ──▶ RunRecord + spans ──▶ evaluators ──▶ MetricResults
                                                  │
                                                  ▼
                                        aggregation ──▶ suite metrics ──▶ report
```

Observability is what makes the *distinction between application regression and
harness noise* possible: `trace_completeness` and `infra`-origin errors are
observability signals, not application scores, so a degraded harness shows up as
a telemetry problem rather than a false regression.

## Non-goals

- No live dashboards, no metrics push, no sampling, no distributed tracing across
  processes. (An optional low-friction local viewer may render the JSONL
  artifacts, but the artifacts are the product.)
- No vendor lock-in: the span shape is compatible with OpenTelemetry concepts so
  a future exporter is possible, but none is required.
