"""The importer contract: what every external trace mapping owes this framework.

An importer reads one external system's trace document and returns
:class:`~...schemas.RunRecord`s — and, when the source also carries a task
definition, the :class:`~...schemas.EvalCase`s those records answer. Nothing
downstream changes: the same evaluators score the records, the same comparison
engine flags them, the same projection turns them into spans.

Mapping two vocabularies onto each other is where evaluation harnesses quietly
start lying, so the contract is defined by what an importer is *not allowed to do*.

**It may not drop a field in silence.** Every key in a source document is
accounted for exactly once: mapped, listed in :attr:`TraceImporter.ignored_fields`
(a deliberate, documented drop — a chat transcript, a workflow id), or *unknown*.
An unknown key is a field the source grew after this importer was written; in
strict mode it raises, and in lenient mode it lands in
:attr:`ImportResult.unknown_fields` and in a warning. It never simply disappears.

**It may not invent a measurement.** :attr:`TraceImporter.absent_fields` names
the ``RunRecord`` fields the source structurally cannot fill. Latency is the one
that bites: a missing ``latency_ms`` imported as ``0.0`` reads as an improvement
against a timed baseline, and — when it *is* the baseline —
``comparison.base.relative`` returns ``inf`` and the candidate takes an unbounded
``latency_regression``. So a record with no recorded duration is refused in strict
mode with that sentence, rather than scored.

**It may not guess an attribution.** ``app`` versus ``infra`` is this repository's
distinction (research question 9), not a field any external system emits. Each
importer therefore publishes :attr:`TraceImporter.error_origins`, a table from
*that source's* error kinds to an origin. An unmapped kind resolves to ``app`` and
warns — the loud direction. ``infra`` withholds regression flags
(``comparison/regression.py``), so defaulting there would let an unrecognized error
silently suppress a real regression. A false flag gets investigated; a silent
suppression does not.

**It may not import a secret.** Milestone 9 noted that ``redaction_status="flagged"``
is unreachable on a trace this project wrote, because the executor refuses to
persist one — and that it becomes reachable exactly here. :func:`finalize` runs
``redact_record`` over every imported record before returning it: content is masked,
identity fields are scanned, and a credential-like string in a join key refuses the
import naming the field and the pattern, never the value. The executor redacts again
downstream; redaction is idempotent, so the second pass is a no-op.

Deterministic and offline. No clock, no RNG, no network, no source repository.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ...schemas import (
    ErrorRecord,
    EvalCase,
    RetrievalTrace,
    RunRecord,
    SchemaError,
    ToolCallTrace,
)
from ...traces.redaction import redact_record
from ..versioning import DEFAULT_TIMESTAMP

__all__ = [
    "TraceImportError",
    "ImportContext",
    "ImportResult",
    "TraceImporter",
    "APP",
    "INFRA",
    "UNKNOWN_VERSION",
    "LATENCY_HAZARD",
    "require",
    "as_mapping",
    "as_list",
    "as_str",
    "as_int",
    "as_float",
    "extra_keys",
    "resolve_origin",
    "check_source_version",
    "finalize",
    "build_record",
    "build_case",
    "tool_call",
    "retrieval",
    "error_record",
]


class TraceImportError(RuntimeError):
    """Raised when an external trace cannot be mapped onto this repo's contracts."""


APP = "app"
INFRA = "infra"

#: What a provenance field becomes when neither the caller nor the source names it.
#: Not ``""``: an empty string reads as "no version", and a reader cannot tell that
#: apart from a version that is literally empty.
UNKNOWN_VERSION = "unknown"

#: The refusal a source with no recorded duration earns in strict mode. Spelled out
#: because "latency_ms is required" would not tell the reader why 0.0 is worse than
#: an error: it is a *number*, and every downstream rule believes numbers.
LATENCY_HAZARD = (
    "{source}: case {case_id!r} carries no recorded duration. Importing it as "
    "0.0 ms would not be missing data — it would be a measurement. Against a "
    "timed baseline it reads as an improvement; as the baseline it makes every "
    "candidate an unbounded latency_regression. Record the duration in the trace, "
    "or import with strict=False and drop latency from the comparison."
)


