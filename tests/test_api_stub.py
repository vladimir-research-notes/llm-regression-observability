"""Milestone 11 — the guarded paid-API adapter.

Every test here runs offline. Nothing in this file, or in anything it imports,
performs network I/O: ``conftest.py`` disables outbound TCP for the whole suite,
and :func:`test_the_no_network_guard_is_actually_armed` proves that guard is
armed rather than assumed.

The five protections ``CLAUDE.md`` demands each get their own section, plus the
sixth thing the milestone asks for and the one that cannot be tested by calling
anything: **the default path never imports a network client**. That is checked by
parsing the package's own source, because a runtime check can only observe the
imports that happened to run.
"""

from __future__ import annotations

import ast
import json
import socket
from pathlib import Path

import pytest

from llm_regression_observability.adapters import (
    ADAPTER_NAMES,
    AdapterContext,
    AdapterError,
    ApiAdapterDisabled,
    ApiAdapterStub,
    ApiCall,
    ApiGuard,
    ApiGuardError,
    ApiRequest,
    ApiResponse,
    CacheEntry,
    CacheError,
    CostLedger,
    MaxCostExceeded,
    ResponseCache,
    TransportMissing,
    UnverifiedPricing,
)
from llm_regression_observability.adapters.api_guard import (
    ENV_ALLOW_UNVERIFIED_PRICING,
    ENV_CACHE_DIR,
    ENV_DRY_RUN,
    ENV_ENABLED,
    ENV_MAX_COST_USD,
)
from llm_regression_observability.adapters.api_stub import (
    DRY_RUN_ERROR_KIND,
    SOURCE_CACHE,
    SOURCE_DRY_RUN,
    SOURCE_TRANSPORT,
)
from llm_regression_observability.executor import ExecutorError, build_adapter
from llm_regression_observability.pricing import (
    COST_DECIMALS,
    CostModel,
    PriceList,
    PricingError,
    UnpricedModel,
    billable_input_text,
    estimate_tokens,
    load_price_list,
)
from llm_regression_observability.schemas import EvalCase, ToolCallTrace

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "src" / "llm_regression_observability"
EXAMPLE_PRICING = REPO_ROOT / "config" / "api_pricing.example.json"


# --------------------------------------------------------------------------- #
# Fixtures and doubles.
# --------------------------------------------------------------------------- #


def _case(
    case_id: str = "c1", prompt: str = "Summarize the synthetic report."
) -> EvalCase:
    return EvalCase(
        case_id=case_id,
        version="1",
        prompt=prompt,
        risk_category="benign",
    )


def _ctx(app_version: str = "candidate") -> AdapterContext:
    return AdapterContext(app_version=app_version, timestamp="2026-01-01T00:00:00Z")


def _priced(*, verified: bool = True, input_usd: float = 1.0, output_usd: float = 1.0):
    """A price list with deliberately huge prices, so one call costs real money.

    At $1 per 1k tokens a 1024-token output bound costs ~$1.02, which makes the
    ceiling arithmetic legible in the tests rather than lost in the eighth decimal.
    """
    return PriceList(
        models={"test-model": CostModel(
            input_usd_per_1k=input_usd, output_usd_per_1k=output_usd
        )},
        verified=verified,
        priced_at="2026-01-01" if verified else "",
        source="<test>",
    )


class CountingTransport:
    """A transport that records its calls. It is the only 'provider' in this suite.

    It never touches a socket. Its presence in a test is what proves a guard
    refused *before* the transport, because ``calls`` stays at zero.
    """

    def __init__(self, *, input_tokens: int = 100, output_tokens: int = 50) -> None:
        self.calls: list[ApiRequest] = []
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def __call__(self, request: ApiRequest) -> ApiResponse:
        self.calls.append(request)
        return ApiResponse(
            final_output=f"synthetic answer #{len(self.calls)}",
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            latency_ms=123.0,
        )

    @property
    def call_count(self) -> int:
        return len(self.calls)


class ExplodingCache(ResponseCache):
    """A cache that fails if it is consulted at all.

    Used to prove a gate ran *before* the cache, rather than merely that no file
    was created — a cache that is never written to looks identical to one that
    was never reached.
    """

    def __init__(self) -> None:
        super().__init__(Path("/nonexistent"))

    def get(self, key: str) -> CacheEntry | None:
        raise AssertionError("the cache was consulted")

    def put(self, entry: CacheEntry) -> Path | None:
        raise AssertionError("the cache was written to")


def _adapter(tmp_path: Path | None = None, **kwargs) -> ApiAdapterStub:
    kwargs.setdefault("model_id", "test-model")
    kwargs.setdefault("pricing", _priced())
    if tmp_path is not None:
        kwargs.setdefault("cache", ResponseCache(tmp_path / "cache"))
    else:
        kwargs.setdefault("cache", ResponseCache.disabled())
    return ApiAdapterStub(**kwargs)


