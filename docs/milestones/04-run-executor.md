# Milestone 4 — Run-suite executor

**Status: planned.**

## Goal

Run a suite through an adapter and produce `RunRecord`s and traces on disk.

## Deliverables

- `datasets/loader.py` — load an `EvalSuite`/cases from `evals/`.
- Run executor (library + `llmreg run-suite`) that: reads cases, calls the
  adapter, records final output and parsed output, records tool calls and
  retrieval sources, estimates input/output tokens and cost, records latency,
  and writes `results/runs/<run_id>/run.jsonl` (+ a `run_meta.json`).
- `traces/storage.py` — JSONL read/write helpers with byte-stable serialization.
- `scripts/02_run_baseline.py`, `scripts/03_run_candidate.py` — thin wrappers.

CLI:

```bash
llmreg run-suite --suite evals/suites/smoke.yaml --adapter recorded \
    --app-version baseline --out results/runs/
```

## Validation

```bash
python3 -m pytest -q tests/test_run_executor.py
```

Tests assert: a run over the recorded adapter produces one `RunRecord` per case;
token/cost/latency fields are populated deterministically; the output JSONL
round-trips through the schemas; and two runs on the same inputs are
byte-identical except for `run_id`.

## Rationale

This is the "traces in, traces out" core. Everything downstream consumes its
artifacts.

## Definition of done

Run executor produces valid, reproducible `results/runs/` artifacts; tests pass;
docs/website updated. Next: Milestone 5 — evaluators.
