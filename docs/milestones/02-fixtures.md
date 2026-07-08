# Milestone 2 — Fixture suites

**Status: planned.**

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

## Definition of done

Fixtures validate; suite manifest loads; docs/website updated. Next: Milestone 3
— adapters.
