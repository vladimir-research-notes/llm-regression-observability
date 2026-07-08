# Milestone 2 — Fixture suites

**Status: Complete.** Six synthetic suites (26 cases: 18 injected defects + 8
controls) ship under `evals/fixtures/` (cases + ground-truth expectations) and
`data/fixtures/` (recorded baseline/candidate `RunRecord`s). The
`llm_regression_observability.datasets` package loads and validates them; a
restricted stdlib-only YAML reader parses `evals/suites/smoke.yaml`. Validation
(`python3 scripts/01_validate_fixtures.py evals/fixtures` and `llmreg
validate-fixtures`) passes; `python3 -m pytest -q` → 93 passed (25 new).

## Goal

Create the small, committed, **synthetic** fixtures that drive every downstream
scorer, plus a validator. Each suite ships a baseline (valid) output set and a
candidate output set with **known, injected** defects, so the detector can later
be tested against ground truth.

## Deliverables

Under `evals/fixtures/` (cases + expected) and `data/fixtures/` (prompts,
outputs, tool traces, retrieval docs), six suites:

1. `structured_json/` — valid JSON at baseline; candidate adds invalid JSON,
   missing required fields, wrong types, extra fields, plausible-but-invalid.
2. `tool_use/` — expected tool patterns; candidate adds wrong/missing/extra
   tool, changed args, order change.
3. `retrieval_evidence/` — cited answers; candidate adds uncited claims, wrong
   source ids, unsupported snippets, missing evidence.
4. `refusal_boundary/` — safe synthetic prompts; candidate adds over/under
   refusal, changed risk label.
5. `latency_cost/` — recorded traces with latency/cost differences.
6. `agent_trace/` — synthetic multi-step traces; candidate adds retries, failed
   tool calls, policy violations, longer runtime, correct-but-worse process.

Plus `evals/suites/smoke.yaml` referencing a subset for the end-to-end smoke run,
and a fixture-validation entry point (`llmreg validate-fixtures` /
`scripts/01_validate_fixtures.py`).

## Validation

```bash
python3 scripts/01_validate_fixtures.py evals/fixtures
python3 -m pytest -q tests/test_fixtures.py
```

Validation checks every fixture parses against the schemas, every candidate's
injected defect is annotated with its expected regression category, and no
fixture contains secret-like or non-synthetic content.

## Rationale

Ground-truth fixtures are what let Milestone 6 assert "the injected regression
was detected" as a stable test rather than a hope.

## Implementation notes

- **Layout.** Per suite: `evals/fixtures/<suite>/cases.json` (a list of
  `EvalCase`), `evals/fixtures/<suite>/expected.json` (a list of the new
  `CaseExpectation` ground-truth records), and `data/fixtures/<suite>/{baseline,
  candidate}.jsonl` (recorded `RunRecord`s). Every file parses against the
  Milestone 1 schemas as it loads.
- **One injected defect per candidate case.** Baseline and candidate differ on
  exactly the dimension under test (JSON validity, tool calls, evidence, refusal,
  latency/cost, or error/retry rate); the validator asserts a non-control case is
  actually behaviorally different from its baseline, so an annotation cannot
  describe a defect that was never injected. Control cases prove benign changes
  (e.g. a JSON key reorder) and sub-threshold drifts do not flag.
- **Attribution ground truth.** `agent_trace/at_infra_noise` carries an
  `infra`-attributed error with `expected_category: none` — the fixture that
  Milestone 6 uses to prove infra noise is surfaced but not flagged (research
  question 9).
- **Restricted YAML.** `datasets/_yaml.py` parses only the small subset the
  manifests use (mappings, nested mappings, scalar sequences, comments) and
  raises on anything else, so the dependency-free-core constraint holds without
  PyYAML.
- **Safety scan.** The validator scans every fixture for credential-like
  patterns (a lightweight precursor to the Milestone 9 redactor); all fixtures
  are synthetic and scan clean.

## Definition of done

Fixtures validate; suite manifest loads and resolves; docs/website updated.
**Done.** Next: Milestone 3 — adapters (mock / recorded / scripted-regression),
which replay these recorded runs.
