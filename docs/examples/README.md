# Example artifacts

Small, committed outputs of the pipeline, kept here so a reader can see what the
tool produces without running it.

| File | Produced by | Kept honest by |
| --- | --- | --- |
| [`regression_report.md`](regression_report.md) | `python3 scripts/06_generate_report.py` | `tests/test_reports.py::test_the_committed_example_report_is_not_stale` |

The test rebuilds the smoke comparison from the committed fixtures, renders it, and
compares byte for byte. A sample report in the docs that drifts from what the code
emits is a claim nobody re-checks — so it cannot drift.

Regenerate after any change to a section heading, a flag explanation, or a number
format:

```bash
python3 scripts/02_run_baseline.py
python3 scripts/03_run_candidate.py
python3 scripts/05_compare_runs.py
python3 scripts/06_generate_report.py
cp results/reports/regression_report.md docs/examples/regression_report.md
```

Everything here is synthetic: the case ids, metric names, thresholds and
rule-authored explanations come from `evals/fixtures/` and `data/fixtures/`. No
model was called and no real data appears. The rendered HTML report is *not*
committed — it is regenerable, and `results/reports/` is gitignored.
