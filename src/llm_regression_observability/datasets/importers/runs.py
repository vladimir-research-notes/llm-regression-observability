"""An imported trace, executed as a run: ``results/runs/<run_id>/`` and nothing new.

The temptation here is to write a second artifact writer — imported records to
``run.jsonl``, plus a hand-rolled ``run_meta.json``. That would be a second place
where run identity, token accounting, cost estimation, redaction and the records
digest are decided, and the two would drift.

So an imported trace is executed instead. :class:`ImportedTraceAdapter` is a
:class:`~....adapters.base.ModelAdapter` whose "behavior" is a record somebody else
recorded, exactly as :class:`~....adapters.recorded_model.RecordedModelAdapter`
replays a committed fixture. Handing it to :func:`~....executor.execute_suite` buys,
unchanged:

* identity re-stamping — every record gets the run's ``run_id`` and ``app_version``,
  so ``(run_id, case_id)`` keys the artifact and a comparison can name its sides;
* redaction before accounting, and a refusal on a ``flagged`` record;
* token estimation where the source carried none, and cost from the ``CostModel``,
  which is how a source that reports no price (``claude_api``) is still priced under
  the same model as its baseline;
* ``run_meta.json`` with ``records_sha256`` over the bytes actually written.

The adapter is deliberately **not** registered in ``adapters.ADAPTER_NAMES``. It has
no fixtures to construct itself from and no meaning without a document, so
``llmreg run-suite --adapter imported`` could not work; ``llmreg import-trace`` is
the command, and it names the adapter in ``run_meta.adapter`` as
``imported-<source>`` so a report says where the trace came from.

What *is* new here is :class:`ImportMeta`: the warnings, the unmapped fields and the
defaulted attributions have to land in an artifact, or the honesty of the importer
survives only as long as somebody's terminal scrollback.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from ...adapters.base import AdapterContext, AdapterError, ModelAdapter
from ...executor import (
    CostModel,
    ExecutorError,
    RunConfig,
    RunResult,
    execute_suite,
    write_run,
)
from ...schemas import EvalCase, EvalSuite, RunRecord, Schema
from ...traces.storage import write_json
from ..fixtures import repo_root
from ..loader import load_eval_suite
from ..versioning import VersioningError, is_envelope, read_document
from .base import ImportContext, ImportResult, TraceImportError
from .registry import import_document

__all__ = [
    "IMPORT_META_JSON",
    "ImportedTraceAdapter",
    "ImportMeta",
    "ImportedRun",
    "adapter_name",
    "import_run",
]

#: Written next to ``run.jsonl``. Everything the import could not carry losslessly.
IMPORT_META_JSON = "import_meta.json"


def adapter_name(source: str) -> str:
    """``imported-<source>`` — what ``run_meta.adapter`` records for an imported run."""
    return f"imported-{source}"


class ImportedTraceAdapter(ModelAdapter):
    """Replay imported records by ``case_id``. Not registered as a CLI adapter."""

    name = "imported"

    def __init__(self, records: list[RunRecord] | tuple[RunRecord, ...]) -> None:
        self._by_case: dict[str, RunRecord] = {r.case_id: r for r in records}

    def generate(self, case: EvalCase, context: AdapterContext) -> RunRecord:
        record = self._by_case.get(case.case_id)
        if record is None:
            raise AdapterError(
                f"the imported trace has no record for case {case.case_id!r}; "
                f"it covers {sorted(self._by_case)}"
            )
        return copy.deepcopy(record)


# --------------------------------------------------------------------------- #
# The sidecar
# --------------------------------------------------------------------------- #


@dataclass(kw_only=True)
class ImportMeta(Schema):
    """The audit trail for one import — written to ``import_meta.json``.

    ``run_meta.json`` says what the run *is*. This says what the import *could not
    say*: which source fields were dropped by design, which appeared that this build
    has never seen, which ``RunRecord`` fields the source cannot fill, and every
    warning — an unmapped error kind defaulted to ``app``, a multi-model run
    flattened to one ``model_id``, a record whose text was masked. A comparison that
    reads clean over a trace half of which never arrived is precisely the lie an
    evaluation framework must not be able to tell quietly.
    """

    run_id: str
    source: str
    source_schema: str
    source_version: str
    contract_version: str
    record_schema: str
    document: str
    suite_id: str
    strict: bool = True
    case_count: int = 0
    imported_case_ids: list[str] = field(default_factory=list)
    cases_from_source: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ignored_fields: list[str] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)
    absent_fields: list[str] = field(default_factory=list)


@dataclass
class ImportedRun:
    """An imported trace, executed. Both halves of the audit trail, and the paths."""

    run: RunResult
    imported: ImportResult
    meta: ImportMeta
    run_dir: Path | None = None

    @property
    def import_meta_path(self) -> Path | None:
        return None if self.run_dir is None else self.run_dir / IMPORT_META_JSON


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #


def _check_covers_suite(
    suite: EvalSuite, result: ImportResult, *, document: str
) -> None:
    """Every case the suite names must be in the document, and no case may be extra.

    An extra case is not harmless. It means the trace answers a question this suite
    never asked, and the executor would drop it in silence — so the import that
    looked complete would have scored a subset of what it read.
    """
    wanted = [case.case_id for case in suite.cases]
    have = set(result.case_ids)
    missing = [case_id for case_id in wanted if case_id not in have]
    extra = sorted(have - set(wanted))
    if missing:
        raise TraceImportError(
            f"{document}: the imported trace covers no record for "
            f"{', '.join(missing)}, which suite {suite.suite_id!r} requires"
        )
    if extra:
        raise TraceImportError(
            f"{document}: the imported trace carries record(s) for "
            f"{', '.join(extra)}, which suite {suite.suite_id!r} does not define; "
            "they would be read and never scored"
        )


def import_run(
    document_path: Path | str,
    suite_path: Path | str,
    *,
    source: str | None = None,
    context: ImportContext,
    out_root: Path | str | None = None,
    cost_model: CostModel | None = None,
    fixtures_path: Path | str | None = None,
) -> ImportedRun:
    """Import a document, execute it against ``suite_path``, and write the run.

    ``out_root=None`` is the dry-run path: everything is computed, nothing is written.
    """
    try:
        document = read_document(document_path)
    except VersioningError as exc:
        raise TraceImportError(str(exc)) from exc
    envelope = document if is_envelope(document) else {}

    result = import_document(document, context, source=source)
    suite = load_eval_suite(suite_path, fixtures_path=fixtures_path)
    _check_covers_suite(suite, result, document=str(document_path))

    config = RunConfig(
        suite_path=Path(suite_path),
        adapter=adapter_name(result.source),
        app_version=context.app_version,
        run_id=context.run_id,
        cost_model=cost_model or CostModel(),
        fixtures_path=None if fixtures_path is None else Path(fixtures_path),
    )
    try:
        run = execute_suite(suite, ImportedTraceAdapter(result.records), config)
    except ExecutorError as exc:
        raise TraceImportError(f"{document_path}: {exc}") from exc

    meta = ImportMeta(
        run_id=run.meta.run_id,
        source=result.source,
        source_schema=result.source_schema,
        source_version=result.source_version,
        contract_version=str(envelope.get("contract_version", "")),
        record_schema=str(envelope.get("record_schema", "")),
        document=_display(document_path),
        suite_id=suite.suite_id,
        strict=context.strict,
        case_count=len(result.records),
        imported_case_ids=list(result.case_ids),
        cases_from_source=[case.case_id for case in result.cases],
        warnings=list(result.warnings),
        ignored_fields=list(result.ignored_fields),
        unknown_fields=list(result.unknown_fields),
        absent_fields=list(result.absent_fields),
    )

    imported = ImportedRun(run=run, imported=result, meta=meta)
    if out_root is not None:
        write_run(run, out_root)
        imported.run_dir = run.run_dir
        write_json(run.run_dir / IMPORT_META_JSON, meta)
    return imported


def _display(path: Path | str) -> str:
    """A repo-relative POSIX path when possible, so an artifact leaks no home dir."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(repo_root()).as_posix()
    except ValueError:
        return resolved.as_posix()
