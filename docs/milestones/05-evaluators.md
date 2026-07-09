# Milestone 5 — Evaluators

**Status: Complete.** Ten deterministic scorer modules with exact expected scores
on all 52 fixture records; 155 new tests (345 total).

## Goal

Implement the deterministic scorers defined in [`../METRICS.md`](../METRICS.md).
Each consumes a `RunRecord` (and the `EvalCase`) and emits `MetricResult`s.

## Deliverables

In `src/llm_regression_observability/evaluators/`:

- `base.py` — rounding, ratios, `metric()`, `METRIC_DIRECTION`,
  `PLACEHOLDER_METRICS`, `NEVER_FLAG_METRICS`.
- `exact.py` — opt-in exact match + structural `expected_properties` checks
  (task success), with pairwise and advisory properties made explicit.
- `json_schema.py` — JSON parse success, schema validity (stdlib validator),
  field-level diff.
- `tool_calls.py` — allowed-tool ratio, argument diff, tool-sequence similarity,
  missing/extra tools.
- `evidence.py` — citation presence/correctness, evidence coverage,
  unsupported-claim count.
- `refusal.py` — refusal match, risk-label match/delta, explanation-quality
  **placeholder**.
- `latency.py` — per-case latency, p50/p95.
- `cost.py` — token/cost estimate and totals.
- `semantic_stub.py` — deterministic stand-in similarity (**explicitly not** a
  learned judge).
- `errors.py` — error classification (app vs infra), retries, tool error rate.
- `completeness.py` — trace-completeness checklist.
- `registry.py` — `evaluate_case()` / `evaluate_run()`.

Plus `scripts/04_score_runs.py`, which scores the Milestone 4 run artifacts and
writes `results/processed/<run_id>/metrics.jsonl`.

## Validation

```bash
python3 -m pytest -q tests/test_evaluators.py   # 155 passed
python3 -m pytest -q                            # 345 passed
python3 scripts/04_score_runs.py                # scores both smoke runs
```

Tests assert **exact expected scores** on fixture inputs: an invalid-JSON
candidate scores `json_schema_validity == 0`; a dropped citation scores
`evidence_coverage == 0.5`; a swapped tool order scores
`tool_sequence_similarity == 0.5`; the infra-attributed case keeps
`task_success == 1.0` and `app_error_count == 0`. No scorer uses ambient
time/random — enforced by an AST scan over all thirteen modules.

## Rationale

Exact-score tests turn "the scorer works" into a checkable fact and lock the
metric definitions against drift.

## Decisions this milestone settled

- **A metric that cannot be computed is not emitted.** An absent metric means
  *not applicable*, never zero. `field_diff_count` is therefore silent when the
  output did not parse, so one defect does not count twice.
- **Direction lives in a table, not in each scorer.** `METRIC_DIRECTION` lets
  Milestone 6 flag "worse" without hard-coding a sign per metric;
  `NEVER_FLAG_METRICS` guarantees the two placeholders and the infra counters
  can never raise a regression.
- **Properties a single record cannot answer are named, not dropped.**
  `final_output_stable` is *pairwise*; `expected_source_for_color` is *advisory*.
  A test pins the exact advisory set the committed fixtures produce, so a new
  unchecked property cannot appear silently.
- **Trace completeness is a harness signal.** A broken JSON output is a
  regression with a complete trace; a record with no latency is an incomplete
  trace. Keeping them apart is what lets Milestone 6 exclude harness failures
  instead of scoring their zeros.

## Definition of done

All evaluator tests pass with exact expected scores; docs/website updated. Next:
Milestone 6 — comparison engine.