# --------------------------------------------------------------------------- #
# Inputs and outputs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ImportContext:
    """The identity an imported trace is given, and how strictly it is read.

    Provenance the source does not carry has to come from somewhere, and the honest
    somewhere is the caller. Every provenance field resolves the same way — *the
    caller's override, else the source's own value, else a named default* — so an
    empty ``ImportContext`` never overrides a fact the document already states. That
    ordering is why the four fields default to empty rather than to ``"external"``:
    a default that outranks the source would silently discard, say, the
    ``agent_version`` that ``code_agent_runtime`` maps onto ``tool_version``.

    ``app_version`` is the exception. It labels the *side* of a comparison, no source
    carries it, and it is always the caller's to name.
    """

    app_version: str = "external"
    prompt_version: str = ""
    tool_version: str = ""
    model_id: str = ""
    timestamp: str = ""
    run_id: str = "imported"
    strict: bool = True

    def stamp(self, *, model_id: str = "", timestamp: str = "") -> tuple[str, str]:
        """Resolve ``(model_id, timestamp)``: override, else source's, else default."""
        return (
            self.model_id or model_id or UNKNOWN_VERSION,
            self.timestamp or timestamp or DEFAULT_TIMESTAMP,
        )

    def versions(self, *, tool_version: str = "") -> tuple[str, str]:
        """Resolve ``(prompt_version, tool_version)`` the same way.

        ``tool_version`` is the value the *source* offers for it — an agent binary
        version, a policy version — and is used only when the caller named none.
        """
        return (
            self.prompt_version or UNKNOWN_VERSION,
            self.tool_version or tool_version or UNKNOWN_VERSION,
        )


@dataclass(frozen=True)
class ImportResult:
    """What one source document became, and what was lost on the way.

    The last four fields are the point. A caller that reads only ``records`` gets
    a working import; a caller that reads the rest learns which source fields were
    dropped by design, which appeared that this build has never seen, which
    ``RunRecord`` fields the source cannot fill, and every attribution the importer
    had to default. ``llmreg import-trace`` writes all of it to ``import_meta.json``
    next to the run, so the losses are an artifact rather than a memory.
    """

    source: str
    source_schema: str
    source_version: str
    records: tuple[RunRecord, ...]
    cases: tuple[EvalCase, ...] = ()
    warnings: tuple[str, ...] = ()
    ignored_fields: tuple[str, ...] = ()
    unknown_fields: tuple[str, ...] = ()
    absent_fields: tuple[str, ...] = ()

    @property
    def case_ids(self) -> tuple[str, ...]:
        """Case ids in document order."""
        return tuple(record.case_id for record in self.records)

    def record_for(self, case_id: str) -> RunRecord:
        for record in self.records:
            if record.case_id == case_id:
                return record
        raise TraceImportError(
            f"{self.source}: no imported record for case {case_id!r}; "
            f"the document carries {sorted(self.case_ids)}"
        )

    def case_for(self, case_id: str) -> EvalCase:
        for case in self.cases:
            if case.case_id == case_id:
                return case
        raise TraceImportError(
            f"{self.source}: the document carries no task definition for case "
            f"{case_id!r}; score it against a suite instead"
        )


# --------------------------------------------------------------------------- #
# Reading a foreign document, loudly
# --------------------------------------------------------------------------- #


def require(mapping: Any, key: str, *, path: str) -> Any:
    """Fetch ``key`` from a mapping, or raise naming the JSON path that lacked it."""
    data = as_mapping(mapping, path=path)
    if key not in data:
        raise TraceImportError(
            f"{path}: missing required key {key!r} (has: {sorted(data)})"
        )
    return data[key]


