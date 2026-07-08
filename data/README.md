# data/

Small, committed, **synthetic** fixture inputs used by the evaluation suites.
There is **no real user data** here — everything is authored for this repository
and kept safe (non-clinical, non-exploitative). See
[`../docs/SAFETY.md`](../docs/SAFETY.md).

## Layout (Milestone 2)

Recorded model outputs live here as `RunRecord` streams, one file per suite and
run version:

```
data/fixtures/
  <suite>/
    baseline.jsonl    recorded baseline (known-good) RunRecords, one per line
    candidate.jsonl   recorded candidate RunRecords with exactly one injected
                      defect per case (or a benign / sub-threshold change, for
                      control cases)
```

`<suite>` is one of `structured_json`, `tool_use`, `retrieval_evidence`,
`refusal_boundary`, `latency_cost`, `agent_trace`. Prompts, permitted tools,
retrieval documents, and optional JSON schemas are carried on each case's
`EvalCase` in `evals/fixtures/<suite>/cases.json`, so they are not duplicated
here; the ground truth for what each candidate should trigger lives beside the
cases in `evals/fixtures/<suite>/expected.json`.

Every file parses against the schemas in
`src/llm_regression_observability/schemas.py` and is checked by
`scripts/01_validate_fixtures.py` / `llmreg validate-fixtures` (Milestone 2).

Generated artifacts (runs, comparisons, reports) are **not** stored here — they
go to `results/` and are gitignored.
