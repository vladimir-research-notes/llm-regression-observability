# Milestone 3 — Adapters

## What changed
Added the deterministic adapter layer: the only thing standing where a real model
would be. An adapter takes an `EvalCase` and returns a `RunRecord`, so every
downstream stage runs offline with no model call and no network.

- **`src/llm_regression_observability/adapters/`** — new package:
  - `base.py` — the interface and shared plumbing. `AdapterContext` (run identity:
    `app_version`, `model_id`, prompt/tool versions, recorded `timestamp`,
    optional `run_id_prefix`), `AdapterResponse` (behavior: final/parsed output,
    tool calls, retrieval sources, errors, tokens/latency/cost, redaction status),
    `ModelAdapter` (`generate`, `generate_all`), `build_run_record()`,
    `response_from_record()`, `estimate_tokens()`, `AdapterError`.
  - `mock_model.py` — `MockModelAdapter`: per-`case_id` presets, or a
    deterministic default output with estimated tokens.
  - `recorded_model.py` — `RecordedModelAdapter`: indexes the committed fixtures
    by `(app_version, case_id)` and replays each `RunRecord` **verbatim**.
  - `scripted_model.py` — `ScriptedRegressionAdapter`: 15 pure injectors plus an
    `Injection` record and a fixture-derived injection plan.
  - `api_stub.py` — `ApiAdapterStub` / `ApiAdapterDisabled`: interface only,
    disabled by default.
- **`tests/test_adapters.py`** — 46 CPU-only tests.
- Docs/site: milestone doc → Complete; `site/architecture.html` gained an Adapters
  section; `site/index.html` and `site/milestones.html` updated.

## Why / key decisions
- **Identity separated from behavior.** Splitting `AdapterContext` (which run this
  is) from `AdapterResponse` (what the model produced) makes the determinism rule
  structural rather than conventional: the timestamp is an input threaded through
  the context, so there is no code path where a scored field could pick up
  `datetime.now()`. `build_run_record()` is the single join point.
- **The recorded adapter replays verbatim.** It returns the recorded `RunRecord`
  unchanged (deep-copied), using the context only to select the baseline or
  candidate stream. A test asserts all 52 committed records round-trip byte for
  byte. This settles the open question from the Milestone 2 review note in favor
  of *preserve*; the executor (Milestone 4) owns any re-identification.
- **The scripted adapter is an injector library plus a plan, not a data blob.**
  Fifteen small pure transforms each move exactly one dimension of a clean
  baseline. The `case_id → Injection` plan is derived from the Milestone 2
  `CaseExpectation` ground truth: a non-control case gets a representative defect
  of its annotated category, an `infra`-attributed case gets an `infra`-origin
  error (surfaced, never flagged — research question 9), and a true control gets
  no injection. That is what makes "only the annotated dimension changed" an
  assertion instead of a hope.
- **Scripted output is not byte-identical to the recorded candidates — on
  purpose.** Exact reproduction would have required per-case parameters encoding
  each fixture's hand-authored secondary changes (`sj_extra_field` bumps
  `output_tokens_estimate` 40→44; `at_more_retries` latency 300→360), i.e.
  re-encoding the fixture data inside the adapter. The recorded candidates stay
  the primary ground truth; the scripted adapter is an independent mechanical
  generator of the same defect *categories*. Documented, not glossed.
- **The API stub is inert twice over.** Disabled by default it raises
  `ApiAdapterDisabled`; even with `enabled=True` it raises `NotImplementedError`.
  No default path — and no accidental call — can reach a paid API before
  Milestone 11.
- **Adapters hand back deep copies**, so a caller mutating one record cannot
  corrupt the shared fixture index. Tested.

## Validation run
- `python3 -m pytest -q` → **139 passed** (14 scaffold + 54 schema + 25 fixtures +
  46 adapters).
- `python3 -m pytest -q tests/test_adapters.py` → 46 passed.
- `python3 scripts/01_validate_fixtures.py evals/fixtures` → PASS (unchanged).
- `ruff check src tests conftest.py` → clean (optional; ruff is a dev extra).
- End-to-end spot check (recorded baseline → scripted candidate over
  `evals/suites/smoke.yaml`): each injected defect changes only the behavioral
  fields belonging to its category, the true control changes nothing, and the
  candidate stream hashes to the same SHA-256 (`436b83f7…`) across separate
  processes.

Next: Milestone 4 — run-suite executor (`llmreg run-suite`), which drives these
adapters over a suite and writes `results/runs/<run_id>/`.
