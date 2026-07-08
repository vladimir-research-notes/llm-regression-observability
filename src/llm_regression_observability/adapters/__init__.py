"""Deterministic adapters: EvalCase in, RunRecord out — no model call, no network.

Four adapters (``docs/PLAN.md`` §8, ``docs/milestones/03-adapters.md``):

* :class:`MockModelAdapter` — fixed outputs for tests.
* :class:`RecordedModelAdapter` — replays committed fixture runs verbatim.
* :class:`ScriptedRegressionAdapter` — injects known regressions into a clean
  baseline (the ground-truth generator the detector is measured against).
* :class:`ApiAdapterStub` — interface only, disabled by default (Milestone 11).

Shared plumbing (:class:`AdapterContext`, :class:`AdapterResponse`,
:func:`build_run_record`) lives in :mod:`.base`.
"""

from __future__ import annotations

from .api_stub import ApiAdapterDisabled, ApiAdapterStub
from .base import (
    AdapterContext,
    AdapterError,
    AdapterResponse,
    ModelAdapter,
    build_run_record,
    estimate_tokens,
    response_from_record,
)
from .mock_model import MockModelAdapter
from .recorded_model import RecordedModelAdapter
from .scripted_model import (
    ADD_COST,
    ADD_ERROR,
    ADD_LATENCY,
    ADD_TOOL,
    ARG_DRIFT,
    DROP_JSON_FIELD,
    DROP_SOURCE,
    DROP_TOOL,
    FLIP_REFUSAL,
    INJECTION_KINDS,
    INVALID_JSON,
    SET_JSON_FIELD,
    SET_PARSED_FIELD,
    SWAP_TOOL_ORDER,
    UNSUPPORTED_CLAIM,
    WRONG_TOOL,
    Injection,
    ScriptedRegressionAdapter,
)

#: Stable adapter names for the CLI / executor (Milestone 4).
ADAPTER_NAMES: tuple[str, ...] = (
    MockModelAdapter.name,
    RecordedModelAdapter.name,
    ScriptedRegressionAdapter.name,
    ApiAdapterStub.name,
)

__all__ = [
    "AdapterContext",
    "AdapterResponse",
    "AdapterError",
    "ModelAdapter",
    "build_run_record",
    "response_from_record",
    "estimate_tokens",
    "MockModelAdapter",
    "RecordedModelAdapter",
    "ScriptedRegressionAdapter",
    "Injection",
    "INJECTION_KINDS",
    "ApiAdapterStub",
    "ApiAdapterDisabled",
    "ADAPTER_NAMES",
    # Injection kinds:
    "INVALID_JSON",
    "DROP_JSON_FIELD",
    "SET_JSON_FIELD",
    "DROP_TOOL",
    "WRONG_TOOL",
    "ADD_TOOL",
    "ARG_DRIFT",
    "SWAP_TOOL_ORDER",
    "UNSUPPORTED_CLAIM",
    "DROP_SOURCE",
    "FLIP_REFUSAL",
    "SET_PARSED_FIELD",
    "ADD_LATENCY",
    "ADD_COST",
    "ADD_ERROR",
]