# --------------------------------------------------------------------------- #
# Guard 1 — explicit opt-in. The adapter is inert until told otherwise.
# --------------------------------------------------------------------------- #


def test_the_adapter_is_disabled_by_default() -> None:
    stub = ApiAdapterStub()
    assert stub.enabled is False
    with pytest.raises(ApiAdapterDisabled, match="disabled by default"):
        stub.generate(_case(), _ctx())


def test_a_disabled_adapter_refuses_before_touching_the_cache() -> None:
    # The opt-in gate is *first*: a disabled adapter must not even stat a file.
    # ExplodingCache fails if consulted, so this asserts gate order, not just that
    # no file was written.
    stub = _adapter(transport=CountingTransport(), cache=ExplodingCache())
    with pytest.raises(ApiAdapterDisabled):
        stub.generate(_case(), _ctx())


def test_a_default_adapter_opens_no_cache_directory() -> None:
    assert ApiAdapterStub().cache.enabled is False


def test_opt_in_alone_does_not_permit_a_call() -> None:
    guard = ApiGuard(enabled=True)
    assert guard.dry_run is True
    assert guard.may_call is False


def test_disabling_dry_run_is_a_second_separate_decision() -> None:
    assert ApiGuard(enabled=True, dry_run=False).may_call is True
    assert ApiGuard(enabled=False, dry_run=False).may_call is False


def test_the_refusal_is_an_adapter_error_so_the_executor_handles_it() -> None:
    refusals = (
        ApiAdapterDisabled,
        MaxCostExceeded,
        TransportMissing,
        UnverifiedPricing,
    )
    for exc in refusals:
        assert issubclass(exc, ApiGuardError)
        assert issubclass(exc, AdapterError)


def test_build_adapter_cannot_opt_in() -> None:
    stub = build_adapter("api-stub")
    assert isinstance(stub, ApiAdapterStub)
    assert stub.enabled is False
    assert stub.transport is None
    assert "api-stub" in ADAPTER_NAMES


def test_a_run_through_the_cli_adapter_name_is_refused() -> None:
    with pytest.raises(ApiAdapterDisabled, match="disabled by default"):
        build_adapter("api-stub").generate(_case(), _ctx())


# --------------------------------------------------------------------------- #
# Guard 1b — opt-in comes from an explicit environment mapping, never os.environ.
# --------------------------------------------------------------------------- #


def test_an_empty_environment_yields_the_disabled_guard() -> None:
    assert ApiGuard.from_env({}) == ApiGuard.disabled()


def test_the_environment_can_opt_in_but_dry_run_stays_on() -> None:
    guard = ApiGuard.from_env({ENV_ENABLED: "1"})
    assert guard.enabled is True
    assert guard.dry_run is True
    assert guard.max_cost_usd == 0.0
    assert guard.allow_unverified_pricing is False


def test_the_environment_can_turn_off_dry_run_and_set_a_ceiling() -> None:
    guard = ApiGuard.from_env(
        {
            ENV_ENABLED: "yes",
            ENV_DRY_RUN: "0",
            ENV_MAX_COST_USD: "2.50",
            ENV_ALLOW_UNVERIFIED_PRICING: "true",
            ENV_CACHE_DIR: "/tmp/somewhere",
        }
    )
    assert guard.may_call is True
    assert guard.max_cost_usd == 2.50
    assert guard.allow_unverified_pricing is True
    assert guard.cache_dir == Path("/tmp/somewhere")


@pytest.mark.parametrize("value", ["maybe", "2", "on-ish", "TRUE-ish"])
def test_an_ambiguous_boolean_env_value_is_refused_not_guessed(value: str) -> None:
    # A parser cannot know whether the safe fallback is "spend" or "don't".
    with pytest.raises(ApiGuardError, match="is not a boolean"):
        ApiGuard.from_env({ENV_DRY_RUN: value})


def test_a_non_numeric_ceiling_is_refused() -> None:
    with pytest.raises(ApiGuardError, match="is not a number"):
        ApiGuard.from_env({ENV_MAX_COST_USD: "cheap"})


def test_a_negative_ceiling_is_refused() -> None:
    with pytest.raises(ApiGuardError, match="must be >= 0"):
        ApiGuard(max_cost_usd=-1.0)


def test_from_env_reads_the_mapping_it_is_given_and_no_other() -> None:
    # No monkeypatching of os.environ: the function has no access to it.
    assert ApiGuard.from_env({ENV_ENABLED: "1"}).enabled is True
    assert ApiGuard.from_env({}).enabled is False


# --------------------------------------------------------------------------- #
# Guard 2 — dry-run mode: an estimate, and no call.
# --------------------------------------------------------------------------- #


