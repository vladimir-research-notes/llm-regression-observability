"""Core data contracts for llm-regression-observability.

This module is the code form of ``docs/TRACE_FORMAT.md``. Every later milestone
(executor, evaluators, comparison engine, reports, observability) reads and
writes these types, so fixing the shapes here means the whole pipeline agrees on
one contract and the artifacts on disk are diff-friendly and reproducible.

Design constraints (see ``CLAUDE.md`` / ``docs/PLAN.md`` §3):

* **Standard library only.** Typed models are plain :mod:`dataclasses` with
  explicit validation. ``pydantic`` is an *optional* alternative and is never
  imported here.
* **Deterministic serialization.** ``to_dict()`` preserves the declared field
  order; ``to_json()`` serializes with **sorted keys** and compact separators,
  so two runs on the same inputs produce **byte-identical** output. Nothing in
  this module calls the clock or an RNG.
* **Clear validation.** Construction (and :meth:`Schema.from_dict`) validates
  types, coerces ``int`` into ``float`` where a float is declared, and rejects
  missing required fields, unknown fields, wrong types, and out-of-vocabulary
  enum values, raising :class:`SchemaError` with the offending path.

The public types are:

``EvalCase``, ``EvalSuite``, ``RetrievalDoc`` — evaluation inputs.
``RunRecord`` (alias ``ModelTrace``), ``ToolCallTrace``, ``RetrievalTrace``,
``ErrorRecord`` — execution traces.
``MetricResult`` — evaluator output.
``ComparisonRecord``, ``RegressionFlag`` — comparison output.
``ReportSummary``, ``FailureCluster`` — aggregate report inputs.
"""

from __future__ import annotations

import json
import types
from dataclasses import MISSING, dataclass, field, fields
from typing import Any, ClassVar, Union, get_args, get_origin, get_type_hints

__all__ = [
    "SchemaError",
    "Schema",
    "RetrievalDoc",
    "EvalCase",
    "EvalSuite",
    "ToolCallTrace",
    "RetrievalTrace",
    "ErrorRecord",
    "RunRecord",
    "ModelTrace",
    "MetricResult",
    "RegressionFlag",
    "ComparisonRecord",
    "FailureCluster",
    "ReportSummary",
]

_NONE_TYPE = type(None)


class SchemaError(ValueError):
    """Raised when data does not satisfy a schema.

    Subclasses :class:`ValueError` so callers can catch it as a validation
    error. The message always names the offending path (``Class.field`` or a
    nested ``[index]`` / ``[key]``) so malformed input is easy to locate.
    """


# --------------------------------------------------------------------------- #
# Type-driven (de)serialization helpers.
#
# The conversion is driven entirely by each dataclass's annotations, so there is
# no per-field boilerplate: adding a field to a schema below is enough. The
# supported type forms are the ones the contract uses — primitives, ``Any``,
# ``dict`` / ``dict[str, V]``, ``list[T]``, ``X | None``, and nested ``Schema``
# subclasses. Anything else falls back to an ``isinstance`` check.
# --------------------------------------------------------------------------- #

_HINTS_CACHE: dict[type, dict[str, Any]] = {}


def _hints(cls: type) -> dict[str, Any]:
    """Resolved type hints for ``cls`` (cached; annotations are strings here)."""
    cached = _HINTS_CACHE.get(cls)
    if cached is None:
        cached = get_type_hints(cls)
        _HINTS_CACHE[cls] = cached
    return cached


def _union_args(hint: Any) -> tuple[Any, ...] | None:
    """Return the members of a ``Union`` / ``X | None`` hint, else ``None``."""
    origin = get_origin(hint)
    if origin is Union or origin is types.UnionType:
        return get_args(hint)
    return None


def _typename(hint: Any) -> str:
    return getattr(hint, "__name__", str(hint))


def _to_jsonable(value: Any) -> Any:
    """Recursively convert a field value into JSON-serializable data."""
    if isinstance(value, Schema):
        return value.to_dict()
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


