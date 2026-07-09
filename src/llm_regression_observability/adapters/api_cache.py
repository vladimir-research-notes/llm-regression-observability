"""A content-addressed response cache, so a repeated run never re-spends.

The cache is the least glamorous of the five guards and the one that saves the
most money in practice: an evaluation suite is re-run constantly — after a
scorer changes, after a threshold moves, after a report template is edited — and
none of those reasons is a reason to pay the provider again.

**The key is the request, not the case.** :func:`cache_key` hashes exactly the
fields that determine the provider's answer: model id, prompt, structured input,
the output-token bound, and the prompt / tool versions. It deliberately omits
``case_id`` (two cases with an identical request must share one response),
``app_version`` (a label this repository attaches, invisible to the provider) and
``timestamp`` (which would make every key unique and the cache useless). Getting
this set wrong in either direction is expensive: too many fields and you re-spend
on every run, too few and you serve a stale answer for a changed prompt.

**A cache hit is free, and consumes no budget.** The record it produces carries
the cost of the call *when it was originally made* — that is the case's cost, and
the comparison engine is right to see it — while
:class:`~.api_guard.CostLedger` is never charged, because this run spent nothing.
Conflating the two would mean a completed suite could not be re-run under the
same ceiling it already paid for.

**Writes are atomic.** An entry is written to a temporary file and renamed into
position, so an interrupted run cannot leave a half-written JSON file behind. That
matters more than it looks: a corrupt entry read as a *miss* would silently
re-spend, so :meth:`ResponseCache.get` refuses a corrupt file loudly instead —
and atomic writes mean it should never see one.

The cache directory (``.api_cache/`` by default) is gitignored: it holds real
provider responses, which are exactly the kind of thing ``CLAUDE.md`` forbids
committing. Nothing in this module touches the filesystem unless the cache is
enabled, which the default (disabled) adapter never does.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..schemas import Schema, SchemaError, ToolCallTrace

__all__ = [
    "CACHE_VERSION",
    "DEFAULT_CACHE_DIRNAME",
    "CacheError",
    "CacheEntry",
    "ResponseCache",
    "cache_key",
    "default_cache_dir",
]

#: Bumping this invalidates every stored entry: the key includes it, so an entry
#: written under an older request encoding can never be served for a new one.
CACHE_VERSION = 1

#: Gitignored (see ``.gitignore``); holds real provider responses.
DEFAULT_CACHE_DIRNAME = ".api_cache"

_SUFFIX = ".json"
_TMP_SUFFIX = ".tmp"


class CacheError(RuntimeError):
    """Raised when a cache entry exists but cannot be read as one."""


def default_cache_dir() -> Path:
    """``<repo root>/.api_cache`` — gitignored, and resolved lazily.

    The import of :mod:`..datasets` is deliberately inside the function: this is
    called only when an *enabled* adapter needs a cache, so importing
    :mod:`..adapters` never pays for the dataset loaders and never probes the
    filesystem on the default, opted-out path.
    """
    from ..datasets.fixtures import repo_root

    return repo_root() / DEFAULT_CACHE_DIRNAME


def cache_key(payload: dict[str, Any]) -> str:
    """SHA-256 over the canonical JSON encoding of a request payload.

    Sorted keys and compact separators, so the key depends on the request's
    content and not on dict ordering or whitespace.
    """
    canonical = json.dumps(
        {**payload, "cache_version": CACHE_VERSION},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(kw_only=True)
class CacheEntry(Schema):
    """One cached provider response, plus what the call cost when it was made.

    ``stored_at`` is a recorded input (the run's timestamp), never the clock —
    the same rule every other artifact in this repository follows.
    """

    key: str
    model_id: str
    final_output: str = ""
    parsed_output: Any = None
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    stored_at: str = ""
    cache_version: int = CACHE_VERSION


class ResponseCache:
    """A directory of :class:`CacheEntry` files, or a disabled no-op.

    ``ResponseCache(None)`` (equivalently :meth:`disabled`) never touches the
    filesystem: :meth:`get` always misses and :meth:`put` writes nothing. That is
    the state the default, opted-out adapter is constructed in, so importing this
    package cannot create a directory.
    """

    def __init__(self, directory: Path | str | None) -> None:
        self.directory = Path(directory) if directory is not None else None

    @classmethod
    def disabled(cls) -> ResponseCache:
        return cls(None)

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def path_for(self, key: str) -> Path | None:
        if self.directory is None:
            return None
        return self.directory / f"{key}{_SUFFIX}"

    def get(self, key: str) -> CacheEntry | None:
        """The stored entry for ``key``, or ``None`` on a miss.

        A file that exists but does not parse raises :class:`CacheError` rather
        than reporting a miss: a miss re-spends, and a corrupt file is not
        evidence that the call was never made.
        """
        path = self.path_for(key)
        if path is None or not path.is_file():
            return None
        try:
            entry = CacheEntry.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, SchemaError, OSError) as exc:
            raise CacheError(
                f"{path}: cache entry is unreadable ({exc}). Refusing to treat it "
                "as a miss, which would call the provider again. Delete the file "
                "to force a fresh call."
            ) from exc
        if entry.key != key:
            raise CacheError(f"{path}: entry key {entry.key!r} does not match {key!r}")
        return entry

    def put(self, entry: CacheEntry) -> Path | None:
        """Store ``entry`` atomically. Returns its path, or ``None`` if disabled."""
        path = self.path_for(entry.key)
        if path is None:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(_SUFFIX + _TMP_SUFFIX)
        tmp.write_text(entry.to_json(indent=2) + "\n", encoding="utf-8", newline="\n")
        tmp.replace(path)
        return path