def test_dry_run_produces_an_estimate_and_calls_nothing() -> None:
    transport = CountingTransport()
    stub = _adapter(guard=ApiGuard(enabled=True), transport=transport)

    record = stub.generate(_case(), _ctx())

    assert transport.call_count == 0
    assert record.cost_estimate_usd > 0.0
    assert record.input_tokens_estimate > 0
    assert record.output_tokens_estimate == stub.max_output_tokens
    assert record.final_output == ""
    assert record.latency_ms == 0.0


def test_a_dry_run_record_says_it_is_one_and_blames_infra_not_the_app() -> None:
    stub = _adapter(guard=ApiGuard(enabled=True))
    record = stub.generate(_case(), _ctx())
    assert [e.kind for e in record.errors] == [DRY_RUN_ERROR_KIND]
    # ``infra`` origin: the comparison engine surfaces it and raises no flag, so a
    # dry run cannot be mistaken for an error-rate regression (docs/PLAN.md §6).
    assert [e.origin for e in record.errors] == ["infra"]
    assert "upper bound" in record.errors[0].message


def test_dry_run_is_the_default_even_after_opting_in() -> None:
    transport = CountingTransport()
    stub = _adapter(guard=ApiGuard(enabled=True), transport=transport)
    stub.generate(_case(), _ctx())
    assert transport.call_count == 0
    assert stub.spent_usd == 0.0


def test_dry_run_spends_nothing_from_the_ledger() -> None:
    stub = _adapter(guard=ApiGuard(enabled=True, max_cost_usd=0.0))
    # A zero ceiling does not block a dry run: nothing is being spent.
    stub.generate(_case(), _ctx())
    assert stub.spent_usd == 0.0
    assert stub.ledger.call_count == 0


def test_dry_run_records_are_byte_identical_across_runs() -> None:
    a = _adapter(guard=ApiGuard(enabled=True)).generate(_case(), _ctx())
    b = _adapter(guard=ApiGuard(enabled=True)).generate(_case(), _ctx())
    assert a.to_json() == b.to_json()


def test_dry_run_over_an_unverified_price_list_is_allowed() -> None:
    # Estimating with unchecked prices is fine. Spending against them is not.
    stub = _adapter(guard=ApiGuard(enabled=True), pricing=_priced(verified=False))
    assert stub.generate(_case(), _ctx()).cost_estimate_usd > 0.0


def test_call_reports_where_the_answer_came_from() -> None:
    stub = _adapter(guard=ApiGuard(enabled=True))
    call = stub.call(_case(), _ctx())
    assert isinstance(call, ApiCall)
    assert call.source == SOURCE_DRY_RUN
    assert call.cached is False
    assert call.called_provider is False


# --------------------------------------------------------------------------- #
# Guard 3 — the token/cost estimator, shared with the cost evaluator.
# --------------------------------------------------------------------------- #


def test_the_estimator_is_the_one_the_cost_evaluator_uses() -> None:
    from llm_regression_observability import executor, pricing
    from llm_regression_observability.comparison import base as comparison_base
    from llm_regression_observability.evaluators import cost as cost_evaluator

    # One definition, imported everywhere. Not three constants kept in sync by
    # comment, which is what this was before Milestone 11.
    assert cost_evaluator.COST_DECIMALS is pricing.COST_DECIMALS
    assert comparison_base.COST_DECIMALS is pricing.COST_DECIMALS
    assert executor.COST_DECIMALS is pricing.COST_DECIMALS
    assert executor.CostModel is pricing.CostModel


def test_the_adapter_and_the_executor_price_a_call_identically() -> None:
    from llm_regression_observability.executor import SYNTHETIC_COST_MODEL

    case = _case()
    stub = ApiAdapterStub(max_output_tokens=64)  # synthetic prices, model "api-model"
    estimate = stub.estimate(case)

    expected_input = estimate_tokens(billable_input_text(case))
    assert estimate.input_tokens == expected_input
    assert estimate.output_tokens == 64
    assert estimate.cost_usd == SYNTHETIC_COST_MODEL.estimate(expected_input, 64)


def test_a_pre_call_estimate_bounds_the_output_rather_than_predicting_it() -> None:
    stub = _adapter(guard=ApiGuard(enabled=True), max_output_tokens=2048)
    estimate = stub.estimate(_case())
    assert estimate.output_tokens == 2048
    assert estimate.output_tokens_bounded is True


def test_a_settled_estimate_is_a_measurement_not_a_bound(tmp_path: Path) -> None:
    transport = CountingTransport(input_tokens=10, output_tokens=7)
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=10.0),
        transport=transport,
    )
    call = stub.call(_case(), _ctx())
    assert call.source == SOURCE_TRANSPORT
    assert call.estimate.output_tokens == 7  # reported usage, not the 1024 bound
    assert call.estimate.output_tokens_bounded is False


