"""``MockModelAdapter`` — fixed, deterministic outputs for tests.

The simplest adapter: it returns a caller-supplied response per ``case_id`` (or a
fixed synthetic default derived from the case). It never reads the fixtures, so
it is the adapter to use when a test needs a run record but does not care about
its content. Token/latency/cost are populated with the naive estimator from
:mod:`.base` so the fields are non-empty and deterministic.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..schemas import EvalCase, RunRecord
from .base import (
    AdapterContext,
    AdapterResponse,
    ModelAdapter,
    build_run_record,
    estimate_tokens,
)

__all__ = ["MockModelAdapter"]


class MockModelAdapter(ModelAdapter):
    """Return a fixed :class:`AdapterResponse` per case.

    ``responses`` maps ``case_id`` to a response to return verbatim (copied, so
    the caller's object is never mutated). Any case without an entry gets a
    deterministic default: a short synthetic string, estimated tokens, and the
    configured latency/cost.
    """

    name = "mock"

    def __init__(
        self,
        *,
        responses: Mapping[str, AdapterResponse] | None = None,
        latency_ms: float = 100.0,
        cost_per_1k_tokens: float = 0.001,
    ) -> None:
        self._responses: dict[str, AdapterResponse] = dict(responses or {})
        self._latency_ms = float(latency_ms)
        self._cost_per_1k = float(cost_per_1k_tokens)

    def _default_response(self, case: EvalCase) -> AdapterResponse:
        final = f"mock output for {case.case_id}"
        input_tokens = estimate_tokens(case.prompt)
        output_tokens = estimate_tokens(final)
        cost = round((input_tokens + output_tokens) / 1000.0 * self._cost_per_1k, 8)
        return AdapterResponse(
            final_output=final,
            input_tokens_estimate=input_tokens,
            output_tokens_estimate=output_tokens,
            latency_ms=self._latency_ms,
            cost_estimate_usd=cost,
        )

    def generate(self, case: EvalCase, context: AdapterContext) -> RunRecord:
        preset = self._responses.get(case.case_id)
        response = preset.copy() if preset is not None else self._default_response(case)
        return build_run_record(case, context, response)
