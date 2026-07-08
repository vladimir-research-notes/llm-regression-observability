# Milestone 3 — Mock, recorded, and scripted-regression adapters

**Status: planned.**

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

## Definition of done

Adapter tests pass; API stub is inert by default; docs/website updated. Next:
Milestone 4 — run-suite executor.
