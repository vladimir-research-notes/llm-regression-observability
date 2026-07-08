"""Byte-stable JSON / JSONL persistence for trace artifacts.

Every artifact this project writes has to satisfy one property: **two runs on
the same inputs produce byte-identical files** (``CLAUDE.md``, determinism
rule). That is not automatic — ``json.dumps`` defaults to insertion-ordered
keys, ``ensure_ascii=True``, and space-padded separators, and ``Path.write_text``
inherits the platform line ending. This module fixes all four, in one place, so
no caller has to remember:

* **sorted keys** — dict iteration order never leaks into the bytes;
* **compact separators** for JSONL, two-space indent for single-object JSON;
* ``ensure_ascii=False`` with an explicit UTF-8 encoding;
* an explicit ``"\\n"`` newline, including a trailing one, so the files are
  append-friendly and ``sha256`` is stable across platforms.

Records are validated on the way in *and* on the way out: :func:`read_jsonl`
takes the :class:`~..schemas.Schema` subclass it expects and raises a
path-and-line-qualified :class:`~..schemas.SchemaError` on anything malformed,
so a corrupt artifact fails at load rather than halfway down the pipeline.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..schemas import Schema, SchemaError

__all__ = [
    "dumps_json",
    "render_jsonl",
    "write_jsonl",
    "read_jsonl",
    "write_json",
    "read_json",
    "sha256_hex",
    "file_sha256",
]

ENCODING = "utf-8"
NEWLINE = "\n"


def dumps_json(obj: Any, *, indent: int | None = 2) -> str:
    """Serialize plain data with sorted keys — the byte-stable spelling."""
    separators = (",", ": ") if indent is not None else (",", ":")
    return json.dumps(
        obj,
        sort_keys=True,
        ensure_ascii=False,
        separators=separators,
        indent=indent,
    )


def render_jsonl(records: Iterable[Schema]) -> str:
    """Render records as JSONL text (one compact, sorted-key object per line).

    Exposed separately from :func:`write_jsonl` so a caller can hash exactly the
    bytes it is about to write without reading the file back.
    """
    return "".join(record.to_json() + NEWLINE for record in records)


def write_jsonl(path: Path | str, records: Iterable[Schema]) -> Path:
    """Write ``records`` to ``path`` as JSONL, creating parent directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_jsonl(records), encoding=ENCODING, newline=NEWLINE)
    return path


def read_jsonl[S: Schema](path: Path | str, model: type[S]) -> list[S]:
    """Read a JSONL stream of ``model`` records, validating each line."""
    path = Path(path)
    out: list[S] = []
    with path.open(encoding=ENCODING) as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SchemaError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
            try:
                out.append(model.from_dict(data))
            except SchemaError as exc:
                raise SchemaError(f"{path}:{lineno}: {exc}") from exc
    return out


def write_json(path: Path | str, obj: Schema | Any, *, indent: int | None = 2) -> Path:
    """Write a single :class:`Schema` (or plain data) as pretty, sorted JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, Schema):
        text = obj.to_json(indent=indent)
    else:
        text = dumps_json(obj, indent=indent)
    path.write_text(text + NEWLINE, encoding=ENCODING, newline=NEWLINE)
    return path


def read_json[S: Schema](path: Path | str, model: type[S] | None = None) -> Any:
    """Read a single JSON object, optionally validating it against ``model``."""
    path = Path(path)
    with path.open(encoding=ENCODING) as fh:
        data = json.load(fh)
    if model is None:
        return data
    try:
        return model.from_dict(data)
    except SchemaError as exc:
        raise SchemaError(f"{path}: {exc}") from exc


def sha256_hex(data: bytes | str) -> str:
    """SHA-256 of ``data`` (UTF-8 encoded if a ``str``)."""
    if isinstance(data, str):
        data = data.encode(ENCODING)
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path | str) -> str:
    """SHA-256 of a file's bytes, read in chunks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
