"""``llmreg`` — the native shape, and therefore the contract's reference importer.

A bundle this repository exported carries ``RunRecord`` dicts already. Its
importer is the identity map, which makes it the only one that can be *lossless*
and the only one whose round trip is a meaningful test:
``import(export(records)) == records``, byte for byte, in
``tests/test_integration_contracts.py``.

That test is what the five foreign importers are measured against. Each of them
loses something — a transcript, a per-node model attribution, a stop reason — and
each says what. The native importer loses nothing, so a failure there is a failure
of the envelope, not of a mapping, and the two are worth being able to tell apart.
"""

from __future__ import annotations

from typing import Any

from ...schemas import EvalCase, RunRecord, SchemaError
from ..versioning import NATIVE_SCHEMA, NATIVE_SOURCE
from .base import (
    ImportContext,
    ImportResult,
    TraceImporter,
    TraceImportError,
    as_list,
    as_mapping,
)

__all__ = ["NativeImporter"]

_KNOWN_KEYS = ("records", "cases")


class NativeImporter(TraceImporter):
    """Read a payload this repository wrote. No mapping, no loss, no defaults."""

    source = NATIVE_SOURCE
    source_schema = NATIVE_SCHEMA
    supported_source_versions = frozenset()  # the contract version governs instead
    ignored_fields = ()
    absent_fields = ()
    error_origins = {}

    def parse(self, document: Any, context: ImportContext) -> ImportResult:
        payload = as_mapping(document, path=f"{self.source} payload")
        warnings: list[str] = []
        unknown: set[str] = set()
        self.unknown(
            payload,
            _KNOWN_KEYS,
            path="payload",
            context=context,
            warnings=warnings,
            unknown=unknown,
        )

        records: list[RunRecord] = []
        raw_records = as_list(payload.get("records", []), path="records")
        for index, item in enumerate(raw_records):
            try:
                records.append(RunRecord.from_dict(item))
            except SchemaError as exc:
                raise TraceImportError(
                    f"{self.source}: records[{index}]: {exc}"
                ) from exc

        cases: list[EvalCase] = []
        for index, item in enumerate(as_list(payload.get("cases", []), path="cases")):
            try:
                cases.append(EvalCase.from_dict(item))
            except SchemaError as exc:
                raise TraceImportError(
                    f"{self.source}: cases[{index}]: {exc}"
                ) from exc

        # Identity is not re-stamped here. ``executor._restamp`` owns it, and a
        # native bundle's records already carry the identity of the run they came
        # from — rewriting it in two places is how the two disagree.
        #
        # ``source_version`` is left empty: the payload does not state which build of
        # llmreg wrote it. The envelope does, and ``registry.import_bundle`` fills it.
        return self.result(
            source_version="",
            records=records,
            cases=cases,
            warnings=warnings,
            unknown=unknown,
        )
