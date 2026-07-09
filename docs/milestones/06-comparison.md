# Milestone 6 — Baseline vs candidate comparison

**Status: Complete.** The comparison engine detects **18 of 18** injected defects
across the six fixture suites with their annotated category and severity, and
raises **0 false positives** on the 8 negative controls. `441 tests pass`.

## Goal

Compare a baseline run against a candidate run, compute per-dimension deltas,
raise regression flags, cluster failures, and aggregate — the heart of the
system. See [`../REGRESSION_DETECTION.md`](../REGRESSION_DETECTION.md).

## Deliverables

In `src/llm_regression_observability/comparison/`:

- `base.py` — `ComparisonError`, `RunSide` (one normalized, scored side), delta
  and relative-change helpers, and the behavioral/identity/provenance field split.
- `baseline.py` — load + score the reference side; resolve its suite manifest.
- `candidate.py` — load the side under test; `check_comparable()` decides what may
  be compared (suite agreement is required; a cost-model mismatch disables the
  cost rule rather than aborting).
- `diff.py` — pair cases by `case_id`, compute `changed_fields` and per-metric
  deltas, and the five pairwise signals; report `unpaired` cases explicitly.
- `regression.py` — apply thresholds → `RegressionFlag`s with severity and
  app/infra attribution.
- `clustering.py` — group correlated failures (stdlib only) into
  `FailureCluster`s by `(category, failure signature)`.
- `aggregate.py` — aggregation into a `ReportSummary`, plus a `ComparisonMeta`
  sidecar and the three byte-stable artifacts.
- CLI `llmreg compare` (with `--dry-run` and a `--fail-on` CI gate) +
  `scripts/05_compare_runs.py`.

## Validation

```bash
python3 -m pytest -q tests/test_comparison.py     # 96 passed
python3 scripts/05_compare_runs.py
```

Tests assert the **injected regressions are detected**: for the scripted
candidate, the expected `RegressionFlag`s (category + severity) are produced, no
regression fires for an improved/equal candidate, and infra-tagged degradations
are excluded from flags.

## Rationale

This milestone is where the research question is operationalized and where the
"detects intentional regressions" quality gate is met.

## Design decisions worth knowing

**One defect, one signal.** `task_success` is the union of a case's declared
`expected_properties`, and most of those belong to another dimension. Flagging
every `task_success` drop as `final_answer_regression` would report all 18 defects
twice. `PROPERTY_CATEGORY` attributes each property to its owning category, so
`final_answer_regression` fires only on an answer-level property failure. The one
fixture case that raises two flags — `at_failed_tool` — really does fail twice.

**A new unsupported claim is critical; a claim that lost support is a warning.**
Both raise `unsupported_claim_count` from 0 to 1, so the count cannot separate
them. The rule compares claim *texts*: an unsupported claim absent from the
baseline is one the model invented. This is what lets `re_unsupported_claim`
(critical) and `re_wrong_source` (warn) both match their ground truth.

**Infra attribution withholds every flag on the record, not just the error one.**
A harness timeout inflates latency, fails tool calls, and truncates output; blaming
the application for any of it would be wrong. The cost is that a real regression
co-occurring with an infra error on the same case is masked. It is recorded in
`comparison_meta.json` rather than dropped, and documented in `LIMITATIONS.md`.

**Two floors for latency and cost.** Each needs a relative *and* an absolute move.
`cost_abs_usd: 0.0005` is new to `evals/suites/smoke.yaml` (the other six
thresholds were already declared); without it, a sub-cent estimator rounding error
could flag.

## Deviations from this spec

- The CLI script is `scripts/05_compare_runs.py`, not `04_compare_runs.py`: the
  `04` slot is already `scripts/04_score_runs.py` from Milestone 5. Recorded in
  `REPRODUCIBILITY.md`.
- A third artifact, `comparison_meta.json` (`ComparisonMeta`), was added alongside
  `comparison.jsonl` and `summary.json`. `ReportSummary` has nowhere to record an
  unpaired case, an infra-suppressed flag, or the thresholds actually applied, and
  those silences are the ones most likely to make the harness lie. No Milestone 1
  schema was changed.

## Definition of done

Comparison detects all injected regressions with expected categories; unpaired
and infra cases handled; docs/website updated. Next: Milestone 7 — reports.
