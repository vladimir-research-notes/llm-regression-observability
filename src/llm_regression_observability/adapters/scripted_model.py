"""``ScriptedRegressionAdapter`` — deterministically inject known regressions.

This is the ground-truth generator the detector is later measured against
(Milestone 6): it takes a *clean baseline* response and applies exactly one
mechanical defect, so the candidate differs from the baseline only along the
injected dimension. It is the candidate generator in the offline smoke pipeline
(``--adapter scripted-regression --app-version candidate``; ``docs/PLAN.md`` §9).

Two layers:

* **Injectors** — small, pure transforms (``AdapterResponse`` in, mutated copy
  out), one per named defect ``kind``. Each is unit-tested to do exactly what it
  claims and nothing else.
* **Injection plan** — a ``case_id → Injection`` map. :meth:`from_fixtures` builds
  a plan from the Milestone 2 ``CaseExpectation`` ground truth: each non-control
  case gets a representative defect of its ``expected_category``; an
  ``infra``-attributed case gets an ``infra``-origin error (surfaced, not a
  regression — research question 9); a true control gets no injection.

Nothing here calls the clock or an RNG, so the same baseline + plan always yields
the same candidate.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..schemas import (
    ErrorRecord,
    EvalCase,
    RetrievalTrace,
    RunRecord,
    ToolCallTrace,
)
from .base import (
    AdapterContext,
    AdapterError,
    AdapterResponse,
    ModelAdapter,
    build_run_record,
    response_from_record,
)

__all__ = [
    "ScriptedRegressionAdapter",
    "Injection",
    "INJECTION_KINDS",
    # Injection-kind constants:
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

# --------------------------------------------------------------------------- #
# Injection kinds (closed vocabulary), grouped by the regression category they
# produce. See docs/REGRESSION_DETECTION.md.
# --------------------------------------------------------------------------- #

# structured_output_regression
INVALID_JSON = "invalid_json"
DROP_JSON_FIELD = "drop_json_field"
SET_JSON_FIELD = "set_json_field"
# tool_call_regression
DROP_TOOL = "drop_tool"
WRONG_TOOL = "wrong_tool"
ADD_TOOL = "add_tool"
ARG_DRIFT = "arg_drift"
SWAP_TOOL_ORDER = "swap_tool_order"
# evidence_grounding_regression
UNSUPPORTED_CLAIM = "unsupported_claim"
DROP_SOURCE = "drop_source"
# refusal_behavior_shift
FLIP_REFUSAL = "flip_refusal"
SET_PARSED_FIELD = "set_parsed_field"
# latency_regression / cost_regression
ADD_LATENCY = "add_latency"
ADD_COST = "add_cost"
# error_rate_regression
ADD_ERROR = "add_error"


def _reserialize(obj: Any) -> str:
    """Serialize a parsed object back to a compact JSON string (order preserved)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _reindex(calls: list[ToolCallTrace]) -> None:
    """Renumber ``order_index`` to match list position (0, 1, 2, …)."""
    for i, call in enumerate(calls):
        if call.order_index != i:
            calls[i] = ToolCallTrace(
                name=call.name,
                arguments=dict(call.arguments),
                order_index=i,
                status=call.status,
                error=call.error,
            )


# --------------------------------------------------------------------------- #
# Injectors. Each mutates the (already-copied) response in place.
# --------------------------------------------------------------------------- #


def _inject_invalid_json(resp: AdapterResponse, params: dict) -> None:
    text = resp.final_output
    stripped = text.rstrip()
    if stripped.endswith("}"):
        resp.final_output = stripped[:-1]  # drop the closing brace -> unparseable
    else:
        resp.final_output = text + "{"  # ensure it no longer parses as JSON
    resp.parsed_output = None


def _inject_drop_json_field(resp: AdapterResponse, params: dict) -> None:
    field_name = params["field"]
    obj = resp.parsed_output
    if isinstance(obj, dict) and field_name in obj:
        new = {k: v for k, v in obj.items() if k != field_name}
        resp.parsed_output = new
        resp.final_output = _reserialize(new)


