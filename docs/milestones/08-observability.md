# Milestone 8 — Observability layer

**Status: Complete.** Spans and aggregate metrics are produced deterministically;
`llmreg aggregate` writes `spans.jsonl` and `aggregate.json`, and the harness
signals (`trace_completeness`, `infra_error_rate`) are reported in a map separate
from the application scores. 70 tests.

## Goal

Add trace/span/metric concepts so a run is inspectable as nested spans and
reducible to suite-level metrics. See [`../OBSERVABILITY.md`](../OBSERVABILITY.md).

## Deliverables

In `src/llm_regression_observability/observability/`:

- `spans.py` — request / model-call / tool-call / retrieval / evaluator spans
  (OpenTelemetry-*shaped*, no dependency); written to
  `results/runs/<run_id>/spans.jsonl`.
- `metrics.py` — metric names and reducers (mean, sum, p50, p95, ratio).
- `aggregation.py` — reduce per-case `MetricResult`s and spans to suite
  summaries, including `trace_completeness` and separated `infra_error_rate`.

Plus `llmreg aggregate` (CLI) and `scripts/07_aggregate_runs.py`.

## Validation

```bash
python3 -m pytest -q tests/test_observability.py
python3 scripts/07_aggregate_runs.py
```

Tests assert spans nest correctly, timings come from traces (not the clock),
aggregation is deterministic, and trace-completeness/infra signals are reported
separately from application scores.

## Decisions this milestone had to make

The spec named three files. Four things it left open were settled here.

**Who owns `spans.jsonl`?** `llmreg aggregate`, not the executor. An `evaluator`
span needs a metric, and the executor has never scored anything. Spans are a
*projection* of the recorded trace — a pure function of `(case, record, metrics)`
— so deriving them one stage later loses nothing and buys one writer and a
complete tree. `docs/PLAN.md` §4 still holds: the file lands under
`results/runs/<run_id>/`.

**Where do child timings come from?** Nowhere — the trace records exactly one
duration, `RunRecord.latency_ms`. The request span reproduces it (`recorded`);
its application children get a contiguous, non-overlapping partition of that
interval in trace order (`derived`), which asserts *ordering* and not *duration*;
evaluator spans are zero-width at the request's end (`none`), because scoring
happens afterwards in the harness. Every span says which. Inventing per-step
durations would have been the easy lie.

**What may a span attribute hold?** Identifiers, counts, statuses, booleans. Never
a prompt, an output, a tool argument *value*, a snippet, or an error *message* —
Milestone 9 has not run yet, and until a redactor has seen a string no telemetry
artifact may copy it. Tool argument **keys** are carried; they are schema.

**One map or three?** Three. `application_metrics`, `harness_metrics`,
`placeholder_metrics`. A flat map lets a broken exporter and a broken model look
alike, which is the exact failure this project exists to avoid, so the separation
is structural rather than a naming convention.

## Results on the smoke suite

| | baseline (`recorded`) | candidate (`scripted-regression`) |
| --- | --- | --- |
| spans | 106 over 8 traces, depth 2 | 105 over 8 traces, depth 2 |
| `request` / `model_call` | 8 / 8 | 8 (2 error) / 8 |
| `tool_call` / `retrieval` | 6 / 2 | 5 (2 error) / 3 |
| `evaluator` | 82 | 81 |
| `success_rate` (app) | 1.0 | 0.375 |
| `error_rate` (app) | 0.0 | 0.125 |
| `trace_completeness` (harness) | 1.0 | 1.0 |
| `infra_error_rate` (harness) | 0.0 | 0.125 |

`at_infra_noise` raises `infra_error_rate` and nothing else; `at_more_retries`
raises `error_rate` and nothing else. They are counted in different maps.

## Rationale

Observability is what makes the application-vs-harness distinction possible: a
degraded harness surfaces as a telemetry problem, not a false regression.

## Definition of done

Spans and aggregate metrics produced deterministically; docs/website updated.
Next: Milestone 9 — redaction and safety hygiene.
