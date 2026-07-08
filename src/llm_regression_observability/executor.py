"""Run-suite executor: cases + adapter → ``RunRecord``s on disk.

This is the "traces in, traces out" core (``docs/PLAN.md`` §4). It loads an
:class:`~.schemas.EvalSuite` from a manifest, drives one
:class:`~.adapters.ModelAdapter` over every case in manifest order, and writes
two artifacts:

``results/runs/<run_id>/run.jsonl``
    One :class:`~.schemas.RunRecord` per case, byte-stable JSONL.
``results/runs/<run_id>/run_meta.json``
    A :class:`RunMeta` describing the run: identity, totals, the cost model that
    was applied, and the SHA-256 of ``run.jsonl``.

Everything downstream (evaluators, comparison, reports, observability) reads
these two files, so three questions had to be answered here rather than left to
convention.

**Who owns run identity?** The executor. Adapters emit behavior; the
:class:`RecordedModelAdapter` in particular replays committed fixtures *verbatim*,
including their recorded ``run_id`` and ``timestamp`` (Milestone 3). Re-stamping
happens here: every record in a run carries the run's ``run_id`` and the
configured ``app_version``, so ``(run_id, case_id)`` is the artifact's key and
``ComparisonRecord.baseline_run_id`` / ``candidate_run_id`` mean exactly one run
each. ``model_id`` / ``prompt_version`` / ``tool_version`` / ``timestamp`` are
*provenance*, not identity: they are preserved as the adapter reported them
unless explicitly overridden, and :attr:`RunMeta.identity_overrides` records
which ones the caller forced.

**Where does latency come from?** The recorded input, never the wall clock.
Measuring elapsed time here would make artifacts differ run to run and would put
an ambient value into a scored field, which the determinism rule forbids. The
executor therefore *records* latency; it does not *measure* it. See
``docs/LIMITATIONS.md``.

**When are tokens and cost estimated?** Only where the adapter left them at zero.
Recorded fixtures carry their own token counts and costs and are treated as
recorded inputs. A synthetic response that supplies none gets
:func:`~.adapters.estimate_tokens` over the case input / final output and a cost
from :data:`SYNTHETIC_COST_MODEL`. ``recompute_cost`` forces the cost model over
every record, which is how two runs can be priced under one model regardless of
what their adapters claimed. :class:`RunMeta` counts how many records each path
touched, so an artifact never hides which numbers were derived.

No clock, no RNG, no network. Two runs on the same suite, adapter, and fixtures
produce byte-identical artifacts, modulo an explicit ``run_id``.
"""

from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from . import __version__
from .adapters import (
    ADAPTER_NAMES,
    AdapterContext,
    AdapterError,
    ApiAdapterStub,
    MockModelAdapter,
    ModelAdapter,
    RecordedModelAdapter,
    ScriptedRegressionAdapter,
    estimate_tokens,
)
from .datasets import load_eval_suite, repo_root
from .schemas import ErrorRecord, EvalCase, EvalSuite, RunRecord, Schema
from .traces.storage import (
    file_sha256,
    render_jsonl,
    sha256_hex,
    write_json,
    write_jsonl,
)

__all__ = [
    "ExecutorError",
    "CostModel",
    "SYNTHETIC_COST_MODEL",
    "RunConfig",
    "RunMeta",
    "RunResult",
    "DEFAULT_TIMESTAMP",
    "RUN_JSONL",
    "RUN_META_JSON",
    "build_adapter",
    "default_run_id",
    "execute_suite",
    "write_run",
    "run_suite",
]

#: Artifact file names inside ``results/runs/<run_id>/``.
RUN_JSONL = "run.jsonl"
RUN_META_JSON = "run_meta.json"

#: Used when neither the caller nor the adapter supplies a timestamp. A recorded
#: constant, never ``datetime.now()`` — timestamps are inputs (``CLAUDE.md``).
DEFAULT_TIMESTAMP = "1970-01-01T00:00:00Z"

#: Cost is rounded to this many decimals so float noise cannot break byte-stability.
COST_DECIMALS = 8

#: ``run_id`` doubles as a directory name; keep it a safe single path segment.
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: Identity fields the executor always forces onto every record in a run.
_FORCED_IDENTITY = ("run_id", "app_version")

#: Identity fields overridden only when the caller sets them explicitly.
_OPTIONAL_IDENTITY = ("model_id", "prompt_version", "tool_version", "timestamp")