def _inject_set_json_field(resp: AdapterResponse, params: dict) -> None:
    field_name = params["field"]
    obj = resp.parsed_output
    if isinstance(obj, dict):
        new = dict(obj)
        new[field_name] = params["value"]
        resp.parsed_output = new
        resp.final_output = _reserialize(new)


def _inject_drop_tool(resp: AdapterResponse, params: dict) -> None:
    calls = resp.tool_calls
    if not calls:
        return
    name = params.get("name")
    if name is not None:
        idx = next((i for i, c in enumerate(calls) if c.name == name), None)
    else:
        idx = len(calls) - 1
    if idx is not None:
        del calls[idx]
        _reindex(calls)


def _inject_wrong_tool(resp: AdapterResponse, params: dict) -> None:
    calls = resp.tool_calls
    if not calls:
        return
    index = params.get("index")
    if index is None:
        index = len(calls) - 1
    if 0 <= index < len(calls):
        calls[index] = ToolCallTrace(
            name=params["name"],
            arguments=dict(params.get("arguments", {})),
            order_index=index,
            status=params.get("status", "ok"),
        )


def _inject_add_tool(resp: AdapterResponse, params: dict) -> None:
    resp.tool_calls.append(
        ToolCallTrace(
            name=params["name"],
            arguments=dict(params.get("arguments", {})),
            order_index=len(resp.tool_calls),
            status=params.get("status", "ok"),
        )
    )


def _inject_arg_drift(resp: AdapterResponse, params: dict) -> None:
    calls = resp.tool_calls
    index = params.get("index", 0)
    if 0 <= index < len(calls):
        call = calls[index]
        calls[index] = ToolCallTrace(
            name=call.name,
            arguments=dict(params["arguments"]),
            order_index=call.order_index,
            status=call.status,
            error=call.error,
        )


def _inject_swap_tool_order(resp: AdapterResponse, params: dict) -> None:
    calls = resp.tool_calls
    i = params.get("i", 0)
    j = params.get("j", 1)
    if 0 <= i < len(calls) and 0 <= j < len(calls) and i != j:
        calls[i], calls[j] = calls[j], calls[i]
        _reindex(calls)


def _inject_unsupported_claim(resp: AdapterResponse, params: dict) -> None:
    claim = params.get("claim", "unsupported synthetic claim")
    resp.retrieval_sources.append(
        RetrievalTrace(
            source_id=params.get("source_id", "doc-a"),
            snippet=params.get("snippet", "(no matching text)"),
            claim=claim,
            supported=False,
        )
    )
    append_text = params.get("append_text")
    if append_text is None:
        append_text = f" {claim}."
    resp.final_output = resp.final_output + append_text


def _inject_drop_source(resp: AdapterResponse, params: dict) -> None:
    sources = resp.retrieval_sources
    if not sources:
        return
    source_id = params.get("source_id")
    if source_id is not None:
        idx = next(
            (i for i, s in enumerate(sources) if s.source_id == source_id), None
        )
    else:
        idx = len(sources) - 1
    if idx is not None:
        del sources[idx]


def _inject_flip_refusal(resp: AdapterResponse, params: dict) -> None:
    obj = resp.parsed_output if isinstance(resp.parsed_output, dict) else {}
    new = dict(obj)
    current = bool(new.get("refused", False))
    refused = params.get("refused", not current)
    new["refused"] = refused
    resp.parsed_output = new
    if "final_output" in params:
        resp.final_output = params["final_output"]
    elif refused:
        resp.final_output = "I'm sorry, but I can't help with that request."
    else:
        resp.final_output = "Here is the requested synthetic answer."


def _inject_set_parsed_field(resp: AdapterResponse, params: dict) -> None:
    obj = resp.parsed_output if isinstance(resp.parsed_output, dict) else {}
    new = dict(obj)
    new[params["field"]] = params["value"]
    resp.parsed_output = new
    if "final_output" in params:
        resp.final_output = params["final_output"]


