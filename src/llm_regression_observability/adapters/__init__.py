"""Deterministic adapters: EvalCase in, RunRecord out — no model call, no network.

Four adapters (``docs/PLAN.md`` §8, ``docs/milestones/03-adapters.md``):

* :class:`MockModelAdapter` — fixed outputs for tests.
* :class:`RecordedModelAdapter` — replays committed fixture runs verbatim.
* :class:`ScriptedRegressionAdapter` — injects known regressions into a clean
  baseline (the ground-truth generator the detector is measured against).
* :class:`ApiAdapterStub` — the guarded paid-API adapter, **disabled by default**
  (Milestone 11). Its guards live in :mod:`.api_guard` and :mod:`.api_cache`.

Shared plumbing (:class:`AdapterContext`, :class:`AdapterResponse`,
:func:`build_run_record`) lives in :mod:`.base`.

:mod:`.api_transport` — the seam where a real provider client would go — is
deliberately **not** imported here. Re-exporting it would put a network client one
``import *`` away from the default path; ``tests/test_api_stub.py`` asserts this
module's source never names it.
"""

from __future__ import annotations

from .api_cache import (
    CACHE_VERSION,
    CacheEntry,
    CacheError,
    ResponseCache,
    cache_key,
    default_cache_dir,
)
from .api_guard import (
    ApiAdapterDisabled,
    ApiGuard,
    ApiGuardError,
    CostLedger,
    MaxCostExceeded,
    TransportMissing,
    UnverifiedPricing,
)
from .api_stub import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DRY_RUN_ERROR_KIND,
    SOURCE_CACHE,
    SOURCE_DRY_RUN,
    SOURCE_TRANSPORT,
    ApiAdapterStub,
    ApiCall,
    ApiPlan,
    ApiRequest,
    ApiResponse,
    Transport,
)
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
    "ADAPTER_NAMES",
    # Guarded paid-API adapter (Milestone 11). Disabled by default.
    "ApiAdapterStub",
    "ApiCall",
    "ApiPlan",
    "ApiRequest",
    "ApiResponse",
    "Transport",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DRY_RUN_ERROR_KIND",
    "SOURCE_CACHE",
    "SOURCE_DRY_RUN",
    "SOURCE_TRANSPORT",
    # Its guards.
    "ApiGuard",
    "ApiGuardError",
    "ApiAdapterDisabled",
    "MaxCostExceeded",
    "TransportMissing",
    "UnverifiedPricing",
    "CostLedger",
    # Its response cache.
    "CacheEntry",
    "CacheError",
    "ResponseCache",
    "cache_key",
    "default_cache_dir",
    "CACHE_VERSION",
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
