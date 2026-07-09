"""The versioned import/export contract: how a trace crosses the repo boundary.

Everything upstream of this module assumes a trace was produced *here*, by an
adapter this repository owns. Milestone 10 relaxes that assumption without
depending on any of the systems it relaxes it for: an external runtime emits its
own trace shape, an importer maps it onto :class:`~..schemas.RunRecord`, and the
evaluators, the comparison engine, the span projection and the report generator
run over the result **unchanged**. The mapping is where the two vocabularies
meet, and this module is the envelope that says which mapping was applied.

**Why an envelope at all.** A bare document is a shape without a claim. An
envelope makes four claims the importer can check before it reads a single
record:

``contract_version``
    Which version of *this* contract the document was written against
    (:data:`CONTRACT_VERSION`). An unknown version is refused, not guessed at.
``source`` / ``source_schema`` / ``source_version``
    Which system emitted the payload and under which of *its* schemas. The
    importer for ``source`` is the only code that may read ``payload``.
``record_schema``
    A fingerprint of :class:`~..schemas.RunRecord` (:func:`record_schema_fingerprint`)
    at export time. This is the part a hand-maintained version constant gets
    wrong: it is *derived from the dataclass*, so adding a field to the trace
    model changes it whether or not anybody remembered to bump a number.
    ``tests/test_integration_contracts.py`` pins the current value, so a schema
    change fails the suite until someone decides what it means for a consumer.
``payload_sha256``
    The digest of the payload's canonical JSON. A bundle that was edited after
    export does not verify.

**Bare documents are still accepted.** A sibling system will not wrap its traces
in our envelope, and demanding that it does would make the contract useless for
its purpose. :func:`is_envelope` distinguishes the two; a bare document imports
exactly as well, minus the four claims above, provided the caller names the
source. What is *not* optional is the mapping: a document only enters this
framework through an importer, and an importer that meets a field it does not
understand says so (see ``datasets/importers/base.py``).

**Spans are not part of the payload.** ``observability/spans.py`` derives a span
tree from ``(EvalCase, RunRecord, metrics)`` as a pure projection. Shipping spans
across the boundary would ship a *derivation* that the receiver can recompute and
cannot verify. So the contract carries records; the span model comes free, and
``tests/test_integration_contracts.py`` proves it by projecting an imported
record without touching :mod:`..observability`.

Deterministic, offline, dependency-free. ``exported_at`` is a recorded input —
:func:`export_bundle` requires one and never reads a clock, so exporting the same
run twice produces byte-identical bytes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .. import __version__
from ..schemas import EvalCase, RunRecord, Schema, SchemaError
from ..traces.storage import dumps_json, read_json, write_json

__all__ = [
    "VersioningError",
    "CONTRACT_VERSION",
    "SUPPORTED_CONTRACT_VERSIONS",
    "NATIVE_SOURCE",
    "NATIVE_SCHEMA",
    "DEFAULT_TIMESTAMP",
    "ENVELOPE_REQUIRED_KEYS",
    "TraceBundle",
    "record_schema_fingerprint",
    "check_contract_version",
    "check_record_schema",
    "payload_digest",
    "is_envelope",
    "native_payload",
    "export_bundle",
    "write_bundle",
    "read_bundle",
    "read_document",
]


class VersioningError(RuntimeError):
    """Raised on an envelope this build of the contract cannot honor."""


#: The version of *this* contract. Bumped when the envelope's own shape changes,
#: not when a source schema does — a source version lives in ``source_version``.
CONTRACT_VERSION = "1.0"

#: Envelopes this build can read. A one-element set today; the point of naming it
#: is that widening it is a deliberate act with a test behind it.
SUPPORTED_CONTRACT_VERSIONS: frozenset[str] = frozenset({"1.0"})

#: ``source`` of a bundle this repository exported. Its payload is already in the
#: native shape, so its "importer" is the identity map — which is exactly what
#: makes it the reference implementation of the contract, and the one importer
#: that must be *lossless* (``test_native_bundle_round_trips``).
NATIVE_SOURCE = "llmreg"
NATIVE_SCHEMA = "llmreg/run-records"

#: Used when neither the caller nor the document supplies a timestamp. The same
#: recorded constant as ``executor.DEFAULT_TIMESTAMP`` — never ``datetime.now()``
#: (``CLAUDE.md``, determinism rule). A test pins the two equal rather than
#: importing across the cycle ``executor -> datasets -> importers -> executor``.
DEFAULT_TIMESTAMP = "1970-01-01T00:00:00Z"

#: The keys whose presence identifies a document as one of our envelopes.
ENVELOPE_REQUIRED_KEYS: frozenset[str] = frozenset(
    {"contract_version", "source", "payload"}
)

#: Width of the schema fingerprint, in hex characters. Long enough that two
#: distinct field sets do not collide in any repository anyone will ever write.
_FINGERPRINT_WIDTH = 12


# --------------------------------------------------------------------------- #
# Schema fingerprint
# --------------------------------------------------------------------------- #


def _field_signature(model: type) -> str:
    """``name:typename`` for every field, in declared order."""
    return ";".join(
        f"{f.name}:{getattr(f.type, '__name__', str(f.type))}"
        for f in fields(model)  # type: ignore[arg-type]
    )


def record_schema_fingerprint() -> str:
    """A stable digest of the :class:`~..schemas.RunRecord` field set.

    Derived, not declared. A consumer that reads a bundle whose fingerprint does
    not match its own build knows the trace model moved underneath it — before it
    silently drops a field it has never heard of, or scores a record that is
    missing one it needs.
    """
    signature = _field_signature(RunRecord)
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:_FINGERPRINT_WIDTH]


def check_contract_version(version: str, *, source: str = "bundle") -> None:
    """Refuse an envelope written against a contract version this build lacks."""
    if version not in SUPPORTED_CONTRACT_VERSIONS:
        raise VersioningError(
            f"{source}: contract_version {version!r} is not supported by "
            f"llmreg {__version__} (supported: "
            f"{', '.join(sorted(SUPPORTED_CONTRACT_VERSIONS))})"
        )


def check_record_schema(fingerprint: str, *, strict: bool = True) -> tuple[str, ...]:
    """Compare a bundle's record fingerprint against this build's.

    An empty fingerprint means the bundle predates the field or was written by a
    producer that does not compute one; that is a warning, never a refusal. A
    fingerprint that is present *and* different means the two builds disagree
    about what a ``RunRecord`` is, which is a refusal in strict mode: every
    downstream score would be computed against a shape the exporter never wrote.
    """
    current = record_schema_fingerprint()
    if not fingerprint:
        return (
            "bundle carries no record_schema fingerprint; the RunRecord shape it "
            f"was written against cannot be checked (this build: {current})",
        )
    if fingerprint == current:
        return ()
    message = (
        f"bundle was exported against RunRecord schema {fingerprint!r}; this "
        f"build is {current!r}. Fields may be missing, added, or retyped"
    )
    if strict:
        raise VersioningError(message)
    return (message,)


# --------------------------------------------------------------------------- #
# The envelope
# --------------------------------------------------------------------------- #


def payload_digest(payload: Any) -> str:
    """SHA-256 of the payload's canonical (sorted-key, compact) JSON."""
    return hashlib.sha256(
        dumps_json(payload, indent=None).encode("utf-8")
    ).hexdigest()


