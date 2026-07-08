Human review: Milestone 3 — Mock, recorded, and scripted-regression adapters

TL;DR
Added the four deterministic adapters that turn an `EvalCase` into a `RunRecord`
with no model call and no network: `MockModelAdapter`, `RecordedModelAdapter`
(replays the 52 committed fixture records byte for byte), `ScriptedRegressionAdapter`
(15 pure injectors + a plan derived from the Milestone 2 ground truth), and an
inert `ApiAdapterStub`. Run identity (`AdapterContext`) is kept separate from
behavior (`AdapterResponse`), so no scored path reads the clock. Full suite is
139 passed (46 new). Ruff clean. Offline, dependency-free, deterministic. Nothing
pushed.

What changed
- `src/llm_regression_observability/adapters/` — new package:
  - `base.py` — `AdapterContext` (run identity: `app_version`, `model_id`,
    prompt/tool versions, recorded `timestamp`, optional `run_id_prefix`),
    `AdapterResponse` (behavior: final/parsed output, tool calls, retrieval
    sources, errors, tokens/latency/cost, redaction status), `ModelAdapter`
    interface (`generate`, `generate_all`), `build_run_record()`,
    `response_from_record()`, `estimate_tokens()`, `AdapterError`.
  - `mock_model.py` — fixed outputs; per-`case_id` presets or a deterministic
    default (`"mock output for <case_id>"`).
  - `recorded_model.py` — indexes fixtures by `(app_version, case_id)`; returns a
    deep copy of the recorded `RunRecord` **verbatim**. `from_fixtures(path)`
    loads all six suites' baseline + candidate streams.
  - `scripted_model.py` — 15 injectors (`invalid_json`, `drop_json_field`,
    `set_json_field`, `drop_tool`, `wrong_tool`, `add_tool`, `arg_drift`,
    `swap_tool_order`, `unsupported_claim`, `drop_source`, `flip_refusal`,
    `set_parsed_field`, `add_latency`, `add_cost`, `add_error`), an `Injection`
    record, and `from_fixtures()` which derives the `case_id → Injection` plan
    from `CaseExpectation`.
  - `api_stub.py` — `ApiAdapterStub` / `ApiAdapterDisabled`.
- `tests/test_adapters.py` — 46 CPU-only tests.
- Docs/site: milestone doc → Complete (with the observed injection table);
  `site/architecture.html` gained a full Adapters section; `site/index.html` and
  `site/milestones.html` status/implementation updated.

What should be checked later
- **Scripted output is deliberately NOT byte-identical to the recorded
  candidates.** The recorded candidates (Milestone 2) remain the primary ground
  truth; the scripted adapter independently generates the same defect
  *categories*. Exact reproduction was rejected on purpose: several recorded
  candidates carry hand-authored secondary changes a generic injector cannot
  derive (`sj_extra_field` bumps `output_tokens_estimate` 40→44,
  `re_unsupported_claim` 70→78, `at_more_retries` latency 300→360). Encoding
  those as per-case parameters would have re-encoded the fixture data inside the
  adapter. **Decide for Milestone 6** which stream the detector is scored
  against — recorded candidates, scripted candidates, or both.
- **One injector per category is representative, not exhaustive.** The plan maps
  `structured_output_regression → invalid_json`, `tool_call_regression →
  drop_tool`, `evidence_grounding_regression → unsupported_claim`,
  `refusal_behavior_shift → flip_refusal`, `latency_regression → add_latency
  (+250 ms)`, `cost_regression → add_cost (+$0.004)`, `error_rate_regression →
  add_error (app)`. The other injectors are implemented and unit-tested but not
  reachable from the default plan. If Milestone 6 wants per-defect coverage
  (wrong tool *and* missing tool *and* arg drift), the plan needs richer
  directives — the natural place is a machine-readable `injection` field on
  `CaseExpectation`.
- **Latency/cost injection deltas are hard-coded** (+250 ms, +$0.004) and chosen
  to clear the smoke thresholds (`latency_abs_ms: 50`, `cost_rel: 0.20`). If
  those thresholds move, these defaults must move with them or the scripted
  candidates stop being detectable. No test currently couples the two.
- **Recorded adapter replays verbatim** — it preserves the recorded `run_id`,
  `timestamp`, and `model_id`, using the context only to pick the
  baseline/candidate stream. This resolves the open question from the Milestone 2
  review note in favor of *preserve*. Milestone 4's executor must therefore decide
  explicitly whether it re-stamps replayed runs; if it does, the "reproduces
  fixtures exactly" test will need to move down a layer.
- **`estimate_tokens()` is a placeholder** (~4 chars/token, no tokenizer). It is
  only used by `MockModelAdapter`; the recorded adapter uses fixture values.
  Real accounting belongs to Milestones 4 and 11.
- **True controls receive no injection at all**, so a scripted control is
  behaviorally identical to its baseline. The recorded controls are subtler
  (a benign JSON key reorder; a sub-threshold latency drift 150→180 ms). The
  scripted controls therefore test "no change must not flag" but not "benign
  change must not flag" — the recorded controls still carry that burden.

Technical risks
- Low. Pure stdlib (`json`, `copy`, `dataclasses`, `pathlib`); no network, clock,
  RNG, paid API, or secrets. `ApiAdapterStub` is inert twice over: disabled by
  default it raises `ApiAdapterDisabled`, and with `enabled=True` it still raises
  `NotImplementedError`, so no default path can reach a paid API before
  Milestone 11. Adapters return deep copies, so a caller mutating one record
  cannot corrupt the shared fixture index (covered by a test).

Commands to verify
    python3 -m pytest -q                                   # 139 passed
    python3 -m pytest -q tests/test_adapters.py            # 46 passed
    python3 scripts/01_validate_fixtures.py evals/fixtures # PASS (unchanged)
    ruff check src tests conftest.py                       # clean (optional dev extra)

Human feedback
<!-- Human will fill this later. -->