def _inject_add_latency(resp: AdapterResponse, params: dict) -> None:
    if "delta_ms" in params:
        resp.latency_ms = resp.latency_ms + float(params["delta_ms"])
    elif "factor" in params:
        resp.latency_ms = resp.latency_ms * float(params["factor"])
    else:
        resp.latency_ms = resp.latency_ms + 250.0


def _inject_add_cost(resp: AdapterResponse, params: dict) -> None:
    if "delta_usd" in params:
        resp.cost_estimate_usd = resp.cost_estimate_usd + float(params["delta_usd"])
    elif "factor" in params:
        resp.cost_estimate_usd = resp.cost_estimate_usd * float(params["factor"])
    else:
        resp.cost_estimate_usd = resp.cost_estimate_usd + 0.004


def _inject_add_error(resp: AdapterResponse, params: dict) -> None:
    message = params.get("message", "synthetic injected error")
    resp.errors.append(
        ErrorRecord(
            kind=params.get("kind", "tool_error"),
            message=message,
            origin=params.get("origin", "app"),
        )
    )
    fail_index = params.get("fail_tool_index")
    if fail_index is not None and 0 <= fail_index < len(resp.tool_calls):
        call = resp.tool_calls[fail_index]
        resp.tool_calls[fail_index] = ToolCallTrace(
            name=call.name,
            arguments=dict(call.arguments),
            order_index=call.order_index,
            status="failed",
            error=params.get("tool_error", message),
        )


_INJECTORS: dict[str, Callable[[AdapterResponse, dict], None]] = {
    INVALID_JSON: _inject_invalid_json,
    DROP_JSON_FIELD: _inject_drop_json_field,
    SET_JSON_FIELD: _inject_set_json_field,
    DROP_TOOL: _inject_drop_tool,
    WRONG_TOOL: _inject_wrong_tool,
    ADD_TOOL: _inject_add_tool,
    ARG_DRIFT: _inject_arg_drift,
    SWAP_TOOL_ORDER: _inject_swap_tool_order,
    UNSUPPORTED_CLAIM: _inject_unsupported_claim,
    DROP_SOURCE: _inject_drop_source,
    FLIP_REFUSAL: _inject_flip_refusal,
    SET_PARSED_FIELD: _inject_set_parsed_field,
    ADD_LATENCY: _inject_add_latency,
    ADD_COST: _inject_add_cost,
    ADD_ERROR: _inject_add_error,
}

INJECTION_KINDS: frozenset[str] = frozenset(_INJECTORS)


@dataclass
class Injection:
    """One defect to inject: a ``kind`` from :data:`INJECTION_KINDS` and params."""

    kind: str
    params: dict[str, Any] = field(default_factory=dict)

    def apply(self, base: AdapterResponse) -> AdapterResponse:
        """Return a copy of ``base`` with this defect injected."""
        injector = _INJECTORS.get(self.kind)
        if injector is None:
            raise AdapterError(f"unknown injection kind {self.kind!r}")
        resp = base.copy()
        injector(resp, self.params)
        return resp


# --------------------------------------------------------------------------- #
# Default plan derived from CaseExpectation ground truth.
# --------------------------------------------------------------------------- #

# A representative injector per regression category. Parameters that need the
# baseline (e.g. which field to drop) are resolved in ``_plan_injection_for``.
_CATEGORY_DEFAULT: dict[str, Injection] = {
    "structured_output_regression": Injection(INVALID_JSON),
    "tool_call_regression": Injection(DROP_TOOL),
    "evidence_grounding_regression": Injection(
        UNSUPPORTED_CLAIM, {"claim": "unsupported synthetic claim"}
    ),
    "refusal_behavior_shift": Injection(FLIP_REFUSAL),
    "latency_regression": Injection(ADD_LATENCY, {"delta_ms": 250.0}),
    "cost_regression": Injection(ADD_COST, {"delta_usd": 0.004}),
    "error_rate_regression": Injection(
        ADD_ERROR,
        {
            "kind": "tool_error",
            "message": "synthetic injected app error",
            "origin": "app",
        },
    ),
}