def test_an_unpriced_model_is_refused_rather_than_defaulted() -> None:
    stub = ApiAdapterStub(
        guard=ApiGuard(enabled=True), model_id="nobody-priced-me", pricing=_priced()
    )
    with pytest.raises(UnpricedModel, match="has no price"):
        stub.generate(_case(), _ctx())


def test_an_unbounded_output_cannot_be_priced() -> None:
    with pytest.raises(PricingError, match="cannot be guarded"):
        _priced().estimate_case("test-model", _case(), max_output_tokens=0)


def test_the_token_estimate_is_the_naive_four_chars_per_token_rule() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100


def test_a_negative_price_is_refused() -> None:
    from llm_regression_observability.schemas import SchemaError

    with pytest.raises(SchemaError, match="finite and >= 0"):
        CostModel(input_usd_per_1k=-1.0)


def test_plan_prices_a_whole_suite_without_opting_in() -> None:
    # ``plan`` is a calculator, not the adapter's execution path. It needs no
    # opt-in because it cannot call anything.
    stub = _adapter(max_output_tokens=100)
    cases = [_case("a"), _case("b", prompt="Another synthetic prompt entirely.")]
    plan = stub.plan(cases, _ctx())

    assert stub.enabled is False
    assert plan.case_count == 2
    assert plan.cached_case_count == 0
    assert plan.output_tokens_bound_total == 200
    assert plan.cost_upper_bound_usd == round(
        sum(stub.estimate(c).cost_usd for c in cases), COST_DECIMALS
    )
    assert plan.pricing_verified is True


def test_plan_reports_whether_the_suite_fits_the_ceiling() -> None:
    cases = [_case("a"), _case("b")]
    generous = _adapter(guard=ApiGuard(enabled=True, max_cost_usd=100.0))
    stingy = _adapter(guard=ApiGuard(enabled=True, max_cost_usd=0.000001))
    assert generous.plan(cases, _ctx()).within_ceiling is True
    assert stingy.plan(cases, _ctx()).within_ceiling is False


def test_plan_discounts_the_cases_the_cache_can_already_answer(tmp_path: Path) -> None:
    transport = CountingTransport()
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=100.0),
        transport=transport,
    )
    cases = [_case("a"), _case("b", prompt="A different synthetic prompt.")]
    before = stub.plan(cases, _ctx())

    stub.generate(cases[0], _ctx())  # warm the cache for one case
    after = stub.plan(cases, _ctx())

    assert before.cached_case_count == 0
    assert after.cached_case_count == 1
    assert after.cost_upper_bound_usd < before.cost_upper_bound_usd


# --------------------------------------------------------------------------- #
# Guard 4 — the hard max-cost guard. Refuses before the call, never truncates it.
# --------------------------------------------------------------------------- #


def test_the_ceiling_blocks_an_over_budget_run_before_any_call(tmp_path: Path) -> None:
    transport = CountingTransport()
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=0.000001),
        transport=transport,
    )
    with pytest.raises(MaxCostExceeded, match="max-cost guard refused a call"):
        stub.generate(_case(), _ctx())
    assert transport.call_count == 0  # the refusal happened first
    assert stub.spent_usd == 0.0


def test_the_default_ceiling_is_zero_so_nothing_is_affordable(tmp_path: Path) -> None:
    transport = CountingTransport()
    stub = _adapter(
        tmp_path, guard=ApiGuard(enabled=True, dry_run=False), transport=transport
    )
    assert stub.ledger.ceiling_usd == 0.0
    with pytest.raises(MaxCostExceeded):
        stub.generate(_case(), _ctx())
    assert transport.call_count == 0


def test_the_ceiling_is_cumulative_across_a_suite(tmp_path: Path) -> None:
    # At $1 per 1k tokens each call is *bounded* at ~$0.106 (6 input + 100 output)
    # and *settles* at $0.15 (100 input + 50 output reported). A $0.30 ceiling
    # therefore affords exactly two: the third is refused on accumulated spend.
    transport = CountingTransport()
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=0.30),
        transport=transport,
        max_output_tokens=100,
    )
    cases = [_case(f"c{i}", prompt=f"Synthetic prompt number {i}.") for i in range(3)]

    stub.generate(cases[0], _ctx())
    stub.generate(cases[1], _ctx())
    with pytest.raises(MaxCostExceeded, match="already spent"):
        stub.generate(cases[2], _ctx())

    assert transport.call_count == 2  # the third never reached the provider
    assert stub.spent_usd > 0.0


def test_the_guard_refuses_rather_than_shrinking_the_request(tmp_path: Path) -> None:
    transport = CountingTransport()
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=0.000001),
        transport=transport,
        max_output_tokens=4096,
    )
    with pytest.raises(MaxCostExceeded, match="does not shrink the request"):
        stub.generate(_case(), _ctx())
    assert stub.max_output_tokens == 4096  # unchanged; the experiment is intact