@dataclass(kw_only=True)
class TraceBundle(Schema):
    """One document crossing the boundary, in either direction.

    ``payload`` is *source-shaped*: for :data:`NATIVE_SOURCE` it is
    ``{"records": [...], "cases": [...]}``; for anything else it is whatever that
    system emits, and only that system's importer may read it. Keeping the
    payload opaque here is what lets a new source be added without touching the
    envelope, the executor, or a single evaluator.
    """

    contract_version: str = CONTRACT_VERSION
    source: str
    source_schema: str = ""
    source_version: str = ""
    #: A recorded input. ``export_bundle`` requires one; nothing here reads a clock.
    exported_at: str = DEFAULT_TIMESTAMP
    suite_id: str = ""
    app_version: str = ""
    record_schema: str = ""
    payload_sha256: str = ""
    payload: Any = None
    notes: list[str] = field(default_factory=list)

    def sealed(self) -> TraceBundle:
        """A copy carrying the digest of its own payload and this build's schema."""
        return TraceBundle(
            contract_version=self.contract_version,
            source=self.source,
            source_schema=self.source_schema,
            source_version=self.source_version,
            exported_at=self.exported_at,
            suite_id=self.suite_id,
            app_version=self.app_version,
            record_schema=self.record_schema or record_schema_fingerprint(),
            payload_sha256=payload_digest(self.payload),
            payload=self.payload,
            notes=list(self.notes),
        )

    def verify(self) -> None:
        """Refuse a bundle whose payload does not match its recorded digest."""
        if not self.payload_sha256:
            return
        actual = payload_digest(self.payload)
        if actual != self.payload_sha256:
            raise VersioningError(
                f"bundle from {self.source!r}: payload digest mismatch "
                f"({actual} != {self.payload_sha256}); the payload was edited "
                "after export"
            )


