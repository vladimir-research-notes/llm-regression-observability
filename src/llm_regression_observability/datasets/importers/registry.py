"""Importer registry and dispatch: a document, a source name, and one mapping.

:func:`import_document` is the single entry point. It accepts either a sealed
:class:`~..versioning.TraceBundle` — in which case the envelope names the source,
pins the contract version and verifies the payload digest — or a bare source
document, in which case the caller must name the source, because a JSON object is
a shape and shapes do not identify themselves.

Adding a source is: write a :class:`~.base.TraceImporter`, register it here. No
evaluator, no comparison rule, no report template, and no schema changes. That
property is the milestone's actual deliverable, and
``tests/test_integration_contracts.py`` holds it by asserting the whole registry
satisfies one contract rather than by testing each importer's mapping twice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..versioning import (
    TraceBundle,
    VersioningError,
    check_record_schema,
    is_envelope,
    read_document,
)
from .agent_control_bench import AgentControlBenchImporter
from .base import ImportContext, ImportResult, TraceImporter, TraceImportError
from .bioagentbench import BioAgentBenchImporter
from .bioscientist_os import BioScientistOsImporter
from .claude_api import ClaudeApiImporter
from .code_agent_runtime import CodeAgentRuntimeImporter
from .native import NativeImporter

__all__ = [
    "IMPORTERS",
    "SOURCES",
    "get_importer",
    "import_document",
    "import_bundle",
    "import_path",
]


def _registry() -> dict[str, TraceImporter]:
    importers: tuple[TraceImporter, ...] = (
        NativeImporter(),
        CodeAgentRuntimeImporter(),
        BioAgentBenchImporter(),
        BioScientistOsImporter(),
        AgentControlBenchImporter(),
        ClaudeApiImporter(),
    )
    return {importer.source: importer for importer in importers}


#: Every importer, by source name.
IMPORTERS: dict[str, TraceImporter] = _registry()

#: The source names, sorted — what ``llmreg import-trace --source`` accepts.
SOURCES: tuple[str, ...] = tuple(sorted(IMPORTERS))


def get_importer(source: str) -> TraceImporter:
    """The importer registered for ``source``, or a refusal naming the alternatives."""
    importer = IMPORTERS.get(source)
    if importer is None:
        raise TraceImportError(
            f"no importer for source {source!r}; known sources are "
            f"{', '.join(SOURCES)}"
        )
    return importer


def import_bundle(bundle: TraceBundle, context: ImportContext) -> ImportResult:
    """Import a sealed envelope. Verifies the digest and the record fingerprint.

    The envelope outranks the importer on ``source_version``: it records the version
    the *exporter* was on, which is a fact, where an importer reading a bare document
    can only report the version that document declared.
    """
    bundle.verify()
    # Envelope-level warnings precede the importer's, which are already ordered.
    warnings = list(check_record_schema(bundle.record_schema, strict=context.strict))
    result = get_importer(bundle.source).parse(bundle.payload, context)
    return ImportResult(
        source=result.source,
        source_schema=result.source_schema,
        source_version=bundle.source_version or result.source_version,
        records=result.records,
        cases=result.cases,
        warnings=tuple(warnings) + result.warnings,
        ignored_fields=result.ignored_fields,
        unknown_fields=result.unknown_fields,
        absent_fields=result.absent_fields,
    )


def import_document(
    document: Any, context: ImportContext, *, source: str | None = None
) -> ImportResult:
    """Import an envelope or a bare source document.

    A bare document needs ``source``: the mapping is the only thing that knows what
    the keys mean, and picking one by sniffing the keys would guess exactly where
    guessing is most expensive. An envelope names its own source; passing a
    conflicting ``source`` is an error rather than an override, because one of the
    two is wrong and the file is the one under version control.
    """
    if is_envelope(document):
        try:
            bundle: TraceBundle = TraceBundle.from_dict(document)
        except Exception as exc:  # SchemaError, and anything malformed
            raise TraceImportError(f"malformed trace bundle: {exc}") from exc
        if source is not None and source != bundle.source:
            raise TraceImportError(
                f"--source {source!r} contradicts the bundle, which was exported "
                f"from {bundle.source!r}"
            )
        try:
            return import_bundle(bundle, context)
        except VersioningError as exc:
            raise TraceImportError(str(exc)) from exc

    if source is None:
        raise TraceImportError(
            "this document is not a trace bundle, so it does not name its source; "
            f"pass one of {', '.join(SOURCES)}"
        )
    return get_importer(source).parse(document, context)


def import_path(
    path: Path | str, context: ImportContext, *, source: str | None = None
) -> ImportResult:
    """Read a JSON document off disk and import it."""
    try:
        document = read_document(path)
    except VersioningError as exc:
        raise TraceImportError(str(exc)) from exc
    return import_document(document, context, source=source)