def test_the_ledger_settles_actual_usage_not_the_bound(tmp_path: Path) -> None:
    transport = CountingTransport(input_tokens=100, output_tokens=50)
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=10.0),
        transport=transport,
        max_output_tokens=1000,
    )
    stub.generate(_case(), _ctx())
    # The ceiling was checked against the ~$1.007 bound (7 input + 1000 output at
    # $1/1k); the ledger settles the $0.15 the provider actually reported.
    assert stub.estimate(_case()).cost_usd == pytest.approx(1.007)
    assert stub.spent_usd == pytest.approx(0.15)


def test_the_ledger_arithmetic_is_exact_at_the_boundary() -> None:
    ledger = CostLedger(ceiling_usd=1.0)
    ledger.check(1.0)  # exactly the ceiling fits
    ledger.charge(1.0)
    assert ledger.remaining_usd == 0.0
    with pytest.raises(MaxCostExceeded):
        ledger.check(0.00000001)


def test_the_ledger_refuses_a_negative_charge() -> None:
    with pytest.raises(ApiGuardError, match="negative cost"):
        CostLedger(ceiling_usd=1.0).charge(-1.0)


def test_resetting_the_ledger_restores_the_budget() -> None:
    stub = _adapter(guard=ApiGuard(enabled=True, max_cost_usd=5.0))
    stub.ledger.charge(4.0)
    assert stub.spent_usd == 4.0
    stub.reset_ledger()
    assert stub.spent_usd == 0.0
    assert stub.ledger.ceiling_usd == 5.0


# --------------------------------------------------------------------------- #
# Guard 4b — an unverified price list cannot authorize spend.
# --------------------------------------------------------------------------- #


def test_spending_against_unverified_prices_is_refused(tmp_path: Path) -> None:
    transport = CountingTransport()
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=100.0),
        pricing=_priced(verified=False),
        transport=transport,
    )
    with pytest.raises(UnverifiedPricing, match="the ceiling guarantees nothing"):
        stub.generate(_case(), _ctx())
    assert transport.call_count == 0


def test_an_operator_may_accept_an_unenforceable_ceiling_deliberately(
    tmp_path: Path,
) -> None:
    transport = CountingTransport()
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(
            enabled=True,
            dry_run=False,
            max_cost_usd=100.0,
            allow_unverified_pricing=True,
        ),
        pricing=_priced(verified=False),
        transport=transport,
    )
    stub.generate(_case(), _ctx())
    assert transport.call_count == 1


def test_a_verified_price_list_must_say_when_it_was_checked() -> None:
    with pytest.raises(PricingError, match="must record priced_at"):
        PriceList(models={"m": CostModel()}, verified=True)


def test_the_committed_example_price_list_cannot_authorize_spend() -> None:
    pricing = load_price_list(EXAMPLE_PRICING)
    assert pricing.verified is False
    assert pricing.priced_at == ""
    assert "PLACEHOLDER" in pricing.note
    assert pricing.model_ids()  # it is still usable for estimating


def test_the_example_price_list_prices_are_not_zero() -> None:
    # A zero-priced example would make every estimate $0.00 and teach nothing.
    pricing = load_price_list(EXAMPLE_PRICING)
    for model_id in pricing.model_ids():
        model = pricing.for_model(model_id)
        assert model.input_usd_per_1k > 0.0
        assert model.output_usd_per_1k > 0.0


def test_a_malformed_price_list_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"models": {}}', encoding="utf-8")
    with pytest.raises(PricingError, match="non-empty JSON object"):
        load_price_list(bad)

    worse = tmp_path / "worse.json"
    worse.write_text(
        '{"models": {"m": {"input_usd_per_1k": "free"}}}', encoding="utf-8"
    )
    with pytest.raises(PricingError, match="models\\['m'\\]"):
        load_price_list(worse)


def test_an_unreadable_price_list_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PricingError, match="cannot read price list"):
        load_price_list(tmp_path / "does-not-exist.json")


# --------------------------------------------------------------------------- #
# Guard 5 — the response cache. A repeated run never re-spends.
# --------------------------------------------------------------------------- #


def test_the_cache_prevents_a_second_call(tmp_path: Path) -> None:
    transport = CountingTransport()
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=100.0),
        transport=transport,
    )
    first = stub.generate(_case(), _ctx())
    second = stub.generate(_case(), _ctx())

    assert transport.call_count == 1
    assert first.to_json() == second.to_json()


def test_a_cache_hit_consumes_no_budget(tmp_path: Path) -> None:
    # Otherwise re-running a completed suite would fail on money it already paid.
    transport = CountingTransport()
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=100.0),
        transport=transport,
    )
    stub.generate(_case(), _ctx())
    after_first = stub.spent_usd
    call = stub.call(_case(), _ctx())

    assert call.source == SOURCE_CACHE
    assert call.cached is True
    assert stub.spent_usd == after_first  # unchanged
    assert stub.ledger.call_count == 1


