# Milestone 8 — Observability layer

**Status: planned.**

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

## Validation

```bash
python3 -m pytest -q tests/test_observability.py
```

Tests assert spans nest correctly, timings come from traces (not the clock),
aggregation is deterministic, and trace-completeness/infra signals are reported
separately from application scores.

## Rationale

Observability is what makes the application-vs-harness distinction possible: a
degraded harness surfaces as a telemetry problem, not a false regression.

## Definition of done

Spans and aggregate metrics produced deterministically; docs/website updated.
Next: Milestone 9 — redaction and safety hygiene.