def as_mapping(value: Any, *, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TraceImportError(
            f"{path}: expected an object, got {type(value).__name__}"
        )
    return value


def as_list(value: Any, *, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise TraceImportError(f"{path}: expected a list, got {type(value).__name__}")
    return value


def as_str(value: Any, *, path: str) -> str:
    if not isinstance(value, str):
        raise TraceImportError(f"{path}: expected a string, got {type(value).__name__}")
    return value


def as_int(value: Any, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TraceImportError(
            f"{path}: expected an integer, got {type(value).__name__}"
        )
    return value


def as_float(value: Any, *, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TraceImportError(f"{path}: expected a number, got {type(value).__name__}")
    return float(value)


def extra_keys(
    mapping: Mapping[str, Any], known: Iterable[str], ignored: Iterable[str] = ()
) -> tuple[str, ...]:
    """Keys present in ``mapping`` that are neither mapped nor deliberately dropped."""
    return tuple(sorted(set(mapping) - set(known) - set(ignored)))


def _report_unknown(
    keys: Sequence[str],
    *,
    path: str,
    source: str,
    context: ImportContext,
    warnings: list[str],
    unknown: set[str],
) -> None:
    """Refuse (strict) or record (lenient) source keys this importer never mapped."""
    if not keys:
        return
    names = ", ".join(repr(key) for key in keys)
    message = (
        f"{source}: {path} carries unmapped field(s) {names}. The importer was "
        "written against an earlier version of this source schema; a field it "
        "cannot read is a field it cannot score"
    )
    if context.strict:
        raise TraceImportError(message)
    warnings.append(message)
    unknown.update(f"{path}.{key}" for key in keys)


def resolve_origin(
    kind: str,
    table: Mapping[str, str],
    *,
    source: str,
    warnings: list[str],
) -> str:
    """Map one of the source's error kinds onto ``app`` / ``infra``.

    Unmapped kinds become ``app`` and warn. See the module docstring: ``infra``
    withholds flags, so guessing it is the direction that hides regressions.
    """
    origin = table.get(kind)
    if origin is None:
        warnings.append(
            f"{source}: error kind {kind!r} has no origin mapping; attributed to "
            f"{APP!r}, which is the direction that raises a flag rather than "
            "withholding one. Add it to the importer's error_origins table"
        )
        return APP
    return origin


def check_source_version(
    version: str,
    *,
    supported: frozenset[str],
    source: str,
    context: ImportContext,
    warnings: list[str],
) -> None:
    """Refuse (strict) or warn (lenient) on a source schema version we do not know."""
    if version in supported:
        return
    message = (
        f"{source}: source schema version {version!r} is not one this importer "
        f"was written against ({', '.join(sorted(supported))})"
    )
    if context.strict:
        raise TraceImportError(message)
    warnings.append(message)


# --------------------------------------------------------------------------- #
# The interface
# --------------------------------------------------------------------------- #


class TraceImporter:
    """Base class for every external-trace importer.

    Subclasses set the five class attributes — which *are* the published contract,
    rendered into ``docs/INTEGRATION_CONTRACTS.md`` and asserted in
    ``tests/test_integration_contracts.py`` — and implement :meth:`parse`. Kept a
    plain class rather than an ABC, matching :class:`~...adapters.base.ModelAdapter`
    and the stdlib-first style of the package.
    """

    #: Short, stable name. Selects this importer on the CLI and in a bundle envelope.
    source: str = ""
    #: The source system's own schema identifier, as it writes it into its documents.
    source_schema: str = ""
    #: Versions of ``source_schema`` this importer was written against.
    supported_source_versions: frozenset[str] = frozenset()
    #: Source fields deliberately not mapped, path-qualified, and explained in
    #: ``docs/INTEGRATION_CONTRACTS.md``. This is the *published* list; each call
    #: to :meth:`unknown` passes the concrete keys it tolerates at that path, so
    #: an ignored key at one path is still unknown at another.
    ignored_fields: tuple[str, ...] = ()
    #: ``RunRecord`` fields this source structurally cannot fill.
    absent_fields: tuple[str, ...] = ()
    #: This source's error kinds, mapped onto ``app`` / ``infra``.
    error_origins: Mapping[str, str] = {}

    def parse(self, document: Any, context: ImportContext) -> ImportResult:
        raise NotImplementedError

    # -- helpers available to every subclass -------------------------------- #

    def unknown(
        self,
        mapping: Mapping[str, Any],
        known: Iterable[str],
        *,
        path: str,
        context: ImportContext,
        warnings: list[str],
        unknown: set[str],
        ignored: Iterable[str] = (),
    ) -> None:
        """Account for every key of ``mapping``: mapped, ignored, or reported."""
        _report_unknown(
            extra_keys(mapping, known, ignored),
            path=path,
            source=self.source,
            context=context,
            warnings=warnings,
            unknown=unknown,
        )

    def origin(self, kind: str, *, warnings: list[str]) -> str:
        return resolve_origin(
            kind, self.error_origins, source=self.source, warnings=warnings
        )

    def latency(
        self,
        value: float | None,
        *,
        case_id: str,
        context: ImportContext,
        warnings: list[str],
    ) -> float:
        """Resolve a duration, refusing to invent one. See :data:`LATENCY_HAZARD`."""
        if value is not None:
            return value
        message = LATENCY_HAZARD.format(source=self.source, case_id=case_id)
        if context.strict:
            raise TraceImportError(message)
        warnings.append(message)
        return 0.0

    def result(
        self,
        *,
        source_version: str,
        records: list[RunRecord],
        cases: list[EvalCase] | None = None,
        warnings: list[str],
        unknown: set[str],
    ) -> ImportResult:
        """Redact, check, and freeze — the one way an importer returns."""
        return finalize(
            source=self.source,
            source_schema=self.source_schema,
            source_version=source_version,
            records=records,
            cases=cases or [],
            warnings=warnings,
            unknown=unknown,
            ignored_fields=self.ignored_fields,
            absent_fields=self.absent_fields,
        )


# --------------------------------------------------------------------------- #
# Finalization: redact, check, freeze
# --------------------------------------------------------------------------- #


def _check_unique(records: Sequence[RunRecord], *, source: str) -> None:
    seen: set[str] = set()
    for record in records:
        if record.case_id in seen:
            raise TraceImportError(
                f"{source}: two records for case {record.case_id!r}; "
                "(run_id, case_id) must key exactly one trace"
            )
        seen.add(record.case_id)


def finalize(
    *,
    source: str,
    source_schema: str,
    source_version: str,
    records: list[RunRecord],
    cases: list[EvalCase],
    warnings: list[str],
    unknown: set[str],
    ignored_fields: tuple[str, ...] = (),
    absent_fields: tuple[str, ...] = (),
) -> ImportResult:
    """Redact every imported record, then freeze the result.

    This is the boundary Milestone 9 pointed at: the first place a trace this
    project did not write can carry a credential. Content is masked in place;
    a certain-tier match in an identity field — a ``case_id``, a ``model_id``, a
    tool *name* — is ``flagged``, and a flagged record refuses the import. There is
    no safe repair: masking a join key corrupts every downstream comparison, and
    keeping it writes the secret to disk.
    """
    _check_unique(records, source=source)

    redacted: list[RunRecord] = []
    masked_cases: list[str] = []
    for record in records:
        outcome = redact_record(record)
        if outcome.flagged:
            raise TraceImportError(f"{source}: {outcome.describe()}")
        if not outcome.clean:
            masked_cases.append(record.case_id)
        redacted.append(outcome.record)

    if masked_cases:
        warnings.append(
            f"{source}: redacted credential- or PII-like text in "
            f"{len(masked_cases)} record(s) ({', '.join(sorted(masked_cases))}); "
            "the imported trace carries placeholders where the source carried text"
        )

    return ImportResult(
        source=source,
        source_schema=source_schema,
        source_version=source_version,
        records=tuple(redacted),
        cases=tuple(cases),
        warnings=tuple(warnings),
        ignored_fields=tuple(ignored_fields),
        unknown_fields=tuple(sorted(unknown)),
        absent_fields=tuple(absent_fields),
    )


# --------------------------------------------------------------------------- #
# Small constructors, shared by the source modules
# --------------------------------------------------------------------------- #


def build_record(**kwargs: Any) -> RunRecord:
    """Construct a :class:`~...schemas.RunRecord`, re-raising schema errors as import
    errors so a caller catches one exception type at the boundary."""
    try:
        return RunRecord(**kwargs)
    except SchemaError as exc:  # pragma: no cover - defensive
        raise TraceImportError(
            f"imported record is not a valid RunRecord: {exc}"
        ) from exc


def build_case(**kwargs: Any) -> EvalCase:
    """Construct an :class:`~...schemas.EvalCase` from a source's task definition."""
    try:
        return EvalCase(**kwargs)
    except SchemaError as exc:
        raise TraceImportError(
            f"imported task definition is not a valid EvalCase: {exc}"
        ) from exc


def tool_call(
    *,
    name: str,
    arguments: dict[str, Any],
    order_index: int,
    status: str,
    error: str | None,
) -> ToolCallTrace:
    try:
        return ToolCallTrace(
            name=name,
            arguments=arguments,
            order_index=order_index,
            status=status,
            error=error,
        )
    except SchemaError as exc:
        raise TraceImportError(f"imported tool call is invalid: {exc}") from exc


def retrieval(
    *, source_id: str, snippet: str, claim: str | None, supported: bool | None
) -> RetrievalTrace:
    return RetrievalTrace(
        source_id=source_id, snippet=snippet, claim=claim, supported=supported
    )


def error_record(*, kind: str, message: str, origin: str) -> ErrorRecord:
    try:
        return ErrorRecord(kind=kind, message=message, origin=origin)
    except SchemaError as exc:
        raise TraceImportError(f"imported error record is invalid: {exc}") from exc
