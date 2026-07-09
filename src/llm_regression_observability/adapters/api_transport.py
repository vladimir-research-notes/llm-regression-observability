"""The seam where a real provider client goes. Nothing imports this by default.

This module exists to make a boundary *visible and testable* rather than merely
asserted. Everything above it — opt-in, cache, estimator, ceiling — is guarded,
tested, and offline. Everything a network call needs is here, and here alone:

* :mod:`.api_stub` never imports this module, so ``import
  llm_regression_observability`` cannot pull a network client into the process.
* :mod:`.__init__` does not re-export it, so ``from ...adapters import *`` cannot
  either. ``tests/test_api_stub.py`` asserts both by parsing the source.
* The provider client is imported **inside** :func:`anthropic_transport`, so even
  importing *this* module does not import ``anthropic``.
* ``anthropic`` is not a dependency of this package and is not installed by the
  test environment. Calling :func:`anthropic_transport` on a default checkout
  raises :class:`ImportError` before it can raise anything else.

Two rules the transport must honor, both inherited from earlier milestones.

**It measures; it does not guess.** ``latency_ms`` is wall-clock around the
call, and ``perf_counter`` is imported inside the function for the same reason the
client is: the determinism rule forbids ambient time in any code path that feeds a
trace, and this is the one place where the ambient value *is* the measurement.
Everything the guard ladder does above this line stays a pure function of its
inputs. A live call is nondeterministic because calling a real API is
nondeterministic — not because this repository reached for a clock.

**It reports usage, or it fails.** A response with no token counts cannot settle
the ledger, and :meth:`~.api_stub.ApiAdapterStub._settle` refuses it. Reporting
``0`` to keep the call alive would silently under-count the spend the ceiling
exists to cap. The provider returns real usage; pass it through unchanged.

**Model ids and prices are configuration.** This module hard-codes neither. The
model id arrives on :class:`~.api_stub.ApiRequest` from the adapter, and the price
comes from a :class:`~..pricing.PriceList` the operator supplied and marked
``verified``. A price constant compiled into source is a price that goes stale
without anybody noticing, which is exactly what a cost ceiling must not be built
on. See ``config/api_pricing.example.json`` and ``docs/API_ADAPTER.md``.

The function below has **no test coverage and never runs in CI**, because running
it would cost money. It is the documented shape of the thing, not a load-bearing
part of the system. Treat it as a reference implementation to adapt.
"""

from __future__ import annotations

from typing import Any

from .api_stub import ApiRequest, ApiResponse, Transport

__all__ = ["anthropic_transport"]


def anthropic_transport(client: Any = None) -> Transport:
    """Build a :class:`~.api_stub.Transport` backed by the Anthropic SDK.

    ``client`` may be an already-configured ``anthropic.Anthropic`` instance. If
    omitted, one is constructed, which reads ``ANTHROPIC_API_KEY`` from the
    environment via the SDK's own resolution — this package never reads, stores,
    logs, or caches a credential.

    Returns a callable the adapter can use::

        from llm_regression_observability.adapters import ApiAdapterStub, ApiGuard
        from llm_regression_observability.adapters.api_transport import (
            anthropic_transport,
        )
        from llm_regression_observability.pricing import load_price_list

        adapter = ApiAdapterStub(
            guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=0.50),
            model_id="claude-sonnet-5",
            pricing=load_price_list("config/api_pricing.json"),  # verified
            transport=anthropic_transport(),
        )

    Raises :class:`ImportError` if the SDK is absent, which is the default state
    of this repository's environment.
    """

    def _call(request: ApiRequest) -> ApiResponse:
        # Imported here, not at module scope: the default path must never load a
        # network client, and the determinism rule confines ambient time to the
        # one place where the ambient value is the measurement being taken.
        from time import perf_counter

        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - never installed in CI
            raise ImportError(
                "the optional Anthropic SDK is not installed. This package's core "
                "is dependency-free by design (CLAUDE.md); install `anthropic` "
                "yourself if you have opted in to paid-API mode."
            ) from exc

        sdk = client or anthropic.Anthropic()
        started = perf_counter()
        message = sdk.messages.create(
            model=request.model_id,
            max_tokens=request.max_output_tokens,
            messages=[{"role": "user", "content": request.prompt}],
        )
        latency_ms = (perf_counter() - started) * 1000.0

        text = "".join(
            block.text
            for block in message.content
            if getattr(block, "type", "") == "text"
        )
        return ApiResponse(
            final_output=text,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            latency_ms=latency_ms,
        )

    return _call
