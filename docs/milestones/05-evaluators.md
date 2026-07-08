# Milestone 5 — Evaluators

**Status: planned.**

## Goal

Implement the deterministic scorers defined in [`../METRICS.md`](../METRICS.md).
Each consumes a `RunRecord` (and the `EvalCase`) and emits `MetricResult`s.

## Deliverables

In `src/llm_regression_observability/evaluators/`:

- `exact.py` — opt-in exact match + structural `expected_properties` checks
  (task success).
- `json_schema.py` — JSON parse success, schema validity (stdlib validator),
  field-level diff.
- `tool_calls.py` — allowed-tool ratio, argument diff, tool-sequence similarity,
  missing/extra tools.
- `evidence.py` — citation presence/correctness, evidence coverage,
  unsupported-claim count.
- `refusal.py` — refusal match, risk-label delta, explanation-quality
  **placeholder**.
- `latency.py` — per-case latency, p50/p95.
- `cost.py` — token/cost estimate and totals.
- `semantic_stub.py` — deterministic stand-in similarity (**explicitly not** a
  learned judge).
- error classification + trace completeness helpers.

## Validation

```bash
python3 -m pytest -q tests/test_evaluators.py
```

Tests assert **exact expected scores** on fixture inputs (e.g. an invalid-JSON
candidate scores `json_schema_validity == 0`; a dropped citation scores the
expected `evidence_coverage`). No scorer uses ambient time/random.

## Rationale

Exact-score tests turn "the scorer works" into a checkable fact and lock the
metric definitions against drift.

## Definition of done

All evaluator tests pass with exact expected scores; docs/website updated. Next:
Milestone 6 — comparison engine.