def test_a_cache_hit_still_reports_what_the_call_cost(tmp_path: Path) -> None:
    # The case cost that money once; the comparison engine is right to see it.
    transport = CountingTransport()
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=100.0),
        transport=transport,
    )
    first = stub.generate(_case(), _ctx())
    second = stub.generate(_case(), _ctx())
    assert second.cost_estimate_usd == first.cost_estimate_usd > 0.0


def test_a_cache_hit_survives_a_zero_ceiling(tmp_path: Path) -> None:
    transport = CountingTransport()
    warm = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=100.0),
        transport=transport,
    )
    warm.generate(_case(), _ctx())

    broke = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=0.0),
        transport=transport,
    )
    record = broke.generate(_case(), _ctx())  # no MaxCostExceeded
    assert transport.call_count == 1
    assert record.final_output == "synthetic answer #1"


def test_a_dry_run_over_a_warm_cache_returns_the_real_answer(tmp_path: Path) -> None:
    transport = CountingTransport()
    warm = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=100.0),
        transport=transport,
    )
    warm.generate(_case(), _ctx())

    dry = _adapter(tmp_path, guard=ApiGuard(enabled=True), transport=transport)
    call = dry.call(_case(), _ctx())
    assert call.source == SOURCE_CACHE
    assert call.record.final_output == "synthetic answer #1"
    assert transport.call_count == 1


def test_the_cache_key_ignores_labels_the_provider_never_sees() -> None:
    # app_version and timestamp are this repository's labels. Including them would
    # make every key unique and the cache useless.
    base = ApiRequest(model_id="m", prompt="p")
    assert base.cache_key() == ApiRequest(model_id="m", prompt="p").cache_key()

    stub = _adapter(guard=ApiGuard(enabled=True))
    case = _case()
    a = stub.request_for(case, AdapterContext(app_version="baseline", timestamp="t1"))
    b = stub.request_for(case, AdapterContext(app_version="candidate", timestamp="t2"))
    assert a.cache_key() == b.cache_key()


@pytest.mark.parametrize(
    "changed",
    [
        {"model_id": "other"},
        {"prompt": "different"},
        {"input_payload": {"k": "v"}},
        {"allowed_tools": ("search",)},
        {"max_output_tokens": 2048},
        {"prompt_version": "p-1"},
        {"tool_version": "t-1"},
    ],
)
def test_the_cache_key_changes_when_the_request_does(changed: dict) -> None:
    base = ApiRequest(model_id="m", prompt="p")
    from dataclasses import replace

    assert replace(base, **changed).cache_key() != base.cache_key()


def test_two_cases_with_an_identical_request_share_one_response(tmp_path: Path) -> None:
    transport = CountingTransport()
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=100.0),
        transport=transport,
    )
    stub.generate(_case("first", prompt="same"), _ctx())
    stub.generate(_case("second", prompt="same"), _ctx())
    assert transport.call_count == 1


def test_the_cache_stores_a_recorded_timestamp_not_the_clock(tmp_path: Path) -> None:
    transport = CountingTransport()
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=100.0),
        transport=transport,
    )
    stub.generate(_case(), _ctx())
    entries = list((tmp_path / "cache").glob("*.json"))
    assert len(entries) == 1
    stored = json.loads(entries[0].read_text(encoding="utf-8"))
    assert stored["stored_at"] == "2026-01-01T00:00:00Z"


def test_a_corrupt_cache_entry_is_refused_not_read_as_a_miss(tmp_path: Path) -> None:
    # A miss re-spends. A corrupt file is not evidence the call was never made.
    cache = ResponseCache(tmp_path)
    key = "a" * 64
    cache.path_for(key).write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(CacheError, match="unreadable"):
        cache.get(key)


def test_a_cache_entry_stored_under_the_wrong_key_is_refused(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    cache.put(CacheEntry(key="b" * 64, model_id="m"))
    (tmp_path / f"{'b' * 64}.json").rename(tmp_path / f"{'c' * 64}.json")
    with pytest.raises(CacheError, match="does not match"):
        cache.get("c" * 64)


def test_the_cache_round_trips_tool_calls(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    entry = CacheEntry(
        key="d" * 64,
        model_id="m",
        final_output="ok",
        tool_calls=[ToolCallTrace(name="search", arguments={"q": "x"})],
        input_tokens=1,
        output_tokens=1,
    )
    cache.put(entry)
    loaded = cache.get("d" * 64)
    assert loaded.tool_calls[0].name == "search"
    assert loaded.tool_calls[0].arguments == {"q": "x"}


def test_a_disabled_cache_writes_nothing(tmp_path: Path) -> None:
    cache = ResponseCache.disabled()
    assert cache.enabled is False
    assert cache.get("k") is None
    assert cache.put(CacheEntry(key="k", model_id="m")) is None


def test_the_cache_directory_is_gitignored() -> None:
    # It holds real provider responses, which CLAUDE.md forbids committing.
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".api_cache/" in gitignore


# --------------------------------------------------------------------------- #
# Guard 6 — the transport. Absent by default; refuses to invent a measurement.
# --------------------------------------------------------------------------- #


def test_a_live_call_without_a_transport_is_refused(tmp_path: Path) -> None:
    stub = _adapter(
        tmp_path, guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=100.0)
    )
    with pytest.raises(TransportMissing, match="ships no network client"):
        stub.generate(_case(), _ctx())


def test_the_ceiling_is_checked_before_the_transport_is_looked_for(
    tmp_path: Path,
) -> None:
    # Budget is policy; a missing client is configuration. Policy answers first.
    stub = _adapter(
        tmp_path, guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=0.0)
    )
    with pytest.raises(MaxCostExceeded):
        stub.generate(_case(), _ctx())