def _build(value: Any, hint: Any, path: str) -> Any:
    """Reconstruct ``value`` for a field typed ``hint`` when loading from data.

    Nested :class:`Schema` objects are rebuilt from their dicts; containers are
    walked; primitives and free-form payloads (``Any`` / bare ``dict``) pass
    through and are type-checked later by ``__post_init__``.
    """
    if hint is Any or hint is object:
        return value

    union = _union_args(hint)
    if union is not None:
        if value is None:
            if _NONE_TYPE in union:
                return None
            raise SchemaError(f"{path}: got null, expected {_typename(hint)}")
        non_none = [a for a in union if a is not _NONE_TYPE]
        return _build(value, non_none[0], path)

    origin = get_origin(hint)
    if origin is list:
        if not isinstance(value, list):
            raise SchemaError(f"{path}: expected list, got {type(value).__name__}")
        (elem_t,) = get_args(hint) or (Any,)
        return [_build(v, elem_t, f"{path}[{i}]") for i, v in enumerate(value)]
    if origin is dict:
        if not isinstance(value, dict):
            raise SchemaError(f"{path}: expected dict, got {type(value).__name__}")
        args = get_args(hint)
        if args:
            _, val_t = args
            return {k: _build(v, val_t, f"{path}[{k!r}]") for k, v in value.items()}
        return value

    if isinstance(hint, type) and issubclass(hint, Schema):
        return hint.from_dict(value, _path=path)

    # Primitive or bare container: leave as-is; validated on construction.
    return value


def _coerce_validate(value: Any, hint: Any, path: str) -> Any:
    """Validate ``value`` against ``hint``, coercing ``int`` → ``float``.

    Returns the (possibly coerced) value or raises :class:`SchemaError`.
    """
    if hint is Any or hint is object:
        return value

    union = _union_args(hint)
    if union is not None:
        if value is None:
            if _NONE_TYPE in union:
                return None
            raise SchemaError(f"{path}: got null, expected {_typename(hint)}")
        for arg in union:
            if arg is _NONE_TYPE:
                continue
            try:
                return _coerce_validate(value, arg, path)
            except SchemaError:
                continue
        raise SchemaError(
            f"{path}: {type(value).__name__} matches no member of {_typename(hint)}"
        )

    origin = get_origin(hint)
    if origin is list:
        if not isinstance(value, list):
            raise SchemaError(f"{path}: expected list, got {type(value).__name__}")
        (elem_t,) = get_args(hint) or (Any,)
        out: list[Any] = []
        changed = False
        for i, item in enumerate(value):
            new = _coerce_validate(item, elem_t, f"{path}[{i}]")
            out.append(new)
            changed = changed or new is not item
        return out if changed else value
    if origin is dict:
        if not isinstance(value, dict):
            raise SchemaError(f"{path}: expected dict, got {type(value).__name__}")
        args = get_args(hint)
        if not args:
            return value
        key_t, val_t = args
        out_d: dict[Any, Any] = {}
        changed = False
        for key, item in value.items():
            if key_t is str and not isinstance(key, str):
                raise SchemaError(f"{path}: dict key {key!r} must be str")
            new = _coerce_validate(item, val_t, f"{path}[{key!r}]")
            out_d[key] = new
            changed = changed or new is not item
        return out_d if changed else value

    if isinstance(hint, type):
        if issubclass(hint, Schema):
            if not isinstance(value, hint):
                raise SchemaError(
                    f"{path}: expected {hint.__name__}, got {type(value).__name__}"
                )
            return value
        # ``bool`` is a subclass of ``int``; treat the two as distinct.
        if hint is bool:
            if not isinstance(value, bool):
                raise SchemaError(f"{path}: expected bool, got {type(value).__name__}")
            return value
        if hint is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise SchemaError(f"{path}: expected int, got {type(value).__name__}")
            return value
        if hint is float:
            if isinstance(value, bool):
                raise SchemaError(f"{path}: expected float, got bool")
            if isinstance(value, int):
                return float(value)
            if isinstance(value, float):
                return value
            raise SchemaError(f"{path}: expected float, got {type(value).__name__}")
        if not isinstance(value, hint):
            raise SchemaError(
                f"{path}: expected {hint.__name__}, got {type(value).__name__}"
            )
        return value

    return value


