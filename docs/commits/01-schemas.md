# Milestone 1 — Core schemas and trace model

## What changed
Made the trace contract in `docs/TRACE_FORMAT.md` executable. Added
`src/llm_regression_observability/schemas.py` and `tests/test_schemas.py`.

- **`schemas.py`** — a `Schema` base class plus twelve typed models:
  `RetrievalDoc`, `EvalCase`, `EvalSuite`, `ToolCallTrace`, `RetrievalTrace`,
  `ErrorRecord`, `RunRecord` (with `ModelTrace = RunRecord`), `MetricResult`,
  `RegressionFlag`, `ComparisonRecord`, `FailureCluster`, `ReportSummary`, and a
  `SchemaError(ValueError)`.
- Each model is a `@dataclass(kw_only=True)` and inherits:
  - `to_dict()` — plain dict in declared field order (recursive over nested
    models and containers);
  - `to_json()` — `json.dumps(..., sort_keys=True)` with compact separators, so
    output is **byte-stable** across runs;
  - `from_dict()` / `from_json()` — reconstruct (rebuilding nested models to
    their proper types) with validation.
- **Validation is annotation-driven.** A small type-walker reads each field's
  resolved type hint and: coerces `int` → `float` for float fields; rejects
  `bool` where `int`/`float` is expected; validates `list[...]`, `dict[str, V]`,
  `X | None`, `Any`, primitives, and nested `Schema` subclasses; and raises
  `SchemaError` (naming the offending path) on a missing required field, an
  unknown field, or a wrong type.
- **Closed vocabularies** are enforced via a per-class `_ALLOWED` map:
  `risk_category`, tool `status`, error `origin`, `redaction_status`, flag
  `severity`, flag `attribution`.
- **`test_schemas.py`** — 54 CPU-only tests: parametrized round-trip (dict and
  JSON) over every model, nested reconstruction, sorted-keys-at-every-level,
  byte-identical output across equal instances, `int`→`float` coercion, and
  rejection of missing/unknown/wrong-type/bad-enum inputs.

## Why / key decisions
- **Annotation-driven, not per-field, validation** keeps the module DRY: adding a
  field to a contract needs no extra (de)serialization code, which matters
  because these shapes are read/written by every later milestone.
- **`to_dict` keeps field order; `to_json` sorts keys.** Both are deterministic;
  sorted-key JSON is what actually lands in `results/` artifacts, so diffs are
  meaningful and two identical runs are byte-identical (the determinism rule).
- **`int` → `float` coercion** prevents numeric drift in artifacts: a latency of
  `120` and `120.0` must serialize identically.
- **Strict unknown-field rejection and closed enums** make malformed input fail
  loudly and early — the milestone's "clear errors on malformed input" goal.
  Trade-offs noted in the review placeholder.
- **Stdlib only** (`dataclasses`, `json`, `typing`, `types`): the core stays
  dependency-free; `pydantic` remains an unused optional alternative.

## Validation run
- `python3 -m pytest -q` → **68 passed** (14 scaffold + 54 schema).
- `python3 -m pytest -q tests/test_schemas.py` → 54 passed.
- `ruff check src tests conftest.py` → clean (optional; ruff is a dev extra).

Next: Milestone 2 — fixture suites.
