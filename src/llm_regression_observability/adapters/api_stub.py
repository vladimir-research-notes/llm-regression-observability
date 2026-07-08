"""``ApiAdapterStub`` — interface only, disabled by default.

A placeholder for the paid-API adapter fully built in Milestone 11 (dry-run
mode, token/cost estimator, hard max-cost guard, response cache, explicit
opt-in). It performs **no** network or API call. By default it refuses to run;
even with ``enabled=True`` it raises :class:`NotImplementedError`, so no default
test path — and no accidental invocation — can reach a paid API.
"""

from __future__ import annotations

from ..schemas import EvalCase, RunRecord
from .base import AdapterContext, AdapterError, ModelAdapter

__all__ = ["ApiAdapterStub", "ApiAdapterDisabled"]


class ApiAdapterDisabled(AdapterError):
    """Raised when the API adapter stub is invoked without explicit opt-in."""


class ApiAdapterStub(ModelAdapter):
    """Inert interface for the future paid-API adapter (Milestone 11)."""

    name = "api-stub"

    def __init__(self, *, enabled: bool = False, model_id: str = "api-model") -> None:
        self.enabled = enabled
        self.model_id = model_id

    def generate(self, case: EvalCase, context: AdapterContext) -> RunRecord:
        if not self.enabled:
            raise ApiAdapterDisabled(
                "ApiAdapterStub is disabled by default and makes no real API "
                "call. The paid-API adapter is built in Milestone 11 (dry-run, "
                "token/cost estimator, hard max-cost guard, response cache, "
                "explicit opt-in). Pass enabled=True to acknowledge the interface, "
                "but generation still raises until Milestone 11."
            )
        raise NotImplementedError(
            "ApiAdapterStub.generate is not implemented until Milestone 11 "
            "(cost-guarded, cached, opt-in paid-API adapter)."
        )
