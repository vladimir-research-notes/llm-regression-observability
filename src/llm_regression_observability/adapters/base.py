"""Adapter interface and shared helpers.

An *adapter* turns an :class:`~..schemas.EvalCase` into a
:class:`~..schemas.RunRecord` — the behavioral output of one (case, run) — so the
whole pipeline runs offline with **no model call and no network** (see
``docs/PLAN.md`` §8 and ``docs/milestones/03-adapters.md``).

The contract is split into two pieces so that *what the model produced*
(behavioral content) is kept separate from *which run this is* (identity):

* :class:`AdapterContext` carries the run-level identity — ``app_version``,
  ``model_id``, prompt/tool versions, and the recorded ``timestamp``. Nothing
  here is sampled from the ambient clock; the timestamp is an input, exactly as
  ``CLAUDE.md`` requires.
* :class:`AdapterResponse` carries the behavioral output — final/parsed output,
  tool calls, retrieval sources, errors, and token/latency/cost estimates.

:func:`build_run_record` stitches the two into a :class:`RunRecord`. Because the
identity fields come from the context (never the clock) and the behavioral fields
come from deterministic adapters, two runs on the same inputs produce
byte-identical records except for the parts of the context the caller varies.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from ..schemas import (
    ErrorRecord,
    EvalCase,
    RetrievalTrace,
    RunRecord,
    ToolCallTrace,
)

__all__ = [
    "AdapterError",
    "AdapterContext",
    "AdapterResponse",
    "ModelAdapter",
    "estimate_tokens",
    "response_from_record",
    "build_run_record",
]


class AdapterError(RuntimeError):
    """Raised when an adapter cannot produce an output (e.g. a missing recorded
    run, an unknown injection kind, or a disabled paid-API stub)."""


def estimate_tokens(text: str) -> int:
    """A deterministic, naive token estimate (~4 characters per token).

    Deliberately crude: it exists so synthetic adapters can populate the
    ``*_tokens_estimate`` fields without a tokenizer dependency. The run executor
    (Milestone 4) and the paid-API adapter (Milestone 11) refine cost/token
    accounting; nothing here claims to match a real tokenizer.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


@dataclass(kw_only=True)
class AdapterContext:
    """Run-level identity for one adapter invocation.

    ``run_id`` defaults to ``"<case_id>-<app_version>"`` (the fixture naming
    convention) unless ``run_id_prefix`` is set, in which case it is
    ``"<run_id_prefix><case_id>"``. Timestamps/versions are recorded inputs.
    """

    app_version: str
    model_id: str = "mock-model"
    prompt_version: str = "p-0"
    tool_version: str = "t-0"
    timestamp: str = "1970-01-01T00:00:00Z"
    run_id_prefix: str | None = None

    def run_id_for(self, case_id: str) -> str:
        if self.run_id_prefix is not None:
            return f"{self.run_id_prefix}{case_id}"
        return f"{case_id}-{self.app_version}"


@dataclass(kw_only=True)
class AdapterResponse:
    """The behavioral content an adapter produces for one case, before run-level
    identity is attached. All token/latency/cost values are recorded inputs."""

    final_output: str = ""
    parsed_output: Any = None
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    retrieval_sources: list[RetrievalTrace] = field(default_factory=list)
    errors: list[ErrorRecord] = field(default_factory=list)
    input_tokens_estimate: int = 0
    output_tokens_estimate: int = 0
    latency_ms: float = 0.0
    cost_estimate_usd: float = 0.0
    redaction_status: str = "clean"

    def copy(self) -> AdapterResponse:
        """A deep copy, so callers (and injectors) never mutate a shared source."""
        return copy.deepcopy(self)


def response_from_record(record: RunRecord) -> AdapterResponse:
    """Extract the behavioral fields of ``record`` into an :class:`AdapterResponse`.

    The nested trace objects are deep-copied so the response is independent of the
    record it came from.
    """
    return AdapterResponse(
        final_output=record.final_output,
        parsed_output=copy.deepcopy(record.parsed_output),
        tool_calls=copy.deepcopy(record.tool_calls),
        retrieval_sources=copy.deepcopy(record.retrieval_sources),
        errors=copy.deepcopy(record.errors),
        input_tokens_estimate=record.input_tokens_estimate,
        output_tokens_estimate=record.output_tokens_estimate,
        latency_ms=record.latency_ms,
        cost_estimate_usd=record.cost_estimate_usd,
        redaction_status=record.redaction_status,
    )


def build_run_record(
    case: EvalCase, context: AdapterContext, response: AdapterResponse
) -> RunRecord:
    """Combine a case, a run context, and behavioral output into a RunRecord."""
    return RunRecord(
        run_id=context.run_id_for(case.case_id),
        case_id=case.case_id,
        app_version=context.app_version,
        model_id=context.model_id,
        prompt_version=context.prompt_version,
        tool_version=context.tool_version,
        timestamp=context.timestamp,
        input_tokens_estimate=response.input_tokens_estimate,
        output_tokens_estimate=response.output_tokens_estimate,
        latency_ms=response.latency_ms,
        cost_estimate_usd=response.cost_estimate_usd,
        final_output=response.final_output,
        parsed_output=copy.deepcopy(response.parsed_output),
        tool_calls=copy.deepcopy(response.tool_calls),
        retrieval_sources=copy.deepcopy(response.retrieval_sources),
        errors=copy.deepcopy(response.errors),
        redaction_status=response.redaction_status,
    )


class ModelAdapter:
    """Base class for every adapter.

    Subclasses implement :meth:`generate`; the base only fixes the interface and a
    convenience :meth:`generate_all`. Kept a plain class (not an ABC) to match the
    dependency-free, stdlib-first style of the rest of the package.
    """

    #: Short, stable name used by the CLI / executor (Milestone 4) to select it.
    name: str = "adapter"

    def generate(self, case: EvalCase, context: AdapterContext) -> RunRecord:
        raise NotImplementedError

    def generate_all(
        self, cases: list[EvalCase], context: AdapterContext
    ) -> list[RunRecord]:
        """Generate a record for every case, in order."""
        return [self.generate(case, context) for case in cases]
