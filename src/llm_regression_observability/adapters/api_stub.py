"""``ApiAdapterStub`` — the guarded paid-API adapter. Disabled by default.

This is the only adapter in the repository that *could* cost money, and it is
built so that it cannot until an operator has said so four separate times. The
guards are the deliverable (``docs/milestones/11-api-adapter-cost-guard.md``);
the network call is not, and this package ships no network client at all. See
``docs/API_ADAPTER.md``.

Every call passes through the same fixed ladder, and the order is the design:

1. **Opt-in** (:meth:`ApiGuard.check_opt_in`). Not enabled →
   :class:`~.api_guard.ApiAdapterDisabled`.
2. **Cache** (:mod:`.api_cache`). A hit returns the recorded response and stops.
   It is checked *before* the budget, because a cache hit spends nothing and must
   not consume budget — otherwise re-running a suite fails on money it already
   paid. It is checked before the dry-run branch too, so a dry run over a warm
   cache returns the real answers, free.
3. **Estimate** (:mod:`..pricing`). Real input tokens, output priced at
   ``max_output_tokens`` — an upper bound, not a prediction. An unpriced model
   raises :class:`~..pricing.UnpricedModel` rather than defaulting to a number
   that looks like a cost.
4. **Dry run** (the default, even once opted in). Returns a record carrying the
   estimate and no output. Nothing below this line executes.
5. **Verified pricing.** A ceiling enforced against unchecked prices is theater,
   so the spend path refuses an unverified :class:`~..pricing.PriceList`.
6. **Ceiling** (:class:`~.api_guard.CostLedger`). Refuses — never truncates — a
   call whose worst-case cost would push cumulative spend past ``max_cost_usd``,
   which defaults to ``0.0``. The refusal happens *before* the transport is
   reached, so an over-budget suite makes zero calls.
7. **Transport.** Absent by default → :class:`~.api_guard.TransportMissing`.

Steps 5 and 6 sit above step 7 deliberately: budget and pricing are policy, and
policy is decided without reference to whether a client happens to be installed.

**What the transport must not invent.** A response reporting zero tokens, or zero
latency, is refused. This is the rule Milestone 10's importers established: a
missing measurement imported as ``0.0`` is not missing, it is a *number*, and the
comparison engine believes numbers — a zero-latency baseline makes every
candidate an unbounded ``latency_regression``. Cost cannot be settled from usage
that was never reported, either, so the ledger would silently under-count.

**Determinism.** Nothing in this module reads a clock, an RNG, or a socket:
``stored_at`` is the run's recorded timestamp and latency is whatever the
transport reports. A live call is inherently nondeterministic — that is the
property of calling a real API, not of this code — and it is confined to the
transport at the very bottom of the ladder, behind all seven gates, in a module
(:mod:`.api_transport`) that nothing imports by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..pricing import (
    COST_DECIMALS,
    CostEstimate,
    PriceList,
)
from ..schemas import ErrorRecord, EvalCase, RunRecord, ToolCallTrace
from .api_cache import CacheEntry, ResponseCache, cache_key, default_cache_dir
from .api_guard import (
    ApiAdapterDisabled,
    ApiGuard,
    ApiGuardError,
    TransportMissing,
    UnverifiedPricing,
)
from .base import AdapterContext, AdapterResponse, ModelAdapter, build_run_record

__all__ = [
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DRY_RUN_ERROR_KIND",
    "SOURCE_CACHE",
    "SOURCE_DRY_RUN",
    "SOURCE_TRANSPORT",
    "ApiRequest",
    "ApiResponse",
    "Transport",
    "ApiCall",
    "ApiPlan",
    "ApiAdapterStub",
    "ApiAdapterDisabled",
]

#: The output-token bound used to price a call before it is made. A budget guard
#: needs the worst case the API is permitted to produce, not the average.
DEFAULT_MAX_OUTPUT_TOKENS = 1024

#: Where a record came from, recorded on :class:`ApiCall`.
SOURCE_CACHE = "cache"
SOURCE_DRY_RUN = "dry_run"
SOURCE_TRANSPORT = "transport"

#: A dry-run record carries this marker as an ``infra``-origin error. ``RunRecord``
#: has no free-form metadata field, and the alternative — an empty ``final_output``
#: with no explanation — would read downstream as a catastrophic model regression.
#: ``infra`` origin means the comparison engine surfaces it and raises no flag.
DRY_RUN_ERROR_KIND = "api_dry_run"


@dataclass(frozen=True, kw_only=True)
class ApiRequest:
    """Everything the provider needs, and nothing this repository added.

    The cache key is derived from exactly these fields (see
    :meth:`cache_payload`), so what belongs here is what changes the provider's
    answer. ``case_id``, ``app_version`` and ``timestamp`` are deliberately absent:
    the first two are labels the provider never sees, and the third would make
    every key unique and the cache useless.
    """

    model_id: str
    prompt: str
    input_payload: dict[str, Any] = field(default_factory=dict)
    allowed_tools: tuple[str, ...] = ()
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    prompt_version: str = "p-0"
    tool_version: str = "t-0"

    def cache_payload(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "prompt": self.prompt,
            "input_payload": self.input_payload,
            "allowed_tools": list(self.allowed_tools),
            "max_output_tokens": self.max_output_tokens,
            "prompt_version": self.prompt_version,
            "tool_version": self.tool_version,
        }

    def cache_key(self) -> str:
        return cache_key(self.cache_payload())


@dataclass(frozen=True, kw_only=True)
class ApiResponse:
    """What a transport returns. Usage and latency are **measurements**.

    A transport that does not know them must say so by failing, not by reporting
    zero — see the module docstring.
    """

    final_output: str = ""
    parsed_output: Any = None
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


class Transport(Protocol):
    """The seam a real provider client plugs into. None ships with this package."""

    def __call__(self, request: ApiRequest) -> ApiResponse: ...


@dataclass(frozen=True, kw_only=True)
class ApiCall:
    """One resolved call: the record, what it was estimated to cost, and how it
    was answered (``cache`` / ``dry_run`` / ``transport``)."""

    record: RunRecord
    estimate: CostEstimate
    source: str

    @property
    def cached(self) -> bool:
        return self.source == SOURCE_CACHE

    @property
    def called_provider(self) -> bool:
        return self.source == SOURCE_TRANSPORT


@dataclass(frozen=True, kw_only=True)
class ApiPlan:
    """What running a suite would cost, before running it.

    ``cost_upper_bound_usd`` counts only the cases the cache cannot answer, and
    prices each at its ``max_output_tokens`` bound. It is the number the ceiling
    is compared against, so it errs high by construction.
    """

    model_id: str
    case_count: int
    cached_case_count: int
    input_tokens_total: int
    output_tokens_bound_total: int
    cost_upper_bound_usd: float
    ceiling_usd: float
    within_ceiling: bool
    pricing_source: str
    pricing_verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "case_count": self.case_count,
            "cached_case_count": self.cached_case_count,
            "input_tokens_total": self.input_tokens_total,
            "output_tokens_bound_total": self.output_tokens_bound_total,
            "cost_upper_bound_usd": self.cost_upper_bound_usd,
            "ceiling_usd": self.ceiling_usd,
            "within_ceiling": self.within_ceiling,
            "pricing_source": self.pricing_source,
            "pricing_verified": self.pricing_verified,
        }


class ApiAdapterStub(ModelAdapter):
    """The paid-API adapter. Inert unless an :class:`ApiGuard` says otherwise.

    Constructed with no arguments it is disabled: :meth:`generate` raises
    :class:`ApiAdapterDisabled` before touching the cache, the filesystem, or
    anything else. That is how ``executor.build_adapter("api-stub")`` builds it,
    so no CLI invocation in this repository can reach a provider.
    """

    name = "api-stub"

    def __init__(
        self,
        *,
        guard: ApiGuard | None = None,
        model_id: str = "api-model",
        pricing: PriceList | None = None,
        transport: Transport | None = None,
        cache: ResponseCache | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        self.guard = guard or ApiGuard.disabled()
        self.model_id = model_id
        # Synthetic placeholder prices. Usable for estimating and comparing;
        # refused on the spend path, because they are unverified.
        self.pricing = pricing or PriceList.synthetic(model_id)
        self.transport = transport
        self.cache = cache if cache is not None else self._default_cache()
        self.max_output_tokens = max_output_tokens
        self.ledger = self.guard.ledger()

    def _default_cache(self) -> ResponseCache:
        """A disabled adapter gets a disabled cache: importing this package, or
        constructing the default adapter, must not create a directory."""
        if not self.guard.enabled:
            return ResponseCache.disabled()
        return ResponseCache(self.guard.cache_dir or default_cache_dir())

    @property
    def enabled(self) -> bool:
        return self.guard.enabled

    @property
    def spent_usd(self) -> float:
        """Cumulative *settled* spend. Cache hits and dry runs never add to it."""
        return self.ledger.spent_usd

    def reset_ledger(self) -> None:
        """Start a fresh budget. The ceiling is per-adapter, not per-process."""
        self.ledger = self.guard.ledger()

    # ----------------------------------------------------------------- #
    # Estimation. Pure arithmetic: no opt-in required, no I/O, no call.
    # ----------------------------------------------------------------- #

    def request_for(self, case: EvalCase, context: AdapterContext) -> ApiRequest:
        """The provider request one case would produce."""
        return ApiRequest(
            model_id=self.model_id,
            prompt=case.prompt,
            input_payload=dict(case.input_payload),
            allowed_tools=tuple(case.allowed_tools or ()),
            max_output_tokens=self.max_output_tokens,
            prompt_version=context.prompt_version,
            tool_version=context.tool_version,
        )

    def estimate(self, case: EvalCase) -> CostEstimate:
        """Upper-bound cost of one case. Raises :class:`UnpricedModel` if unpriced."""
        return self.pricing.estimate_case(
            self.model_id, case, max_output_tokens=self.max_output_tokens
        )

    def plan(self, cases: list[EvalCase], context: AdapterContext) -> ApiPlan:
        """Cost a whole suite without opting in, calling, or spending.

        This is the surface behind ``llmreg estimate-cost``. It requires no opt-in
        because it is a calculator: it reads prices and case text, and returns
        arithmetic. Only :meth:`generate` is gated.
        """
        input_tokens = 0
        output_bound = 0
        cost = 0.0
        cached = 0
        for case in cases:
            estimate = self.estimate(case)
            input_tokens += estimate.input_tokens
            output_bound += estimate.output_tokens
            if self.cache.enabled and self.cache.get(
                self.request_for(case, context).cache_key()
            ):
                cached += 1
                continue
            cost += estimate.cost_usd
        cost = round(cost, COST_DECIMALS)
        return ApiPlan(
            model_id=self.model_id,
            case_count=len(cases),
            cached_case_count=cached,
            input_tokens_total=input_tokens,
            output_tokens_bound_total=output_bound,
            cost_upper_bound_usd=cost,
            ceiling_usd=self.guard.max_cost_usd,
            within_ceiling=round(cost, COST_DECIMALS)
            <= round(self.guard.max_cost_usd, COST_DECIMALS),
            pricing_source=self.pricing.source,
            pricing_verified=self.pricing.verified,
        )

    # ----------------------------------------------------------------- #
    # Execution. The seven-gate ladder.
    # ----------------------------------------------------------------- #

    def generate(self, case: EvalCase, context: AdapterContext) -> RunRecord:
        return self.call(case, context).record

    def call(self, case: EvalCase, context: AdapterContext) -> ApiCall:
        """Resolve one case, refusing at the first gate that says no."""
        self.guard.check_opt_in()  # 1

        request = self.request_for(case, context)
        key = request.cache_key()
        entry = self.cache.get(key) if self.cache.enabled else None  # 2
        if entry is not None:
            return ApiCall(
                record=self._record_from_cache(case, context, entry),
                estimate=self._estimate_of_entry(entry),
                source=SOURCE_CACHE,
            )

        estimate = self.estimate(case)  # 3

        if self.guard.dry_run:  # 4
            return ApiCall(
                record=self._dry_run_record(case, context, estimate),
                estimate=estimate,
                source=SOURCE_DRY_RUN,
            )

        self._check_pricing_is_verified()  # 5
        self.ledger.check(estimate.cost_usd, case_id=case.case_id)  # 6

        if self.transport is None:  # 7
            raise TransportMissing(
                "a live API call was requested (enabled, dry_run=False) but no "
                "transport is configured. This package ships no network client "
                "on purpose: the guards are the deliverable, the call is not. "
                "See docs/API_ADAPTER.md and adapters/api_transport.py."
            )

        response = self.transport(request)
        actual = self._settle(response)
        self.ledger.charge(actual.cost_usd)
        self.cache.put(self._entry_from_response(key, context, response, actual))
        return ApiCall(
            record=self._record_from_response(case, context, response, actual),
            estimate=actual,
            source=SOURCE_TRANSPORT,
        )

    # ----------------------------------------------------------------- #
    # Internals.
    # ----------------------------------------------------------------- #

    def _check_pricing_is_verified(self) -> None:
        if self.pricing.verified or self.guard.allow_unverified_pricing:
            return
        raise UnverifiedPricing(
            f"refusing to spend against unverified prices from "
            f"{self.pricing.source}: the max-cost ceiling is arithmetic over "
            "these numbers, so if nobody has checked them the ceiling guarantees "
            'nothing. Set "verified": true and "priced_at": "<date>" in the '
            "price list once you have compared it against the provider's published "
            "pricing, or set allow_unverified_pricing to accept an unenforceable "
            "ceiling deliberately."
        )

    def _settle(self, response: ApiResponse) -> CostEstimate:
        """Price a completed call from the usage the provider reported.

        Refuses a response that reports no usage or no latency. Those are not
        zeroes; they are absences, and a trace that records them as zeroes lies to
        the comparison engine (see ``docs/INTEGRATION_CONTRACTS.md``).
        """
        if response.input_tokens <= 0 or response.output_tokens <= 0:
            raise ApiGuardError(
                "transport reported no token usage "
                f"(input={response.input_tokens}, output={response.output_tokens}); "
                "cost cannot be settled from an absent measurement, and the ledger "
                "would silently under-count the spend it is meant to cap"
            )
        if response.latency_ms <= 0.0:
            raise ApiGuardError(
                "transport reported no latency; importing an absent duration as "
                "0.0 would read as an improvement against a timed baseline and as "
                "an infinite regression when it is the baseline"
            )
        return self.pricing.estimate(
            self.model_id,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            output_tokens_bounded=False,
        )

    def _estimate_of_entry(self, entry: CacheEntry) -> CostEstimate:
        """What the cached call cost when it was made. Charged to nobody now."""
        return CostEstimate(
            model_id=entry.model_id,
            input_tokens=entry.input_tokens,
            output_tokens=entry.output_tokens,
            cost_usd=entry.cost_usd,
            output_tokens_bounded=False,
        )

    def _entry_from_response(
        self,
        key: str,
        context: AdapterContext,
        response: ApiResponse,
        actual: CostEstimate,
    ) -> CacheEntry:
        return CacheEntry(
            key=key,
            model_id=self.model_id,
            final_output=response.final_output,
            parsed_output=response.parsed_output,
            tool_calls=list(response.tool_calls),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=response.latency_ms,
            cost_usd=actual.cost_usd,
            # A recorded input, never the clock.
            stored_at=context.timestamp,
        )

    def _record_from_response(
        self,
        case: EvalCase,
        context: AdapterContext,
        response: ApiResponse,
        actual: CostEstimate,
    ) -> RunRecord:
        return build_run_record(
            case,
            context,
            AdapterResponse(
                final_output=response.final_output,
                parsed_output=response.parsed_output,
                tool_calls=list(response.tool_calls),
                input_tokens_estimate=response.input_tokens,
                output_tokens_estimate=response.output_tokens,
                latency_ms=response.latency_ms,
                cost_estimate_usd=actual.cost_usd,
            ),
        )

    def _record_from_cache(
        self, case: EvalCase, context: AdapterContext, entry: CacheEntry
    ) -> RunRecord:
        """A cache hit reproduces the original record, cost included.

        ``cost_estimate_usd`` is what the call cost when it was made — that is the
        case's cost, and the comparison engine is right to see it. The ledger is
        untouched: *this* run spent nothing.
        """
        return build_run_record(
            case,
            context,
            AdapterResponse(
                final_output=entry.final_output,
                parsed_output=entry.parsed_output,
                tool_calls=list(entry.tool_calls),
                input_tokens_estimate=entry.input_tokens,
                output_tokens_estimate=entry.output_tokens,
                latency_ms=entry.latency_ms,
                cost_estimate_usd=entry.cost_usd,
            ),
        )

    def _dry_run_record(
        self, case: EvalCase, context: AdapterContext, estimate: CostEstimate
    ) -> RunRecord:
        """An estimate, marked as one. No output, because nothing was called."""
        return build_run_record(
            case,
            context,
            AdapterResponse(
                final_output="",
                parsed_output=None,
                input_tokens_estimate=estimate.input_tokens,
                output_tokens_estimate=estimate.output_tokens,
                latency_ms=0.0,
                cost_estimate_usd=estimate.cost_usd,
                errors=[
                    ErrorRecord(
                        kind=DRY_RUN_ERROR_KIND,
                        message=(
                            "dry run: no API call was made. cost_estimate_usd is an "
                            "upper bound priced at max_output_tokens="
                            f"{self.max_output_tokens}, not a measurement; "
                            "final_output and latency_ms are empty for the same "
                            "reason"
                        ),
                        origin="infra",
                    )
                ],
            ),
        )
