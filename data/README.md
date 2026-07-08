# data/

Small, committed, **synthetic** fixture inputs used by the evaluation suites.
There is **no real user data** here — everything is authored for this repository
and kept safe (non-clinical, non-exploitative). See
[`../docs/SAFETY.md`](../docs/SAFETY.md).

## Layout (populated in Milestone 2)

```
data/fixtures/
  prompts/          synthetic prompt texts
  outputs/          baseline (valid) and candidate (defective) model outputs
  tool_traces/      synthetic tool-call sequences
  retrieval_docs/   small documents answers must be grounded in
```

Fixtures under `data/fixtures/` are the raw inputs; the case/suite manifests that
reference them live under `evals/fixtures/` and `evals/suites/`. All fixtures are
validated by `scripts/01_validate_fixtures.py` (Milestone 2).

Generated artifacts (runs, comparisons, reports) are **not** stored here — they
go to `results/` and are gitignored.
