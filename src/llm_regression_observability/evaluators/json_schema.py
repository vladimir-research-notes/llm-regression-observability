"""Structured-output scoring: parse success, schema validity, field-level diff.

The ``jsonschema`` package is not a dependency (``CLAUDE.md``: stdlib only), so
this module carries a small validator covering the keyword subset the fixtures —
and typical structured-output schemas — actually use. That subset is closed and
named in :data:`SUPPORTED_KEYWORDS`, and :func:`unsupported_keywords` reports
anything outside it rather than validating a schema it does not understand.
Silently ignoring an unknown keyword would make an invalid document score as
valid, which is exactly the regression this suite exists to catch.

Three metrics, in the order a failure cascades:

``json_parse_success``
    Did ``final_output`` parse as JSON at all? The raw string is the source of
    truth, not ``parsed_output`` — an adapter that reports a clean
    ``parsed_output`` beside an unparseable string is itself the bug. When they
    disagree, ``detail["parsed_output_mismatch"]`` records it.
``json_schema_validity``
    Does the parsed value satisfy the case's ``json_schema``? ``0`` when the
    output did not parse (an object that does not exist satisfies nothing).
``field_diff_count``
    How many fields were added, removed, or type-changed versus the shape the
    schema declares. Emitted **only when the output parsed** into an object: a
    field diff against a value that does not exist is a parse failure, and
    counting it twice would make one defect look like two.

All three are computed from the record and the case alone. Nothing here is
compared against a baseline; that is Milestone 6's job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..schemas import EvalCase, MetricResult, RunRecord
from .base import metric, parse_json

__all__ = [
    "SUPPORTED_KEYWORDS",
    "Violation",
    "FieldDiff",
    "validate",
    "is_valid",
    "unsupported_keywords",
    "parse_final_output",
    "field_diff",
    "evaluate",
]

#: The JSON Schema keywords this validator implements. ``title`` / ``description``
#: / ``$schema`` / ``$id`` are accepted as annotations and have no effect.
SUPPORTED_KEYWORDS: frozenset[str] = frozenset(
    {
        "type",
        "enum",
        "const",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "title",
        "description",
        "$schema",
        "$id",
    }
)

#: Keywords that carry no constraint; present so a documented schema validates.
_ANNOTATIONS: frozenset[str] = frozenset({"title", "description", "$schema", "$id"})


@dataclass(frozen=True)
class Violation:
    """One schema violation, located by a JSON-pointer-ish ``path``."""

    path: str
    keyword: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "keyword": self.keyword, "message": self.message}


def _json_type(value: Any) -> str:
    """The JSON type name of ``value``. ``bool`` is checked before ``int``,
    because ``True`` is an ``int`` in Python but not a number in JSON."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_matches(value: Any, expected: str) -> bool:
    actual = _json_type(value)
    if expected == "number":
        return actual in ("integer", "number")
    return actual == expected


def _json_equal(a: Any, b: Any) -> bool:
    """Equality that keeps ``True`` distinct from ``1`` (``bool`` is an ``int``)."""
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return a == b


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def validate(instance: Any, schema: dict, *, path: str = "$") -> list[Violation]:
    """Validate ``instance`` against ``schema``; return every violation found.

    Errors accumulate rather than short-circuit, so one call explains everything
    that is wrong with a document instead of only the first thing.
    """
    out: list[Violation] = []

    declared = schema.get("type")
    if declared is not None:
        allowed = declared if isinstance(declared, list) else [declared]
        if not any(_type_matches(instance, t) for t in allowed):
            out.append(
                Violation(
                    path,
                    "type",
                    f"expected {'|'.join(allowed)}, got {_json_type(instance)}",
                )
            )
            # A wrong type makes the type-specific keywords below meaningless.
            return out

    if "const" in schema and not _json_equal(instance, schema["const"]):
        out.append(Violation(path, "const", f"expected {schema['const']!r}"))

    if "enum" in schema:
        if not any(_json_equal(instance, choice) for choice in schema["enum"]):
            out.append(
                Violation(path, "enum", f"{instance!r} not in {schema['enum']!r}")
            )

    if isinstance(instance, dict):
        out.extend(_validate_object(instance, schema, path))
    elif isinstance(instance, list):
        out.extend(_validate_array(instance, schema, path))
    elif isinstance(instance, str):
        out.extend(_validate_string(instance, schema, path))
    elif _is_number(instance):
        out.extend(_validate_number(instance, schema, path))

    return out


def _validate_object(instance: dict, schema: dict, path: str) -> list[Violation]:
    out: list[Violation] = []
    properties: dict = schema.get("properties", {})

    for name in schema.get("required", []):
        if name not in instance:
            out.append(
                Violation(f"{path}.{name}", "required", "required property missing")
            )

    for name, value in instance.items():
        subschema = properties.get(name)
        if subschema is not None:
            out.extend(validate(value, subschema, path=f"{path}.{name}"))

    extra = schema.get("additionalProperties")
    if extra is not None and extra is not True:
        undeclared = [k for k in instance if k not in properties]
        if extra is False:
            out.extend(
                Violation(f"{path}.{k}", "additionalProperties", "property not allowed")
                for k in sorted(undeclared)
            )
        elif isinstance(extra, dict):
            for k in sorted(undeclared):
                out.extend(validate(instance[k], extra, path=f"{path}.{k}"))
    return out


def _validate_array(instance: list, schema: dict, path: str) -> list[Violation]:
    out: list[Violation] = []
    items = schema.get("items")
    if isinstance(items, dict):
        for i, item in enumerate(instance):
            out.extend(validate(item, items, path=f"{path}[{i}]"))
    minimum = schema.get("minItems")
    if minimum is not None and len(instance) < minimum:
        out.append(Violation(path, "minItems", f"{len(instance)} < {minimum}"))
    maximum = schema.get("maxItems")
    if maximum is not None and len(instance) > maximum:
        out.append(Violation(path, "maxItems", f"{len(instance)} > {maximum}"))
    return out