def test_pricing_verification_is_checked_before_the_ceiling(tmp_path: Path) -> None:
    # A ceiling computed from unchecked prices cannot be trusted to refuse.
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=0.0),
        pricing=_priced(verified=False),
    )
    with pytest.raises(UnverifiedPricing):
        stub.generate(_case(), _ctx())


class _SilentTransport(CountingTransport):
    def __call__(self, request: ApiRequest) -> ApiResponse:
        self.calls.append(request)
        return ApiResponse(final_output="hi", latency_ms=10.0)


class _TimelessTransport(CountingTransport):
    def __call__(self, request: ApiRequest) -> ApiResponse:
        self.calls.append(request)
        return ApiResponse(final_output="hi", input_tokens=5, output_tokens=5)


def test_a_transport_reporting_no_usage_is_refused(tmp_path: Path) -> None:
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=100.0),
        transport=_SilentTransport(),
    )
    with pytest.raises(ApiGuardError, match="no token usage"):
        stub.generate(_case(), _ctx())


def test_a_transport_reporting_no_latency_is_refused(tmp_path: Path) -> None:
    # The Milestone 10 rule: an absent measurement imported as 0.0 is a number,
    # and the comparison engine believes numbers.
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=100.0),
        transport=_TimelessTransport(),
    )
    with pytest.raises(ApiGuardError, match="no latency"):
        stub.generate(_case(), _ctx())


def test_a_refused_response_is_not_cached(tmp_path: Path) -> None:
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=100.0),
        transport=_SilentTransport(),
    )
    with pytest.raises(ApiGuardError):
        stub.generate(_case(), _ctx())
    assert not list((tmp_path / "cache").glob("*.json"))


# --------------------------------------------------------------------------- #
# The default path never imports a network client.
#
# Checked by parsing the package's source, not by watching sys.modules: a runtime
# check only sees the imports that happened to run in this process.
# --------------------------------------------------------------------------- #

#: Third-party HTTP clients, and the stdlib modules they are built on.
_NETWORK_MODULES = frozenset(
    {"socket", "ssl", "http", "urllib", "requests", "httpx", "anthropic", "openai"}
)

#: The single module allowed to reach for a client — inside a function body only.
_TRANSPORT_MODULE = PACKAGE_ROOT / "adapters" / "api_transport.py"


