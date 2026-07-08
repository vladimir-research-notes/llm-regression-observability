# Milestone 1 — Core schemas and trace model

**Status: Complete.** All contracts in `schemas.py` implemented as stdlib
dataclasses with type-driven validation and byte-stable `to_dict`/`to_json`/
`from_dict`; 54 unit tests cover round-trip, rejection, and serialization
stability (`python3 -m pytest -q` → 68 passed).

## Goal

Implement the typed data contracts that every later milestone reads and writes.
The human-readable contract is [`../TRACE_FORMAT.md`](../TRACE_FORMAT.md); this
milestone makes it code.

## Deliverables

In `src/llm_regression_observability/schemas.py`, typed models (stdlib
`dataclasses` + explicit validation; `pydantic` optional, never required):

- `EvalCase`, `EvalSuite`
- `ModelTrace` / `RunRecord`, `ToolCallTrace`, `RetrievalTrace`, `ErrorRecord`
- `MetricResult`
- `ComparisonRecord`, `RegressionFlag`
- `ReportSummary`

Each model provides deterministic `to_dict()` / `from_dict()` with **sorted keys
and fixed field order** for byte-stable serialization, plus validation that
raises clear errors on malformed input.

## Validation

```bash
python3 -m pytest -q tests/test_schemas.py
```

Unit tests cover: round-trip (`from_dict(to_dict(x)) == x`), rejection of missing
required fields and wrong types, and byte-stable JSON output.

## Rationale

Fixing the contracts first means the executor, evaluators, comparison engine, and
reports all agree on shapes, and artifacts on disk are diff-friendly and
reproducible.

## Definition of done

All schema tests pass; serialization is byte-stable; docs/website updated. Next:
Milestone 2 — fixture suites.
