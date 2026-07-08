# Milestone 6 — Baseline vs candidate comparison

**Status: planned.**

## Goal

Compare a baseline run against a candidate run, compute per-dimension deltas,
raise regression flags, cluster failures, and aggregate — the heart of the
system. See [`../REGRESSION_DETECTION.md`](../REGRESSION_DETECTION.md).

## Deliverables

In `src/llm_regression_observability/comparison/`:

- `baseline.py` / `candidate.py` — load and normalize each side's runs.
- `diff.py` — pair cases by `case_id`, compute `changed_fields` and per-metric
  deltas; report `unpaired` cases explicitly.
- `regression.py` — apply thresholds → `RegressionFlag`s with severity and
  app/infra attribution.
- `clustering.py` — group correlated failures (stdlib only) into
  `FailureCluster`s.
- aggregation into a `ReportSummary` (flags by category, latency percentiles,
  cost totals).
- CLI `llmreg compare` + `scripts/04_compare_runs.py`.

## Validation

```bash
python3 -m pytest -q tests/test_comparison.py
```

Tests assert the **injected regressions are detected**: for the scripted
candidate, the expected `RegressionFlag`s (category + severity) are produced, no
regression fires for an improved/equal candidate, and infra-tagged degradations
are excluded from flags.

## Rationale

This milestone is where the research question is operationalized and where the
"detects intentional regressions" quality gate is met.

## Definition of done

Comparison detects all injected regressions with expected categories; unpaired
and infra cases handled; docs/website updated. Next: Milestone 7 — reports.