def _plan_injection_for(expectation, baseline: RunRecord | None) -> Injection | None:
    """Build the injection for one case from its ground-truth expectation.

    * A real regression → the representative injector for its category.
    * ``infra`` attribution with no flagged category → an ``infra``-origin error
      (surfaced but not a regression; research question 9).
    * A true control → ``None`` (no injection; candidate equals baseline).
    """
    from ..datasets import NO_REGRESSION

    category = expectation.expected_category
    if category == NO_REGRESSION:
        if expectation.expected_attribution == "infra":
            params: dict[str, Any] = {
                "kind": "timeout",
                "message": "synthetic upstream timeout (harness/infra)",
                "origin": "infra",
            }
            if baseline is not None and baseline.tool_calls:
                params["fail_tool_index"] = len(baseline.tool_calls) - 1
            return Injection(ADD_ERROR, params)
        return None

    template = _CATEGORY_DEFAULT.get(category)
    if template is None:  # pragma: no cover - guarded by the fixture validator
        raise AdapterError(f"no default injector for category {category!r}")
    injection = Injection(template.kind, dict(template.params))
    if injection.kind == ADD_ERROR and baseline is not None and baseline.tool_calls:
        injection.params.setdefault("fail_tool_index", len(baseline.tool_calls) - 1)
    return injection


class ScriptedRegressionAdapter(ModelAdapter):
    """Inject a scripted regression into a clean baseline per case.

    ``baselines`` maps ``case_id`` to the clean :class:`AdapterResponse`; ``plan``
    maps ``case_id`` to the :class:`Injection` to apply (a case absent from the
    plan is emitted unchanged). Regardless of the context's ``app_version``, this
    adapter always applies its plan — it is a *candidate* generator; use the
    recorded or mock adapter for baselines.
    """

    name = "scripted-regression"

    def __init__(
        self,
        *,
        baselines: Mapping[str, AdapterResponse],
        plan: Mapping[str, Injection] | None = None,
    ) -> None:
        self._baselines: dict[str, AdapterResponse] = dict(baselines)
        self._plan: dict[str, Injection] = dict(plan or {})

    @classmethod
    def from_fixtures(
        cls, path: Path | str | None = None
    ) -> ScriptedRegressionAdapter:
        """Build baselines + a plan from the committed fixtures under ``path``.

        Baselines come from each suite's ``baseline.jsonl``; the plan is derived
        from the per-case ``CaseExpectation`` ground truth.
        """
        from ..datasets import (
            SUITE_NAMES,
            load_suite_fixture,
            resolve_fixture_roots,
        )

        evals_root, data_root = resolve_fixture_roots(path)
        baselines: dict[str, AdapterResponse] = {}
        plan: dict[str, Injection] = {}
        for suite in SUITE_NAMES:
            fixture = load_suite_fixture(suite, evals_root, data_root)
            base_by_id = {r.case_id: r for r in fixture.baseline}
            for case_id, record in base_by_id.items():
                baselines[case_id] = response_from_record(record)
            for expectation in fixture.expectations:
                injection = _plan_injection_for(
                    expectation, base_by_id.get(expectation.case_id)
                )
                if injection is not None:
                    plan[expectation.case_id] = injection
        return cls(baselines=baselines, plan=plan)

    def baseline_response(self, case_id: str) -> AdapterResponse:
        try:
            return self._baselines[case_id]
        except KeyError:
            raise AdapterError(
                f"no baseline response for case_id={case_id!r}"
            ) from None

    def injection_for(self, case_id: str) -> Injection | None:
        return self._plan.get(case_id)

    def generate(self, case: EvalCase, context: AdapterContext) -> RunRecord:
        base = self.baseline_response(case.case_id)
        injection = self._plan.get(case.case_id)
        response = injection.apply(base) if injection is not None else base.copy()
        return build_run_record(case, context, response)
