"""``RecordedModelAdapter`` — replay committed fixture outputs.

Given the recorded :class:`RunRecord` streams under ``data/fixtures/`` (Milestone
2), this adapter returns the record for ``(app_version, case_id)`` **exactly as
recorded** — a faithful replay, so a run over it reproduces the committed
fixtures byte for byte. It is the baseline generator in the offline smoke
pipeline (``--adapter recorded --app-version baseline``; see ``docs/PLAN.md`` §9).

Because it replays verbatim, it uses the context only to pick the stream
(``app_version``); the recorded ``run_id`` / ``timestamp`` / ``model_id`` are
preserved. The executor (Milestone 4) owns any re-identification of replayed runs.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from pathlib import Path

from ..schemas import EvalCase, RunRecord
from .base import AdapterContext, AdapterError, ModelAdapter

__all__ = ["RecordedModelAdapter"]


class RecordedModelAdapter(ModelAdapter):
    """Replay recorded run records, keyed by ``(app_version, case_id)``."""

    name = "recorded"

    def __init__(self, records: Iterable[RunRecord]) -> None:
        self._by_key: dict[tuple[str, str], RunRecord] = {}
        for record in records:
            key = (record.app_version, record.case_id)
            if key in self._by_key:
                raise AdapterError(
                    f"duplicate recorded run for app_version={key[0]!r} "
                    f"case_id={key[1]!r}"
                )
            self._by_key[key] = record

    @classmethod
    def from_fixtures(cls, path: Path | str | None = None) -> RecordedModelAdapter:
        """Build from every suite's baseline + candidate streams under ``path``
        (default: the repository fixtures)."""
        # Imported lazily so importing the adapter package does not pull in the
        # datasets loaders unless fixtures are actually requested.
        from ..datasets import (
            SUITE_NAMES,
            load_suite_fixture,
            resolve_fixture_roots,
        )

        evals_root, data_root = resolve_fixture_roots(path)
        records: list[RunRecord] = []
        for suite in SUITE_NAMES:
            fixture = load_suite_fixture(suite, evals_root, data_root)
            records.extend(fixture.baseline)
            records.extend(fixture.candidate)
        return cls(records)

    def has(self, app_version: str, case_id: str) -> bool:
        return (app_version, case_id) in self._by_key

    def generate(self, case: EvalCase, context: AdapterContext) -> RunRecord:
        key = (context.app_version, case.case_id)
        record = self._by_key.get(key)
        if record is None:
            raise AdapterError(
                f"no recorded run for app_version={context.app_version!r} "
                f"case_id={case.case_id!r}"
            )
        # Return a copy so callers cannot mutate the shared recorded record.
        return copy.deepcopy(record)
