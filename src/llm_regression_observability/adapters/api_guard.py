"""The guards that stand between a run and a bill.

``CLAUDE.md`` requires five protections before any paid-API mode may exist:
explicit opt-in, dry-run by default, a token/cost estimator, a hard max-cost
guard, and a response cache. Four of them are policy and live here; the
estimator lives in :mod:`..pricing` because the cost evaluator shares it, and the
cache lives in :mod:`.api_cache`. :class:`~.api_stub.ApiAdapterStub` composes all
four in a fixed order.

**Opt-in is a value, not an ambient read.** :meth:`ApiGuard.from_env` takes the
environment as an argument. Library code that reaches for ``os.environ`` makes
its own behavior depend on a variable no artifact records — the same objection
the determinism rule makes to ``time.time()``. The CLI may read the process
environment at its boundary and hand the mapping in; nothing below the boundary
does.

**Dry-run is the default even after opting in.** ``LLMREG_API_ENABLED=1`` alone
buys an estimate, not a call. Turning off dry-run is a second, separate decision
(``LLMREG_API_DRY_RUN=0``), because "I want to use the API adapter" and "I want
to spend money right now" are different intentions and a single flag cannot tell
them apart.

**The ceiling defaults to zero.** An opted-in adapter with no configured budget
can spend nothing at all: ``max_cost_usd=0.0`` refuses any call with a nonzero
estimate. The failure mode of a default ceiling — "I meant to set it and forgot"
— must cost nothing, so the unset value is the strictest one, not the loosest.

**The ceiling is checked against an upper bound, before the call.**
:class:`CostLedger` refuses a call whose *worst-case* estimated cost would push
cumulative spend past the ceiling (see
:attr:`~..pricing.CostEstimate.output_tokens_bounded`). It refuses rather than
truncates: silently lowering ``max_tokens`` to fit a budget changes the
experiment, and an evaluation harness that quietly changes the experiment to stay
in budget is worse than one that stops. Actual spend is settled from the
provider's reported usage after the call, so the ledger tracks what was billed
while the guard reasons about what could be.

**An unverified price list cannot authorize spend.** A ceiling is arithmetic over
prices; if the prices are placeholders the ceiling is theater. Prices carry
``verified`` / ``priced_at`` provenance (:class:`~..pricing.PriceList`), and the
spend path refuses an unverified list unless the operator sets
``allow_unverified_pricing`` — an explicit statement that they know the ceiling
is being enforced against numbers nobody checked.

No module here imports a network client, a clock, or an RNG.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..pricing import COST_DECIMALS
from .base import AdapterError

__all__ = [
    "ApiGuardError",
    "ApiAdapterDisabled",
    "MaxCostExceeded",
    "TransportMissing",
    "UnverifiedPricing",
    "ENV_ENABLED",
    "ENV_DRY_RUN",
    "ENV_MAX_COST_USD",
    "ENV_CACHE_DIR",
    "ENV_ALLOW_UNVERIFIED_PRICING",
    "ApiGuard",
    "CostLedger",
]


class ApiGuardError(AdapterError):
    """Base class for every refusal on the paid-API path.

    Subclasses :class:`~.base.AdapterError` so the executor's existing
    adapter-failure handling catches a refused API call the same way it catches a
    missing fixture — a guard that needs bespoke plumbing to be honored is a
    guard that will eventually be bypassed.
    """


class ApiAdapterDisabled(ApiGuardError):
    """Raised when the API adapter is invoked without explicit opt-in."""


class MaxCostExceeded(ApiGuardError):
    """Raised *before* a call whose estimated cost would breach the ceiling."""


class TransportMissing(ApiGuardError):
    """Raised when a live call is requested but no transport was supplied.

    The default build ships no network client, so this is the ordinary outcome of
    disabling dry-run without wiring one up. See :mod:`.api_transport`.
    """


class UnverifiedPricing(ApiGuardError):
    """Raised when a live call is priced by an unverified :class:`PriceList`."""


#: Environment keys. Read at the CLI boundary and passed to :meth:`ApiGuard.from_env`.
ENV_ENABLED = "LLMREG_API_ENABLED"
ENV_DRY_RUN = "LLMREG_API_DRY_RUN"
ENV_MAX_COST_USD = "LLMREG_API_MAX_COST_USD"
ENV_CACHE_DIR = "LLMREG_API_CACHE_DIR"
ENV_ALLOW_UNVERIFIED_PRICING = "LLMREG_API_ALLOW_UNVERIFIED_PRICING"

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSEY = frozenset({"0", "false", "no", "off", ""})


def _flag(env: Mapping[str, str], key: str, *, default: bool) -> bool:
    """Parse a boolean environment flag, refusing anything ambiguous.

    An unrecognized value raises rather than falling back to ``default``. A
    ``LLMREG_API_DRY_RUN=maybe`` that silently means "dry run" is fine; one that
    silently means "spend" is not, and a parser cannot tell which key it is
    reading. Refusing both is the only rule that is safe in every position.
    """
    raw = env.get(key)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSEY:
        return False
    raise ApiGuardError(
        f"{key}={raw!r} is not a boolean; use one of "
        f"{sorted(_TRUTHY)} or {sorted(_FALSEY - {''})}"
    )


@dataclass(frozen=True, kw_only=True)
class ApiGuard:
    """The policy for one API adapter. Inert unless every field says otherwise.

    ``enabled`` is the opt-in. ``dry_run`` (default ``True``) means "estimate,
    never call". ``max_cost_usd`` (default ``0.0``) is the hard ceiling on
    cumulative estimated spend for the adapter's lifetime.
    ``allow_unverified_pricing`` (default ``False``) is required to spend against
    a price list no human has checked. ``cache_dir`` overrides where responses are
    cached; ``None`` lets the adapter choose its gitignored default.
    """

    enabled: bool = False
    dry_run: bool = True
    max_cost_usd: float = 0.0
    allow_unverified_pricing: bool = False
    cache_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.max_cost_usd < 0.0:
            raise ApiGuardError(f"max_cost_usd must be >= 0, got {self.max_cost_usd!r}")

    @classmethod
    def disabled(cls) -> ApiGuard:
        """The default: inert. No opt-in, no budget, dry-run anyway."""
        return cls()

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ApiGuard:
        """Build a guard from an explicitly-supplied environment mapping.

        ``env`` is a parameter, never ``os.environ`` read from inside. Passing
        ``{}`` yields :meth:`disabled`, which is what every test and every default
        code path does.
        """
        cache_dir = env.get(ENV_CACHE_DIR) or None
        raw_ceiling = env.get(ENV_MAX_COST_USD)
        try:
            ceiling = float(raw_ceiling) if raw_ceiling else 0.0
        except ValueError as exc:
            raise ApiGuardError(
                f"{ENV_MAX_COST_USD}={raw_ceiling!r} is not a number"
            ) from exc
        return cls(
            enabled=_flag(env, ENV_ENABLED, default=False),
            dry_run=_flag(env, ENV_DRY_RUN, default=True),
            max_cost_usd=ceiling,
            allow_unverified_pricing=_flag(
                env, ENV_ALLOW_UNVERIFIED_PRICING, default=False
            ),
            cache_dir=Path(cache_dir) if cache_dir else None,
        )

    @property
    def may_call(self) -> bool:
        """Whether this policy permits a network call at all (before budget)."""
        return self.enabled and not self.dry_run

    def check_opt_in(self) -> None:
        """Refuse unless the operator opted in. The first gate, always."""
        if not self.enabled:
            raise ApiAdapterDisabled(
                "the API adapter is disabled by default and makes no real API "
                f"call. Opt in explicitly (ApiGuard(enabled=True), or "
                f"{ENV_ENABLED}=1) to estimate; dry-run stays on until you also "
                f"set {ENV_DRY_RUN}=0, and no call is possible until a transport "
                "and a verified price list are supplied."
            )

    def ledger(self) -> CostLedger:
        """A fresh ledger enforcing this guard's ceiling."""
        return CostLedger(ceiling_usd=self.max_cost_usd)


