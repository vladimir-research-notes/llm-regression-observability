"""Trace persistence and redaction.

The trace *shapes* live in :mod:`..schemas` (``RunRecord``, ``ToolCallTrace``,
``RetrievalTrace``, ``ErrorRecord``). This package owns two things that happen on
the way to disk:

:mod:`.redaction`
    The single definition of "credential-like" in this repository, the masking
    redactor built on it, and the fail-closed gate. It runs *in front of* every
    write — the executor redacts a ``RunRecord`` before it is hashed or persisted,
    the span writer gates ``spans.jsonl``, and the report generator masks free text
    before rendering (Milestone 9).

:mod:`.storage`
    Byte-stable JSON / JSONL readers and writers used by the run executor
    (Milestone 4), the comparison engine (Milestone 6), the report generator
    (Milestone 7), and the observability layer (Milestone 8).
"""

from __future__ import annotations

from .redaction import (
    CERTAIN_RULES,
    CLEAN,
    FLAGGED,
    MASK,
    REDACTED,
    RULES,
    SECRET_PATTERNS,
    RecordRedaction,
    RedactionError,
    RedactionRule,
    Redactor,
    TreeFinding,
    assert_clean,
    identity_findings,
    redact_record,
    redact_text,
    redact_value,
    scan_for_secrets,
    scan_text,
    scan_tree,
    secret_findings,
)
from .storage import (
    dumps_json,
    file_sha256,
    read_json,
    read_jsonl,
    sha256_hex,
    write_json,
    write_jsonl,
)

__all__ = [
    # Redaction
    "CERTAIN_RULES",
    "CLEAN",
    "FLAGGED",
    "MASK",
    "REDACTED",
    "RULES",
    "SECRET_PATTERNS",
    "RecordRedaction",
    "RedactionError",
    "RedactionRule",
    "Redactor",
    "TreeFinding",
    "assert_clean",
    "identity_findings",
    "redact_record",
    "redact_text",
    "redact_value",
    "scan_for_secrets",
    "scan_text",
    "scan_tree",
    "secret_findings",
    # Storage
    "dumps_json",
    "file_sha256",
    "read_json",
    "read_jsonl",
    "sha256_hex",
    "write_json",
    "write_jsonl",
]
