"""Trace persistence.

The trace *shapes* live in :mod:`..schemas` (``RunRecord``, ``ToolCallTrace``,
``RetrievalTrace``, ``ErrorRecord``). This package owns how they reach disk:
byte-stable JSON / JSONL readers and writers used by the run executor
(Milestone 4), the comparison engine (Milestone 6), and the report generator
(Milestone 7). Redaction (Milestone 9) lands here too, in front of every write.
"""

from __future__ import annotations

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
    "dumps_json",
    "file_sha256",
    "read_json",
    "read_jsonl",
    "sha256_hex",
    "write_json",
    "write_jsonl",
]