class ExecutorError(RuntimeError):
    """Raised on an unusable run configuration or an adapter failure in strict mode."""


# --------------------------------------------------------------------------- #
# Cost model
# --------------------------------------------------------------------------- #


@dataclass(kw_only=True)
class CostModel(Schema):
    """A linear price per 1,000 tokens.

    The default prices are **synthetic placeholders** chosen to be in the right
    order of magnitude for a hosted model. They are not any vendor's real
    pricing, and nothing in this repository calls a paid API. They exist so that
    ``cost_estimate_usd`` is a defined, reproducible number rather than an
    absent one; a real adapter (Milestone 11) supplies its own.
    """

    input_usd_per_1k: float = 0.003
    output_usd_per_1k: float = 0.015

    def estimate(self, input_tokens: int, output_tokens: int) -> float:
        """Cost of a call, rounded to :data:`COST_DECIMALS` for byte-stability."""
        usd = (
            input_tokens / 1000.0 * self.input_usd_per_1k
            + output_tokens / 1000.0 * self.output_usd_per_1k
        )
        return round(usd, COST_DECIMALS)


#: The default price list. Synthetic; see :class:`CostModel`.
SYNTHETIC_COST_MODEL = CostModel()


# --------------------------------------------------------------------------- #
# Run configuration and metadata
# --------------------------------------------------------------------------- #


@dataclass(kw_only=True)
class RunConfig:
    """Everything needed to reproduce one run.

    ``run_id`` defaults to :func:`default_run_id`. The four provenance fields
    default to ``None``, meaning *preserve whatever the adapter reported*.
    """

    suite_path: Path
    adapter: str = "recorded"
    app_version: str = "baseline"
    run_id: str | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    tool_version: str | None = None
    timestamp: str | None = None
    cost_model: CostModel = field(default_factory=CostModel)
    recompute_cost: bool = False
    fixtures_path: Path | None = None
    strict: bool = True

    def identity_overrides(self) -> list[str]:
        """Provenance fields the caller forced, in a stable order."""
        return [f for f in _OPTIONAL_IDENTITY if getattr(self, f) is not None]


@dataclass(kw_only=True)
class RunMeta(Schema):
    """Sidecar for one run — written to ``run_meta.json``.

    Totals are derived from the records, so the file is a self-check: recompute
    them from ``run.jsonl`` and they must agree, and ``records_sha256`` must
    equal the SHA-256 of that file's bytes.
    """

    run_id: str
    suite_id: str
    suite_path: str
    adapter: str
    app_version: str
    model_id: str
    prompt_version: str
    tool_version: str
    timestamp: str
    case_count: int = 0
    input_tokens_total: int = 0
    output_tokens_total: int = 0
    cost_total_usd: float = 0.0
    latency_ms_total: float = 0.0
    tool_call_count: int = 0
    retrieval_source_count: int = 0
    error_count: int = 0
    app_error_count: int = 0
    infra_error_count: int = 0
    tokens_estimated_cases: int = 0
    cost_estimated_cases: int = 0
    identity_overrides: list[str] = field(default_factory=list)
    cost_model: CostModel = field(default_factory=CostModel)
    records_sha256: str = ""
    llmreg_version: str = __version__

    #: ``"(mixed)"`` is the sentinel for a provenance field that is not constant
    #: across the run's records.
    MIXED: ClassVar[str] = "(mixed)"


@dataclass
class RunResult:
    """The in-memory outcome of a run, plus the paths it was written to."""

    meta: RunMeta
    records: list[RunRecord]
    run_dir: Path | None = None

    @property
    def run_path(self) -> Path | None:
        return None if self.run_dir is None else self.run_dir / RUN_JSONL

    @property
    def meta_path(self) -> Path | None:
        return None if self.run_dir is None else self.run_dir / RUN_META_JSON


# --------------------------------------------------------------------------- #
# Adapter selection
# --------------------------------------------------------------------------- #


def build_adapter(
    name: str, *, fixtures_path: Path | str | None = None
) -> ModelAdapter:
    """Construct the adapter named on the command line.

    ``api-stub`` is constructible but inert: it raises as soon as it is asked to
    generate, so selecting it can never reach a paid API (Milestone 11).
    """
    if name == RecordedModelAdapter.name:
        return RecordedModelAdapter.from_fixtures(fixtures_path)
    if name == ScriptedRegressionAdapter.name:
        return ScriptedRegressionAdapter.from_fixtures(fixtures_path)
    if name == MockModelAdapter.name:
        return MockModelAdapter()
    if name == ApiAdapterStub.name:
        return ApiAdapterStub()
    raise ExecutorError(
        f"unknown adapter {name!r}; choose from {', '.join(sorted(ADAPTER_NAMES))}"
    )


