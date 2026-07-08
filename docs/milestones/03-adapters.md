# Milestone 3 — Mock, recorded, and scripted-regression adapters

**Status: Complete.** Four deterministic adapters ship under
`src/llm_regression_observability/adapters/`: `MockModelAdapter`,
`RecordedModelAdapter` (replays the committed fixtures byte for byte),
`ScriptedRegressionAdapter` (15 named injectors + a plan derived from the
Milestone 2 ground truth), and an inert `ApiAdapterStub`. `python3 -m pytest -q`
→ 139 passed (46 new).

## Goal

Provide the deterministic adapters that turn an `EvalCase` into a model output,
so the whole pipeline runs with **no model call and no network**.

## Deliverables

In `src/llm_regression_observability/adapters/`:

- `base` — a small adapter interface: `generate(case) -> ModelTrace/RunRecord`
  fields (output, tool calls, retrieval, tokens, latency).
- `mock_model.py` — `MockModelAdapter`: fixed outputs for tests.
- `recorded_model.py` — `RecordedModelAdapter`: replays committed fixture outputs
  keyed by `case_id`.
- `scripted_model.py` — `ScriptedRegressionAdapter`: deterministically injects
  known regressions (invalid JSON, wrong tool, dropped citation, added latency,
  etc.) driven by fixture annotations.
- `api_stub.py` — `ApiAdapterStub`: interface only, **disabled by default**,
  raises if invoked without explicit opt-in (fully built in Milestone 11).

## Validation

```bash
python3 -m pytest -q tests/test_adapters.py
```

Tests assert: adapters are deterministic (same input → identical output), the
recorded adapter reproduces committed fixtures exactly, the scripted adapter
injects exactly the annotated defects, and the API stub refuses to run by
default.

## Rationale

Deterministic adapters are the backbone of reproducibility. The scripted adapter
is the ground-truth generator the detector is later measured against.

## Implementation notes

- **Identity is separated from behavior.** `AdapterContext` carries run-level
  identity (`app_version`, `model_id`, prompt/tool versions, the recorded
  `timestamp`); `AdapterResponse` carries behavioral output (final/parsed output,
  tool calls, retrieval sources, errors, tokens/latency/cost).
  `build_run_record()` stitches them into a `RunRecord`. The timestamp is an
  **input**, never `datetime.now()`, so the determinism rule holds by
  construction. `run_id` defaults to `"<case_id>-<app_version>"`.
- **Recorded = verbatim replay.** `RecordedModelAdapter` indexes fixtures by
  `(app_version, case_id)` and returns a deep copy of the recorded `RunRecord`
  unchanged — so it reproduces `data/fixtures/**` byte for byte (asserted for all
  52 records). It uses the context only to pick the baseline/candidate stream;
  the executor (Milestone 4) owns any re-identification of replayed runs. This
  settles the open question raised in the Milestone 2 review note.
- **Scripted = injector library + plan.** Fifteen small, pure injectors
  (`invalid_json`, `drop_json_field`, `set_json_field`, `drop_tool`,
  `wrong_tool`, `add_tool`, `arg_drift`, `swap_tool_order`, `unsupported_claim`,
  `drop_source`, `flip_refusal`, `set_parsed_field`, `add_latency`, `add_cost`,
  `add_error`) each transform a clean `AdapterResponse` copy along exactly one
  dimension. `from_fixtures()` builds the `case_id → Injection` plan from the
  Milestone 2 `CaseExpectation` ground truth: a non-control case gets a
  representative injector for its `expected_category`; an `infra`-attributed case
  gets an `infra`-origin error; a true control gets **no** injection.
- **Scripted output is not byte-identical to the recorded candidates**, and does
  not claim to be. The recorded candidates (Milestone 2) remain the primary
  ground truth; the scripted adapter is an independent, mechanical generator of
  the *same categories* of defect. Some recorded candidates carry hand-authored
  secondary changes (e.g. `sj_extra_field` bumps `output_tokens_estimate` 40→44)
  that a generic injector cannot derive. See the review note.
- **API stub is inert twice over.** Disabled by default it raises
  `ApiAdapterDisabled`; even with `enabled=True` it raises `NotImplementedError`.
  No default path — and no accidental call — can reach a paid API before
  Milestone 11.

### Observed injection behavior (smoke suite)

Baseline via `RecordedModelAdapter`, candidate via `ScriptedRegressionAdapter`,
exactly as `docs/PLAN.md` §9 wires them. Only the fields belonging to each
category change; the true control changes nothing.

| case | expected_category | attribution | changed behavioral fields |
| --- | --- | --- | --- |
| `sj_invalid_json` | `structured_output_regression` | app | `final_output`, `parsed_output` |
| `tu_wrong_tool` | `tool_call_regression` | app | `tool_calls` |
| `re_unsupported_claim` | `evidence_grounding_regression` | app | `final_output`, `retrieval_sources` |
| `rb_underrefuse` | `refusal_behavior_shift` | app | `final_output`, `parsed_output` |
| `lc_latency_spike` | `latency_regression` | app | `latency_ms` |
| `at_more_retries` | `error_rate_regression` | app | `errors`, `tool_calls` |
| `at_infra_noise` | `none` | **infra** | `errors`, `tool_calls` |
| `sj_control_reorder` | `none` | app | *(none)* |

The candidate stream hashes to the same SHA-256 across separate processes
(`436b83f7…`), confirming byte-level reproducibility.

## Definition of done

Adapter tests pass; API stub is inert by default; docs/website updated.
**Done.** Next: Milestone 4 — run-suite executor, which drives these adapters
over a suite and writes `results/runs/<run_id>/`.
