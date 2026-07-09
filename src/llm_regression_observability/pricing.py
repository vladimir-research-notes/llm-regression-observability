"""Token and cost estimation — the one estimator every priced path shares.

Before Milestone 11 the estimator was scattered: :func:`estimate_tokens` lived in
``adapters/base.py``, ``CostModel`` and ``COST_DECIMALS`` in ``executor.py``, and
``COST_DECIMALS`` was *restated* in ``evaluators/cost.py`` and
``comparison/base.py`` with a comment asking the reader to keep them in sync. That
was survivable while the only consumer was a synthetic price list. It stops being
survivable the moment an estimate is what stands between a run and a bill: a
guard that rounds cost to eight decimals while the evaluator rounds to six is a
guard that can be argued with. So the estimator lives here, once, and the
executor, the cost evaluator, the comparison engine and the guarded API adapter
(``adapters/api_stub.py``) all import it.

Three things are worth stating plainly, because each is a place where an
estimator is tempted to overclaim.

**The token estimate is crude and says so.** ``tokens ≈ chars / 4`` is not a
tokenizer. It is deliberately naive, dependency-free, and identical on both sides
of a comparison, which is the only property a *delta* needs. It is not the
property a *bill* needs. Nothing here should be read as predicting an invoice;
see ``docs/LIMITATIONS.md``.

**A pre-call estimate bounds the cost, it does not predict it.** Input tokens are
knowable before a call; output tokens are not. :func:`estimate_case` therefore
prices the output at the caller's ``max_output_tokens`` — the worst case the API
is permitted to produce. A budget guard that priced the *average* output would be
a guard that lets the tail through. :attr:`CostEstimate.output_tokens_bounded`
records which of the two a number is, so a report can never present an upper
bound as a measurement.

**Prices are configuration, never facts baked into source.** Vendor pricing
changes; a constant in a Python file does not. :class:`PriceList` is loaded from
JSON and carries its own provenance: ``verified`` (has a human checked these
against the vendor's published prices?) and ``priced_at`` (when?). An unverified
price list is perfectly usable for estimating and comparing. It is *refused* on
any path that could spend money — see :class:`~.adapters.api_guard.ApiGuard` —
because a ceiling enforced against made-up prices is not a ceiling.

The default :data:`SYNTHETIC_COST_MODEL` is a placeholder in the right order of
magnitude for a hosted model. It is not any vendor's pricing, and nothing in this
repository calls a paid API.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schemas import EvalCase, Schema, SchemaError

__all__ = [
    "COST_DECIMALS",
    "CHARS_PER_TOKEN",
    "PricingError",
    "UnpricedModel",
    "estimate_tokens",
    "billable_input_text",
    "CostModel",
    "SYNTHETIC_COST_MODEL",
    "CostEstimate",
    "PriceList",
    "load_price_list",
]

#: Cost is rounded to this many decimals everywhere — the executor's artifacts,
#: the cost evaluator's metric, the comparison engine's delta, and the API
#: adapter's ledger. Sub-cent per-case costs would vanish under the six-decimal
#: rounding the other metrics use, and a ledger that disagrees with the artifact
#: it wrote is a ledger nobody can audit.
COST_DECIMALS = 8

#: The naive tokenizer's only constant. See the module docstring: this is a
#: stand-in for a real tokenizer, not an approximation of one.
CHARS_PER_TOKEN = 4


class PricingError(ValueError):
    """Raised when a price list is malformed, or a price cannot be applied."""


class UnpricedModel(PricingError):
    """Raised when a model id has no entry in the active :class:`PriceList`.

    Refusing is the point. Falling back to a default price would produce a number
    that looks like a cost, feeds a ceiling, and means nothing.
    """


def estimate_tokens(text: str) -> int:
    """A deterministic, naive token estimate (~4 characters per token).

    Deliberately crude: it exists so the pipeline can populate the
    ``*_tokens_estimate`` fields without a tokenizer dependency, and so both
    sides of a comparison are counted the same way. It does not match any real
    tokenizer and must not be used to predict a bill.
    """
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def billable_input_text(case: EvalCase) -> str:
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


@dataclass(kw_only=True)
class CostModel(Schema):
    """A linear price per 1,000 tokens.

    The default prices are **synthetic placeholders** chosen to be in the right
    order of magnitude for a hosted model. They are not any vendor's real
    pricing, and nothing in this repository calls a paid API. They exist so that
    ``cost_estimate_usd`` is a defined, reproducible number rather than an absent
    one. A real adapter supplies its own via :class:`PriceList`.
    """

    input_usd_per_1k: float = 0.003
    output_usd_per_1k: float = 0.015

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in ("input_usd_per_1k", "output_usd_per_1k"):
            price = getattr(self, name)
            if not math.isfinite(price) or price < 0.0:
                raise SchemaError(
                    f"CostModel.{name}: price must be finite and >= 0, got {price!r}"
                )

    def estimate(self, input_tokens: int, output_tokens: int) -> float:
        """Cost of a call, rounded to :data:`COST_DECIMALS` for byte-stability."""
        usd = (
            input_tokens / 1000.0 * self.input_usd_per_1k
            + output_tokens / 1000.0 * self.output_usd_per_1k
        )
        return round(usd, COST_DECIMALS)


#: The default price list entry. Synthetic; see :class:`CostModel`.
SYNTHETIC_COST_MODEL = CostModel()


@dataclass(frozen=True, kw_only=True)
class CostEstimate:
    """What one call is expected to cost, and how much of that is known.

    ``output_tokens_bounded`` is the honest bit. Before a call, ``output_tokens``
    is the caller's ``max_output_tokens`` — an upper bound the API may not exceed,
    not a prediction. After a call it is the usage the provider reported, and the
    flag is ``False``. A ceiling is enforced against the bound; a trace records
    the measurement.
    """

    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    output_tokens_bounded: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "output_tokens_bounded": self.output_tokens_bounded,
        }


@dataclass(frozen=True, kw_only=True)
class PriceList:
    """Model id → :class:`CostModel`, plus the provenance of those numbers.

    ``verified`` means *a human checked these against the vendor's published
    prices on* ``priced_at``. It is ``False`` by default and for every price list
    committed to this repository, because this repository cannot check a price:
    it has no network, and a number copied into source on the day it was written
    is a number that silently rots. Estimating and comparing with an unverified
    list is fine — both sides of a delta use the same prices, so the delta is
    sound regardless. Spending against one is not, and the API guard refuses it.
    """

    models: dict[str, CostModel]
    verified: bool = False
    priced_at: str = ""
    note: str = ""
    source: str = "<inline>"

    def __post_init__(self) -> None:
        if not self.models:
            raise PricingError("price list is empty: no model has a price")
        for model_id, model in self.models.items():
            if not isinstance(model_id, str) or not model_id:
                raise PricingError(
                    f"price list model id must be a non-empty string: {model_id!r}"
                )
            if not isinstance(model, CostModel):
                raise PricingError(f"price list entry {model_id!r} is not a CostModel")
        if self.verified and not self.priced_at:
            raise PricingError(
                "a verified price list must record priced_at (the date the prices "
                "were checked against the vendor's published pricing); a 'verified' "
                "claim with no date cannot go stale, which is the problem"
            )

    @classmethod
    def synthetic(cls, model_id: str = "api-model") -> PriceList:
        """The placeholder price list: one model, synthetic prices, unverified."""
        return cls(
            models={model_id: SYNTHETIC_COST_MODEL},
            verified=False,
            note="synthetic placeholder prices; not any vendor's pricing",
            source="<synthetic>",
        )

    def model_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.models))

    def for_model(self, model_id: str) -> CostModel:
        """The price of ``model_id``, or :class:`UnpricedModel`."""
        try:
            return self.models[model_id]
        except KeyError:
            known = ", ".join(self.model_ids()) or "(none)"
            raise UnpricedModel(
                f"model {model_id!r} has no price in {self.source} "
                f"(priced models: {known}). Add it rather than defaulting: a "
                "made-up price produces a number that looks like a cost."
            ) from None

    def estimate(
        self,
        model_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        output_tokens_bounded: bool = True,
    ) -> CostEstimate:
        """Price a call. Raises :class:`UnpricedModel` if the model is unpriced."""
        model = self.for_model(model_id)
        return CostEstimate(
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=model.estimate(input_tokens, output_tokens),
            output_tokens_bounded=output_tokens_bounded,
        )

    def estimate_case(
        self, model_id: str, case: EvalCase, *, max_output_tokens: int
    ) -> CostEstimate:
        """Price one case *before* it runs: real input tokens, bounded output."""
        if max_output_tokens <= 0:
            raise PricingError(
                f"max_output_tokens must be > 0 to bound a cost, got "
                f"{max_output_tokens!r}; an unbounded call cannot be guarded"
            )
        return self.estimate(
            model_id,
            input_tokens=estimate_tokens(billable_input_text(case)),
            output_tokens=max_output_tokens,
            output_tokens_bounded=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "priced_at": self.priced_at,
            "note": self.note,
            "models": {
                model_id: model.to_dict()
                for model_id, model in sorted(self.models.items())
            },
        }


def _require(data: Any, key: str, kind: type, *, source: str, default: Any) -> Any:
    value = data.get(key, default)
    if not isinstance(value, kind):
        raise PricingError(f"{source}: '{key}' must be {kind.__name__}, got {value!r}")
    return value


def load_price_list(path: Path | str) -> PriceList:
    """Load a price list from JSON.

    ::

        {
          "verified": false,
          "priced_at": "",
          "note": "PLACEHOLDER prices - replace with current published pricing",
          "models": {
            "claude-opus-4-8": {"input_usd_per_1k": 0.0, "output_usd_per_1k": 0.0}
          }
        }

    Model ids and prices are **configuration**. This loader validates their shape
    and refuses a malformed file; it cannot and does not check that a price is
    correct. That is what ``verified`` / ``priced_at`` are for.
    """
    path = Path(path)
    source = str(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PricingError(f"{source}: cannot read price list ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise PricingError(f"{source}: not valid JSON ({exc})") from exc

    if not isinstance(raw, dict):
        raise PricingError(f"{source}: price list must be a JSON object")

    models_raw = raw.get("models")
    if not isinstance(models_raw, dict) or not models_raw:
        raise PricingError(f"{source}: 'models' must be a non-empty JSON object")

    models: dict[str, CostModel] = {}
    for model_id, entry in models_raw.items():
        if not isinstance(entry, dict):
            raise PricingError(f"{source}: models[{model_id!r}] must be an object")
        try:
            models[model_id] = CostModel.from_dict(entry)
        except SchemaError as exc:
            raise PricingError(f"{source}: models[{model_id!r}]: {exc}") from exc

    return PriceList(
        models=models,
        verified=_require(raw, "verified", bool, source=source, default=False),
        priced_at=_require(raw, "priced_at", str, source=source, default=""),
        note=_require(raw, "note", str, source=source, default=""),
        source=source,
    )