def default_run_id(suite_id: str, app_version: str, adapter: str) -> str:
    """A deterministic, filesystem-safe run id: ``<suite>-<version>-<adapter>``."""
    return f"{suite_id}-{app_version}-{adapter}"


def _check_run_id(run_id: str) -> str:
    if not _SAFE_RUN_ID.match(run_id):
        raise ExecutorError(
            f"run_id {run_id!r} is not a safe directory name "
            "(allowed: letters, digits, '.', '_', '-'; must start alphanumeric)"
        )
    return run_id


# --------------------------------------------------------------------------- #
# Token / cost accounting
# --------------------------------------------------------------------------- #


def _input_text(case: EvalCase) -> str:
    """The text billed as input: prompt, structured payload, retrieval context.

    Serialized with sorted keys so the estimate does not depend on dict order.
    """
    parts = [case.prompt]
    if case.input_payload:
        parts.append(
            json.dumps(
                case.input_payload,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    for doc in case.retrieval_docs or []:
        parts.append(doc.text)
    return "\n".join(p for p in parts if p)


@dataclass
class _Accounting:
    """Whether the executor derived a record's tokens / cost, or the adapter did."""

    input_tokens: int
    output_tokens: int
    cost_usd: float
    tokens_estimated: bool
    cost_estimated: bool


def _account(record: RunRecord, case: EvalCase, config: RunConfig) -> _Accounting:
    input_tokens = record.input_tokens_estimate
    output_tokens = record.output_tokens_estimate
    tokens_estimated = False
    if input_tokens <= 0:
        input_tokens = estimate_tokens(_input_text(case))
        tokens_estimated = True
    if output_tokens <= 0 and record.final_output:
        output_tokens = estimate_tokens(record.final_output)
        tokens_estimated = True

    cost = record.cost_estimate_usd
    cost_estimated = False
    if config.recompute_cost or (cost <= 0.0 and (input_tokens or output_tokens)):
        cost = config.cost_model.estimate(input_tokens, output_tokens)
        cost_estimated = True

    return _Accounting(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        tokens_estimated=tokens_estimated,
        cost_estimated=cost_estimated,
    )


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #


def _default_model_id(config: RunConfig) -> str:
    """What produced these outputs, absent an explicit ``--model-id``.

    Naming the *adapter* keeps the artifact honest: a synthetic run says so
    rather than claiming a model it never called. The recorded adapter replays
    verbatim, so its records keep their own recorded ``model_id`` regardless.
    """
    return config.model_id or f"{config.adapter}-adapter"


def _context(config: RunConfig) -> AdapterContext:
    """The identity handed to the adapter. Its timestamp is a recorded input."""
    return AdapterContext(
        app_version=config.app_version,
        model_id=_default_model_id(config),
        prompt_version=config.prompt_version or "p-0",
        tool_version=config.tool_version or "t-0",
        timestamp=config.timestamp or DEFAULT_TIMESTAMP,
    )


def _adapter_failure_record(
    case: EvalCase, config: RunConfig, run_id: str, exc: Exception
) -> RunRecord:
    """A record standing in for a case the adapter could not produce.

    The error is tagged ``infra``: the *harness* failed, not the application, so
    it must not later be counted as an ``error_rate_regression`` (research
    question 9).
    """
    return RunRecord(
        run_id=run_id,
        case_id=case.case_id,
        app_version=config.app_version,
        model_id=_default_model_id(config),
        prompt_version=config.prompt_version or "p-0",
        tool_version=config.tool_version or "t-0",
        timestamp=config.timestamp or DEFAULT_TIMESTAMP,
        errors=[
            ErrorRecord(kind="adapter_error", message=str(exc), origin="infra"),
        ],
    )


def _restamp(record: RunRecord, config: RunConfig, run_id: str) -> RunRecord:
    """Force run identity onto a record; override provenance only if configured."""
    updates: dict[str, str] = {"run_id": run_id, "app_version": config.app_version}
    for name in _OPTIONAL_IDENTITY:
        value = getattr(config, name)
        if value is not None:
            updates[name] = value
    return dataclasses.replace(record, **updates)


def _summarize(
    records: list[RunRecord],
    accounts: list[_Accounting],
    suite: EvalSuite,
    config: RunConfig,
    run_id: str,
) -> RunMeta:
    def _constant(field_name: str) -> str:
        values = {getattr(r, field_name) for r in records}
        if len(values) == 1:
            return values.pop()
        if not values:
            return getattr(config, field_name, None) or RunMeta.MIXED
        return RunMeta.MIXED

    errors = [e for r in records for e in r.errors]
    return RunMeta(
        run_id=run_id,
        suite_id=suite.suite_id,
        suite_path=_display_path(config.suite_path),
        adapter=config.adapter,
        app_version=config.app_version,
        model_id=_constant("model_id"),
        prompt_version=_constant("prompt_version"),
        tool_version=_constant("tool_version"),
        timestamp=_constant("timestamp"),
        case_count=len(records),
        input_tokens_total=sum(r.input_tokens_estimate for r in records),
        output_tokens_total=sum(r.output_tokens_estimate for r in records),
        cost_total_usd=round(sum(r.cost_estimate_usd for r in records), COST_DECIMALS),
        latency_ms_total=round(sum(r.latency_ms for r in records), 6),
        tool_call_count=sum(len(r.tool_calls) for r in records),
        retrieval_source_count=sum(len(r.retrieval_sources) for r in records),
        error_count=len(errors),
        app_error_count=sum(1 for e in errors if e.origin == "app"),
        infra_error_count=sum(1 for e in errors if e.origin == "infra"),
        tokens_estimated_cases=sum(1 for a in accounts if a.tokens_estimated),
        cost_estimated_cases=sum(1 for a in accounts if a.cost_estimated),
        identity_overrides=config.identity_overrides(),
        cost_model=config.cost_model,
        records_sha256=sha256_hex(render_jsonl(records)),
    )


def _display_path(path: Path) -> str:
    """Repo-relative POSIX path when possible, so artifacts leak no home dir."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(repo_root()).as_posix()
    except ValueError:
        return resolved.as_posix()


def execute_suite(
    suite: EvalSuite, adapter: ModelAdapter, config: RunConfig
) -> RunResult:
    """Drive ``adapter`` over every case of ``suite``. Pure: writes nothing."""
    # ``None`` means "derive one"; an explicit value — including "" — is checked.
    run_id = config.run_id
    if run_id is None:
        run_id = default_run_id(suite.suite_id, config.app_version, config.adapter)
    run_id = _check_run_id(run_id)
    context = _context(config)

    records: list[RunRecord] = []
    accounts: list[_Accounting] = []
    for case in suite.cases:
        try:
            record = adapter.generate(case, context)
        except (AdapterError, NotImplementedError) as exc:
            if config.strict:
                raise ExecutorError(
                    f"adapter {config.adapter!r} failed on case "
                    f"{case.case_id!r}: {exc}"
                ) from exc
            record = _adapter_failure_record(case, config, run_id, exc)

        record = _restamp(record, config, run_id)
        account = _account(record, case, config)
        record = dataclasses.replace(
            record,
            input_tokens_estimate=account.input_tokens,
            output_tokens_estimate=account.output_tokens,
            cost_estimate_usd=account.cost_usd,
        )
        records.append(record)
        accounts.append(account)

    meta = _summarize(records, accounts, suite, config, run_id)
    return RunResult(meta=meta, records=records)


def write_run(result: RunResult, out_root: Path | str) -> RunResult:
    """Write ``run.jsonl`` and ``run_meta.json`` under ``out_root/<run_id>/``."""
    run_dir = Path(out_root) / result.meta.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    run_path = write_jsonl(run_dir / RUN_JSONL, result.records)
    write_json(run_dir / RUN_META_JSON, result.meta)

    written = file_sha256(run_path)
    if written != result.meta.records_sha256:  # pragma: no cover - defensive
        raise ExecutorError(
            f"{run_path}: digest mismatch after write "
            f"({written} != {result.meta.records_sha256})"
        )
    result.run_dir = run_dir
    return result


def run_suite(config: RunConfig, out_root: Path | str | None = None) -> RunResult:
    """Load the suite, build the adapter, execute, and (unless ``out_root`` is
    ``None``) write the artifacts. ``out_root=None`` is the dry-run path."""
    suite = load_eval_suite(config.suite_path, fixtures_path=config.fixtures_path)
    adapter = build_adapter(config.adapter, fixtures_path=config.fixtures_path)
    result = execute_suite(suite, adapter, config)
    if out_root is not None:
        write_run(result, out_root)
    return result