@dataclass
class CostLedger:
    """Cumulative spend against a hard ceiling, in USD.

    ``check`` is called *before* a call with the worst-case estimate and raises
    :class:`MaxCostExceeded` if it would not fit. ``charge`` is called *after*
    with the provider's reported usage. Cache hits call neither: a response served
    from disk cost nothing this run, so it must not consume budget — otherwise
    re-running a completed suite fails on the budget it already paid.

    Comparisons round to :data:`~..pricing.COST_DECIMALS` first, so float
    representation error cannot make a call that exactly fits appear to overrun.
    """

    ceiling_usd: float
    spent_usd: float = 0.0
    call_count: int = 0

    @property
    def remaining_usd(self) -> float:
        return round(max(0.0, self.ceiling_usd - self.spent_usd), COST_DECIMALS)

    def would_fit(self, estimated_usd: float) -> bool:
        projected = round(self.spent_usd + estimated_usd, COST_DECIMALS)
        return projected <= round(self.ceiling_usd, COST_DECIMALS)

    def check(self, estimated_usd: float, *, case_id: str = "") -> None:
        """Refuse a call that would breach the ceiling. Never truncates it."""
        if self.would_fit(estimated_usd):
            return
        where = f" for case {case_id!r}" if case_id else ""
        projected = round(self.spent_usd + estimated_usd, COST_DECIMALS)
        raise MaxCostExceeded(
            f"max-cost guard refused a call{where}: estimated ${estimated_usd:.6f} "
            f"on top of ${self.spent_usd:.6f} already spent would reach "
            f"${projected:.6f}, past the ${self.ceiling_usd:.6f} ceiling. "
            "Raise max_cost_usd deliberately, or run in dry-run mode. The guard "
            "does not shrink the request to fit: that would change the experiment."
        )

    def charge(self, actual_usd: float) -> None:
        """Settle a completed call against the ledger."""
        if actual_usd < 0.0:
            raise ApiGuardError(f"cannot charge a negative cost: {actual_usd!r}")
        self.spent_usd = round(self.spent_usd + actual_usd, COST_DECIMALS)
        self.call_count += 1