def _package_sources() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _module_level_imports(source: str) -> set[str]:
    """Top-level import names, skipping anything nested inside a function or class."""
    modules: set[str] = set()

    def walk(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
            elif isinstance(node, ast.If | ast.Try):
                walk(node.body)
                walk(node.orelse)
                for handler in getattr(node, "handlers", []):
                    walk(handler.body)
                walk(getattr(node, "finalbody", []))

    walk(ast.parse(source).body)
    return modules


def _all_imports(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def test_the_package_has_at_least_one_source_to_scan() -> None:
    assert len(_package_sources()) > 10  # guards against a silently-empty scan


@pytest.mark.parametrize(
    "source, caught",
    [
        ("import anthropic\n", True),
        ("import socket\n", True),
        ("from urllib import request\n", True),
        ("try:\n    import httpx\nexcept ImportError:\n    httpx = None\n", True),
        ("if True:\n    import requests\n", True),
        ("def f():\n    import anthropic\n", False),
        ("class C:\n    import socket\n", False),
    ],
)
def test_the_import_scanner_catches_what_it_claims_to(
    source: str, caught: bool
) -> None:
    # A scanner that silently matches nothing would make every test below vacuous.
    reached = bool(_module_level_imports(source) & _NETWORK_MODULES)
    assert reached is caught


def test_no_package_module_imports_a_network_client_at_import_time() -> None:
    offenders = {}
    for path in _package_sources():
        reached = _module_level_imports(path.read_text(encoding="utf-8"))
        reached &= _NETWORK_MODULES
        if reached:
            offenders[str(path.relative_to(REPO_ROOT))] = sorted(reached)
    assert not offenders, f"network client reachable at import time: {offenders}"


def test_the_transport_module_keeps_its_client_import_inside_a_function() -> None:
    source = _TRANSPORT_MODULE.read_text(encoding="utf-8")
    assert "anthropic" not in _module_level_imports(source)
    assert "anthropic" in _all_imports(source)  # the seam is real, just deferred
    assert "time" not in _module_level_imports(source)


def test_the_adapters_package_does_not_re_export_the_transport() -> None:
    # Otherwise `from ...adapters import *` would put a client one import away.
    source = (PACKAGE_ROOT / "adapters" / "__init__.py").read_text(encoding="utf-8")
    assert "api_transport" not in _all_imports(source)
    tree = ast.parse(source)
    relative = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "api_transport" not in relative


def test_no_network_client_is_loaded_after_importing_the_package() -> None:
    import sys

    import llm_regression_observability  # noqa: F401

    loaded = {"anthropic", "httpx", "requests", "openai"} & set(sys.modules)
    assert not loaded, f"a network client was imported: {sorted(loaded)}"


def test_even_importing_the_transport_module_imports_no_client() -> None:
    # The last line of defence: the client is imported inside the function body, so
    # loading the seam itself is still free. `anthropic` is not installed here, and
    # this must hold whether or not it ever is.
    import importlib
    import sys

    importlib.import_module("llm_regression_observability.adapters.api_transport")
    assert "anthropic" not in sys.modules


# --------------------------------------------------------------------------- #
# No test in this repository performs network I/O.
# --------------------------------------------------------------------------- #


def test_the_no_network_guard_is_actually_armed() -> None:
    # conftest.py disables outbound TCP for every test. If this ever passes
    # silently, the guarantee below it is worthless.
    with pytest.raises(AssertionError, match="network access attempted"):
        socket.create_connection(("example.invalid", 80))
    with pytest.raises(AssertionError, match="network access attempted"):
        socket.socket().connect(("example.invalid", 80))


def test_the_whole_guarded_path_runs_with_the_network_disabled(tmp_path: Path) -> None:
    # Every gate, exercised end to end, under a socket-blocking fixture.
    transport = CountingTransport()
    stub = _adapter(
        tmp_path,
        guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=100.0),
        transport=transport,
    )
    stub.generate(_case(), _ctx())  # transport
    stub.generate(_case(), _ctx())  # cache
    assert transport.call_count == 1


# --------------------------------------------------------------------------- #
# The executor and the CLI cannot reach a provider.
# --------------------------------------------------------------------------- #


def test_run_suite_with_the_api_adapter_is_refused() -> None:
    from llm_regression_observability.executor import RunConfig, run_suite

    config = RunConfig(
        suite_path=REPO_ROOT / "evals" / "suites" / "smoke.yaml",
        adapter="api-stub",
        run_id="api-stub-refused",
    )
    with pytest.raises(ExecutorError, match="disabled by default"):
        run_suite(config, out_root=None)


def test_estimate_cost_prices_a_suite_and_calls_nothing(capsys) -> None:
    from llm_regression_observability import cli

    code = cli.main(
        [
            "estimate-cost",
            "--suite", str(REPO_ROOT / "evals" / "suites" / "smoke.yaml"),
            "--max-cost-usd", "1000",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "api calls        : 0 (this command cannot make one)" in out
    assert "within ceiling   : yes" in out
    assert "result           : PASS" in out


def test_estimate_cost_fails_when_the_suite_exceeds_the_ceiling(capsys) -> None:
    from llm_regression_observability import cli

    code = cli.main(
        [
            "estimate-cost",
            "--suite", str(REPO_ROOT / "evals" / "suites" / "smoke.yaml"),
            "--max-cost-usd", "0",
        ]
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "within ceiling   : no" in out
    assert "result           : FAIL" in out


def test_estimate_cost_says_when_the_prices_are_unverified(capsys) -> None:
    from llm_regression_observability import cli

    cli.main(
        [
            "estimate-cost",
            "--suite", str(REPO_ROOT / "evals" / "suites" / "smoke.yaml"),
            "--pricing", str(EXAMPLE_PRICING),
            "--model", "claude-opus-4-8",
            "--max-cost-usd", "1000",
        ]
    )
    out = capsys.readouterr().out
    assert "UNVERIFIED" in out
    assert "refuses to spend against prices nobody has checked" in out


def test_estimate_cost_fails_cleanly_on_an_unpriced_model(capsys) -> None:
    from llm_regression_observability import cli

    code = cli.main(
        [
            "estimate-cost",
            "--suite", str(REPO_ROOT / "evals" / "suites" / "smoke.yaml"),
            "--pricing", str(EXAMPLE_PRICING),
            "--model", "not-a-priced-model",
        ]
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "has no price" in out
    assert "result           : FAIL" in out