def _validate_string(instance: str, schema: dict, path: str) -> list[Violation]:
    out: list[Violation] = []
    minimum = schema.get("minLength")
    if minimum is not None and len(instance) < minimum:
        out.append(Violation(path, "minLength", f"{len(instance)} < {minimum}"))
    maximum = schema.get("maxLength")
    if maximum is not None and len(instance) > maximum:
        out.append(Violation(path, "maxLength", f"{len(instance)} > {maximum}"))
    pattern = schema.get("pattern")
    if pattern is not None and re.search(pattern, instance) is None:
        out.append(Violation(path, "pattern", f"does not match {pattern!r}"))
    return out


def _validate_number(instance: float, schema: dict, path: str) -> list[Violation]:
    out: list[Violation] = []
    minimum = schema.get("minimum")
    if minimum is not None and instance < minimum:
        out.append(Violation(path, "minimum", f"{instance} < {minimum}"))
    maximum = schema.get("maximum")
    if maximum is not None and instance > maximum:
        out.append(Violation(path, "maximum", f"{instance} > {maximum}"))
    return out


def is_valid(instance: Any, schema: dict) -> bool:
    """``True`` when ``instance`` satisfies ``schema`` with no violations."""
    return not validate(instance, schema)


def unsupported_keywords(schema: Any) -> list[str]:
    """Every keyword in ``schema`` (recursively) outside :data:`SUPPORTED_KEYWORDS`.

    A non-empty result means this validator would *under*-constrain the document:
    it is surfaced in the metric detail rather than swallowed, so a schema the
    core cannot fully check never masquerades as one it can.
    """
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key not in SUPPORTED_KEYWORDS:
                    found.add(key)
                if key in ("properties",) and isinstance(value, dict):
                    for sub in value.values():
                        walk(sub)
                elif key in ("items", "additionalProperties") and isinstance(
                    value, dict
                ):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return sorted(found)


# --------------------------------------------------------------------------- #
# Reading the record
# --------------------------------------------------------------------------- #


def parse_final_output(record: RunRecord) -> tuple[bool, Any, str | None]:
    """Parse ``record.final_output`` as JSON. Returns ``(ok, value, error)``."""
    return parse_json(record.final_output)


def _parsed_output_mismatch(record: RunRecord, parsed: Any, ok: bool) -> bool:
    """``True`` when the adapter's ``parsed_output`` contradicts ``final_output``.

    Not a schema problem — a *trace* problem. Reported alongside the metric so a
    harness bug is visible instead of quietly changing a score.
    """
    if record.parsed_output is None:
        return False
    if not ok:
        return True
    return record.parsed_output != parsed


@dataclass(frozen=True)
class FieldDiff:
    """Top-level field differences between an object and the shape a schema declares."""

    missing: tuple[str, ...] = ()
    type_changed: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return len(self.missing) + len(self.type_changed) + len(self.extra)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "missing": list(self.missing),
            "type_changed": list(self.type_changed),
            "extra": list(self.extra),
        }


def field_diff(instance: dict, schema: dict) -> FieldDiff:
    """Fields removed, type-changed, or added versus ``schema``'s declared shape.

    ``extra`` lists keys the schema does not declare, whether or not
    ``additionalProperties`` permits them: this is a *diff*, not a verdict.
    Legality is ``json_schema_validity``'s answer.
    """
    properties: dict = schema.get("properties", {})
    required: list[str] = list(schema.get("required", []))

    missing = tuple(sorted(name for name in required if name not in instance))
    extra = tuple(sorted(name for name in instance if name not in properties))
    type_changed = tuple(
        sorted(
            name
            for name, value in instance.items()
            if name in properties
            and "type" in properties[name]
            and not any(
                _type_matches(value, t)
                for t in (
                    properties[name]["type"]
                    if isinstance(properties[name]["type"], list)
                    else [properties[name]["type"]]
                )
            )
        )
    )
    return FieldDiff(missing=missing, type_changed=type_changed, extra=extra)


# --------------------------------------------------------------------------- #
# Evaluator
# --------------------------------------------------------------------------- #


def evaluate(case: EvalCase, record: RunRecord) -> list[MetricResult]:
    """Score structured output. Silent unless the case declares a ``json_schema``."""
    if case.json_schema is None:
        return []

    schema = case.json_schema
    ok, parsed, error = parse_final_output(record)
    unsupported = unsupported_keywords(schema)

    parse_detail: dict[str, Any] = {
        "parsed_output_mismatch": _parsed_output_mismatch(record, parsed, ok),
    }
    if error is not None:
        parse_detail["error"] = error

    results = [
        metric(
            record.case_id,
            "json_parse_success",
            1.0 if ok else 0.0,
            passed=ok,
            detail=parse_detail,
        )
    ]

    violations = validate(parsed, schema) if ok else []
    valid = ok and not violations
    results.append(
        metric(
            record.case_id,
            "json_schema_validity",
            1.0 if valid else 0.0,
            passed=valid,
            detail={
                "violations": [v.to_dict() for v in violations],
                "unsupported_keywords": unsupported,
                "parse_failed": not ok,
            },
        )
    )

    # A field diff needs an object to diff. See the module docstring.
    if ok and isinstance(parsed, dict) and "properties" in schema:
        diff = field_diff(parsed, schema)
        results.append(
            metric(
                record.case_id,
                "field_diff_count",
                diff.count,
                passed=diff.count == 0,
                detail=diff.to_dict(),
            )
        )
    return results