def is_envelope(data: Any) -> bool:
    """Is ``data`` one of our envelopes, rather than a bare source document?"""
    return isinstance(data, dict) and ENVELOPE_REQUIRED_KEYS <= set(data)


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #


def native_payload(
    records: list[RunRecord], cases: list[EvalCase] | None = None
) -> dict[str, Any]:
    """The :data:`NATIVE_SOURCE` payload: records, and the cases they answer.

    Cases travel with the records because a trace without its evaluation case is
    not scorable: the case is where ``expected_properties``, the JSON schema and
    the allow-list live. A consumer that already has the suite may ignore them.
    """
    payload: dict[str, Any] = {"records": [record.to_dict() for record in records]}
    if cases:
        payload["cases"] = [case.to_dict() for case in cases]
    return payload


def export_bundle(
    records: list[RunRecord],
    *,
    exported_at: str,
    source: str = NATIVE_SOURCE,
    source_schema: str = NATIVE_SCHEMA,
    source_version: str = __version__,
    suite_id: str = "",
    app_version: str = "",
    cases: list[EvalCase] | None = None,
    notes: list[str] | None = None,
) -> TraceBundle:
    """Wrap ``records`` in a sealed envelope.

    ``exported_at`` is required and is a *recorded* value — the run's own
    timestamp, or one the caller chose. Defaulting it to the wall clock would make
    two exports of one run differ byte for byte, which the determinism rule
    forbids and which would defeat ``payload_sha256`` as a change detector.
    """
    return TraceBundle(
        contract_version=CONTRACT_VERSION,
        source=source,
        source_schema=source_schema,
        source_version=source_version,
        exported_at=exported_at,
        suite_id=suite_id,
        app_version=app_version,
        payload=native_payload(records, cases),
        notes=list(notes or []),
    ).sealed()


def write_bundle(path: Path | str, bundle: TraceBundle) -> Path:
    """Write a sealed envelope as pretty, sorted-key JSON."""
    return write_json(path, bundle.sealed())


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #


def read_document(path: Path | str) -> Any:
    """Read a JSON document — envelope or bare source trace — without judging it."""
    path = Path(path)
    if not path.is_file():
        raise VersioningError(f"{path}: no such trace document")
    return read_json(path)


def read_bundle(path: Path | str) -> TraceBundle:
    """Read, validate and verify an envelope. Refuses a bare source document."""
    data = read_document(path)
    if not is_envelope(data):
        raise VersioningError(
            f"{path}: not a trace bundle (needs "
            f"{sorted(ENVELOPE_REQUIRED_KEYS)}); import it as a bare document "
            "and name its --source"
        )
    try:
        bundle: TraceBundle = TraceBundle.from_dict(data)
    except SchemaError as exc:
        raise VersioningError(f"{path}: malformed trace bundle: {exc}") from exc
    check_contract_version(bundle.contract_version, source=str(path))
    bundle.verify()
    return bundle
