Human review: Milestone 1 — Core schemas and trace model

TL;DR
Turned the human-readable trace contract (`docs/TRACE_FORMAT.md`) into code:
`src/llm_regression_observability/schemas.py` defines every typed model the rest
of the pipeline reads and writes, as stdlib `dataclasses` with annotation-driven
validation and byte-stable serialization. 54 new tests; full suite is 68 passed.
Dependency-free, deterministic, offline. Nothing pushed.

What changed
- `src/llm_regression_observability/schemas.py` — new. A `Schema` base plus:
  `RetrievalDoc`, `EvalCase`, `EvalSuite`, `ToolCallTrace`, `RetrievalTrace`,
  `ErrorRecord`, `RunRecord` (alias `ModelTrace`), `MetricResult`,
  `RegressionFlag`, `ComparisonRecord`, `FailureCluster`, `ReportSummary`, and a
  `SchemaError` (subclass of `ValueError`).
- Each model gets `to_dict()` (declared field order), `to_json()` (sorted keys,
  compact separators — byte-stable), `from_dict()`, and `from_json()`.
- Validation is driven by the field type annotations (no per-field boilerplate):
  it coerces `int` → `float` for float fields, rejects `bool` where `int`/`float`
  is expected, and raises `SchemaError` — naming the path — on a missing required
  field, an unknown field, a wrong type, or an out-of-vocabulary enum value.
- Closed vocabularies enforced: `EvalCase.risk_category`, `ToolCallTrace.status`,
  `ErrorRecord.origin`, `RunRecord.redaction_status`,
  `RegressionFlag.severity` / `.attribution`.
- `tests/test_schemas.py` — new, 54 tests: parametrized round-trip (dict + JSON),
  nested-object reconstruction, key-sorting at every level, byte-identical output
  across equal instances, int→float coercion, and rejection of every malformed
  case above.
- Docs/site: milestone doc flipped to Complete; `site/trace-format.html`,
  `site/index.html`, `site/milestones.html` status/implementation updated.

What should be checked later
- **Unknown-field rejection is strict.** `from_dict()` rejects keys it does not
  recognize. This is deliberate (catches typos and contract drift) but means old
  artifacts cannot be read after a field is *removed*; confirm that is the
  desired policy vs. lenient forward-compat.
- **Enum vocabularies are closed.** If a later milestone needs a new
  `status`/`origin`/`severity` value it must extend the `_ALLOWED` set. `app_version`
  is intentionally *not* constrained (free label per the plan).
- **`float` coercion.** `int` inputs to float fields are stored as `float` so JSON
  output is stable (`120` → `120.0`). Confirm no downstream code depends on the
  original `int` identity.
- **`Schema` is not frozen.** Instances are mutable; `__post_init__` normalizes
  floats in place. If immutability/hashing is wanted later, revisit.

Technical risks
- Low. Pure stdlib (`dataclasses`, `json`, `typing`, `types`); no network, no
  clock, no RNG, no paid API, no secrets. Determinism preserved (byte-stable
  output verified by tests). Core runtime dependency list stays empty.

Commands to verify
    python3 -m pytest -q                         # 68 passed
    python3 -m pytest -q tests/test_schemas.py   # 54 passed
    ruff check src tests conftest.py             # clean (optional; dev extra)

Human feedback
<!-- Human will fill this later. -->
