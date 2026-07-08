# Milestone 4 — Run-suite executor

**Status: Complete.** `llmreg run-suite` drives an adapter over a suite and
writes byte-reproducible `results/runs/<run_id>/{run.jsonl,run_meta.json}`;
51 new tests (190 total) pass offline.

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

## Design decisions

Three questions the spec left open, answered here rather than by convention:

**The executor owns run identity.** `RecordedModelAdapter` replays fixtures
verbatim, including each record's own `run_id` (`<case>-baseline`) and
`timestamp` — the Milestone 3 review note left the re-identification question to
this milestone. The executor re-stamps: every record in a run carries the run's
`run_id` and the configured `app_version`, so `(run_id, case_id)` keys the
artifact and `ComparisonRecord.baseline_run_id` / `candidate_run_id` each denote
exactly one run. `model_id`, `prompt_version`, `tool_version` and `timestamp` are
*provenance*, not identity: preserved as the adapter reported them unless
explicitly overridden, with `RunMeta.identity_overrides` recording which the
caller forced. Absent an explicit `--model-id`, a synthetic run names the adapter
that produced it (`scripted-regression-adapter`) rather than a model never called.

**Latency is recorded, never measured.** Timing the adapter would put an ambient
value into a scored field and make artifacts differ run to run. The executor
records the latency the adapter reports (a fixture value). See
`docs/LIMITATIONS.md`.

**Tokens and cost are estimated only where the adapter left them at zero.**
Recorded fixtures carry their own counts and are treated as recorded inputs.
`--recompute-cost` forces the cost model over every record, so two runs can be
priced under one model regardless of what their adapters claimed.
`RunMeta.tokens_estimated_cases` / `cost_estimated_cases` count what was derived,
so an artifact never hides which numbers the executor invented.

## Observed run (smoke suite, 8 cases)

| | baseline (`recorded`) | candidate (`scripted-regression`) |
| --- | --- | --- |
| records | 8 | 8 |
| tokens (in / out) | 1310 / 445 | 1310 / 445 |
| cost estimate | $0.018700 | $0.018700 |
| latency total | 1690.0 ms | 1940.0 ms |
| tool calls | 6 | 5 |
| retrieval sources | 2 | 3 |
| errors (app / infra) | 0 / 0 | 1 / 1 |
| `sha256(run.jsonl)` | `ee23bfde…` | `b486ff9e…` |

The deltas are exactly the injected defects: `+250 ms` from `lc_latency_spike`,
one dropped tool call from `tu_wrong_tool`, one added unsupported source from
`re_unsupported_claim`, one `app` error from `at_more_retries`, one `infra` error
from `at_infra_noise`. Cost is unchanged because the smoke suite carries no
`cost_regression` case. Detection of these deltas is Milestone 6's job; nothing
is claimed here beyond the artifacts.

## Definition of done

**Done.** Run executor produces valid, reproducible `results/runs/` artifacts;
190 tests pass; docs/website updated. Next: Milestone 5 — evaluators.
