# Milestone 13 — Final cleanup

**Status: planned.**

## Goal

Run every quality gate end to end and leave the repository clean.

## Checklist

Run and confirm all pass:

```bash
python3 -m pytest -q                                   # tests
python3 scripts/01_validate_fixtures.py evals/fixtures # fixtures validate
# smoke baseline + candidate runs (recorded / scripted-regression)
llmreg run-suite --suite evals/suites/smoke.yaml --adapter recorded \
    --app-version baseline  --out results/runs/
llmreg run-suite --suite evals/suites/smoke.yaml --adapter scripted-regression \
    --app-version candidate --out results/runs/
llmreg compare --baseline results/runs/<baseline> --candidate results/runs/<candidate> \
    --out results/comparisons/
llmreg report --comparison results/comparisons/<id> \
    --out results/reports/regression_report.md
python3 -m ruff check . || true                        # lint (optional tool)
git status
```

Verify the repository contains: no secrets, no real credentials, no large logs,
no caches, no paid-API outputs; and that the working tree is clean.

## Quality gates (all must hold)

- tests pass;
- fixtures validate;
- smoke suite runs fully offline;
- baseline and candidate runs are generated;
- comparison detects the intentional regressions;
- a report is generated from comparison records;
- redaction works;
- docs explain methodology, metrics, limitations, and reproduction;
- no paid API is required;
- working tree is clean; **nothing is pushed**.

## Definition of done

All gates green; hygiene check clean; final commit made locally. The build is
complete.