class Schema:
    """Base class for every data contract.

    Provides deterministic serialization and validation on top of a
    ``@dataclass(kw_only=True)`` subclass. It is intentionally *not* itself a
    dataclass; subclasses inherit :meth:`__post_init__`, which the generated
    ``__init__`` invokes, so validation runs on every construction (including
    :meth:`from_dict`).
    """

    # Per-field closed vocabularies (enum-like). Subclasses override.
    _ALLOWED: ClassVar[dict[str, frozenset[str]]] = {}

    def __post_init__(self) -> None:
        cls = type(self)
        hints = _hints(cls)
        for f in fields(cls):  # type: ignore[arg-type]
            value = getattr(self, f.name)
            new = _coerce_validate(value, hints[f.name], f"{cls.__name__}.{f.name}")
            if new is not value:
                setattr(self, f.name, new)
        for name, allowed in self._ALLOWED.items():
            value = getattr(self, name)
            if value not in allowed:
                raise SchemaError(
                    f"{cls.__name__}.{name}: {value!r} not in {sorted(allowed)}"
                )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict in declared field order (recursively)."""
        return {f.name: _to_jsonable(getattr(self, f.name)) for f in fields(self)}  # type: ignore[arg-type]

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize to JSON with **sorted keys** — byte-stable across runs."""
        separators = (",", ": ") if indent is not None else (",", ":")
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            ensure_ascii=False,
            separators=separators,
            indent=indent,
        )

    @classmethod
    def from_dict(cls, data: Any, *, _path: str | None = None) -> Any:
        """Construct an instance from a plain dict, validating as it goes."""
        path = _path or cls.__name__
        if not isinstance(data, dict):
            raise SchemaError(
                f"{path}: expected object, got {type(data).__name__}"
            )
        known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        unknown = set(data) - known
        if unknown:
            raise SchemaError(
                f"{path}: unknown field(s) {sorted(unknown)}; "
                f"allowed: {sorted(known)}"
            )
        hints = _hints(cls)
        kwargs: dict[str, Any] = {}
        for f in fields(cls):  # type: ignore[arg-type]
            if f.name in data:
                kwargs[f.name] = _build(
                    data[f.name], hints[f.name], f"{path}.{f.name}"
                )
            elif f.default is MISSING and f.default_factory is MISSING:
                raise SchemaError(f"{path}: missing required field '{f.name}'")
        return cls(**kwargs)

    @classmethod
    def from_json(cls, text: str) -> Any:
        """Construct an instance from a JSON string."""
        return cls.from_dict(json.loads(text))


# --------------------------------------------------------------------------- #
# Evaluation inputs
# --------------------------------------------------------------------------- #


@dataclass(kw_only=True)
class RetrievalDoc(Schema):
    """A document provided to a case that the answer must be grounded in."""

    source_id: str
    text: str
    title: str | None = None


@dataclass(kw_only=True)
class EvalCase(Schema):
    """One evaluation case: a synthetic prompt plus the properties its output
    must satisfy. Expected behavior is expressed as *properties*, never as a raw
    expected string (see ``docs/TRACE_FORMAT.md``)."""

    case_id: str
    version: str
    prompt: str
    risk_category: str
    input_payload: dict = field(default_factory=dict)
    expected_properties: dict = field(default_factory=dict)
    json_schema: dict | None = None
    allowed_tools: list[str] | None = None
    retrieval_docs: list[RetrievalDoc] | None = None
    tags: list[str] = field(default_factory=list)

    _ALLOWED: ClassVar[dict[str, frozenset[str]]] = {
        "risk_category": frozenset(
            {"benign", "structured", "tool_use", "retrieval", "refusal", "agent"}
        ),
    }


@dataclass(kw_only=True)
class EvalSuite(Schema):
    """An ordered collection of cases plus suite-level metadata/thresholds."""

    suite_id: str
    description: str = ""
    thresholds: dict = field(default_factory=dict)
    cases: list[EvalCase] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Execution traces
# --------------------------------------------------------------------------- #


@dataclass(kw_only=True)
class ToolCallTrace(Schema):
    """One tool invocation within a run."""

    name: str
    arguments: dict = field(default_factory=dict)
    order_index: int = 0
    status: str = "ok"
    error: str | None = None

    _ALLOWED: ClassVar[dict[str, frozenset[str]]] = {
        "status": frozenset({"ok", "failed", "skipped"}),
    }


@dataclass(kw_only=True)
class RetrievalTrace(Schema):
    """One retrieved source the model relied on, and whether it supports a
    claim it was cited for."""

    source_id: str
    snippet: str
    claim: str | None = None
    supported: bool | None = None


