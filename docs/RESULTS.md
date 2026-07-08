# Results

> **Status: partial.** The run executor (Milestone 4) exists, so the *run
> artifacts* below are real and reproducible. No regression has been **detected**
> yet: scoring is Milestone 5 and comparison is Milestone 6. Nothing on this page
> claims otherwise.

## Run artifacts (Milestone 4)

Produced offline on the eight-case smoke suite by
`scripts/02_run_baseline.py` and `scripts/03_run_candidate.py`. These are counts
read off `results/runs/<run_id>/run_meta.json`, not scores.

| | baseline (`recorded`) | candidate (`scripted-regression`) | delta |
| --- | --- | --- | --- |
| records | 8 | 8 | 0 |
| input tokens (estimated) | 1310 | 1310 | 0 |
| output tokens (estimated) | 445 | 445 | 0 |
| cost estimate (synthetic price list) | $0.018700 | $0.018700 | $0.000000 |
| latency total (recorded) | 1690.0 ms | 1940.0 ms | +250.0 ms |
| tool calls | 6 | 5 | −1 |
| retrieval sources | 2 | 3 | +1 |
| errors (app / infra) | 0 / 0 | 1 / 1 | +1 / +1 |

`sha256(run.jsonl)`: baseline `ee23bfde0137104f…`, candidate `b486ff9e6dabd553…`.

Every delta traces to exactly one injected defect: `+250 ms` from
`lc_latency_spike`, the dropped tool call from `tu_wrong_tool`, the added
(unsupported) source from `re_unsupported_claim`, the `app` error from
`at_more_retries`, the `infra` error from `at_infra_noise`. `sj_invalid_json`
and `rb_underrefuse` change `parsed_output` rather than a count, and
`sj_control_reorder` is a control and changes nothing. Cost is unchanged because
the smoke suite carries no `cost_regression` case.

The candidate's token counts equal the baseline's because the scripted injectors
mutate outputs without re-estimating tokens — a known limitation of the
fixture-derived candidates, documented in
[`review_notes/03-adapters-human-review.md`](review_notes/03-adapters-human-review.md).

**Reproducibility.** Two runs on the same fixtures, in separate processes and
into different output roots, produce byte-identical `run.jsonl` and
`run_meta.json` (verified by `diff -r`, by the digest check `write_run()`
performs after every write, and by
`tests/test_run_executor.py::test_two_runs_on_the_same_inputs_are_byte_identical`).

## Test status

`python3 -m pytest -q` → **190 passed**, CPU-only and offline (14 scaffold +
54 schema + 25 fixtures + 46 adapters + 51 executor).

## What will be reported here

After the smoke suite runs offline with a baseline (recorded adapter) and a
candidate (scripted-regression adapter), this document will summarize:

- **Headline** — number of cases, number regressed, flags by category.
- **Detection of injected regressions** — the scripted adapter injects known
  defects; this table will show detected vs injected per category (the
  detector's recall on ground truth).
- **Structured-output validity** — baseline vs candidate validity rate.
- **Tool-call drift** — allowed-tool ratio, sequence similarity, missing/extra
  tools.
- **Evidence grounding** — coverage, citation correctness, unsupported claims.
- **Latency / cost** — p50/p95 latency deltas and total cost delta.
- **Refusal behavior** — expected vs observed refusal shifts.
- **Application vs. harness noise** — how many degradations were attributed to
  infrastructure and excluded from flags.

## Reproduction

The exact commands that generate these numbers are in
[`REPRODUCIBILITY.md`](REPRODUCIBILITY.md). All numbers are produced offline,
CPU-only, and are byte-reproducible on the same fixtures.

_No **detection** results are reported yet. The run artifacts above are counts
from real, committed-fixture runs; every claim about regressions being detected
waits on Milestones 5 and 6._