@dataclass(kw_only=True)
class ErrorRecord(Schema):
    """A structured error. ``origin`` drives harness-noise filtering: an
    ``infra`` error is surfaced but does not raise a regression flag."""

    kind: str
    message: str
    origin: str = "app"

    _ALLOWED: ClassVar[dict[str, frozenset[str]]] = {
        "origin": frozenset({"app", "infra"}),
    }


@dataclass(kw_only=True)
class RunRecord(Schema):
    """One record per (case, run). Written to
    ``results/runs/<run_id>/run.jsonl``. All time/cost values are recorded
    inputs, never sampled from the ambient clock."""

    run_id: str
    case_id: str
    app_version: str
    model_id: str
    prompt_version: str
    tool_version: str
    timestamp: str
    input_tokens_estimate: int = 0
    output_tokens_estimate: int = 0
    latency_ms: float = 0.0
    cost_estimate_usd: float = 0.0
    final_output: str = ""
    parsed_output: Any = None
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    retrieval_sources: list[RetrievalTrace] = field(default_factory=list)
    errors: list[ErrorRecord] = field(default_factory=list)
    redaction_status: str = "clean"

    _ALLOWED: ClassVar[dict[str, frozenset[str]]] = {
        "redaction_status": frozenset({"clean", "redacted", "flagged"}),
    }


# ``ModelTrace`` is the name used in the package layout (``traces/model_trace``);
# ``RunRecord`` is the name used throughout the plan and the trace-format doc.
# They are the same contract.
ModelTrace = RunRecord


# --------------------------------------------------------------------------- #
# Evaluator output
# --------------------------------------------------------------------------- #


@dataclass(kw_only=True)
class MetricResult(Schema):
    """One evaluator result per (case, metric). ``value`` is normalized to
    ``0..1`` where that is meaningful."""

    case_id: str
    metric: str
    value: float
    passed: bool | None = None
    detail: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Comparison output
# --------------------------------------------------------------------------- #


@dataclass(kw_only=True)
class RegressionFlag(Schema):
    """A single raised regression. Carries what fired, by how much, the
    threshold it crossed, and whether it is attributed to the application or to
    harness/infrastructure noise."""

    category: str
    severity: str
    metric: str
    observed: float
    threshold: float
    attribution: str = "app"
    explanation: str = ""

    _ALLOWED: ClassVar[dict[str, frozenset[str]]] = {
        "severity": frozenset({"info", "warn", "critical"}),
        "attribution": frozenset({"app", "infra"}),
    }


@dataclass(kw_only=True)
class ComparisonRecord(Schema):
    """One record per paired case. Written to
    ``results/comparisons/<id>/comparison.jsonl``. Deltas are candidate minus
    baseline; positive deltas are not necessarily regressions (direction is
    metric-specific)."""

    case_id: str
    baseline_run_id: str
    candidate_run_id: str
    changed_fields: list[str] = field(default_factory=list)
    score_delta: float = 0.0
    latency_delta: float = 0.0
    cost_delta: float = 0.0
    json_validity_delta: float = 0.0
    tool_call_delta: float = 0.0
    refusal_delta: float = 0.0
    evidence_delta: float = 0.0
    regression_flags: list[RegressionFlag] = field(default_factory=list)
    human_readable_summary: str = ""


# --------------------------------------------------------------------------- #
# Aggregate report inputs
# --------------------------------------------------------------------------- #


@dataclass(kw_only=True)
class FailureCluster(Schema):
    """A group of correlated failures so one root cause reads as one finding."""

    cluster_id: str
    label: str = ""
    category: str = ""
    case_ids: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class ReportSummary(Schema):
    """Single object per comparison. Written to
    ``results/comparisons/<id>/summary.json`` and consumed by the report
    generator."""

    comparison_id: str
    suite_id: str
    baseline_version: str
    candidate_version: str
    case_count: int = 0
    regressed_case_count: int = 0
    flags_by_category: dict[str, int] = field(default_factory=dict)
    latency_p50_delta: float = 0.0
    latency_p95_delta: float = 0.0
    cost_total_delta: float = 0.0
    clusters: list[FailureCluster] = field(default_factory=list)
