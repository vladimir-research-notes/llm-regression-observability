"""``agent_control_bench`` — refuse-or-answer trials and policy violations.

.. code-block:: json

    {"suite": "agent-control-bench", "format_version": "1.1",
     "policy_version": "acb-2026-06", "model": "synthetic-guard-1",
     "recorded_at": "2026-06-01T12:00:00Z",
     "trials": [{"trial_id": "...", "prompt": "...",
                 "policy": {"must_refuse": true, "risk_label": "high"},
                 "response": {"refused": true, "text": "I can't help with that.",
                              "risk_label": "high"},
                 "violations": [{"rule": "policy_bypass", "detail": "..."}],
                 "latency_ms": 90.0, "tokens": {"input": 40, "output": 12}}]}

The interesting property of this source is that it carries the *expectation* and the
*decision* separately, which is exactly the split ``evaluators/refusal.py`` wants:

* ``policy.must_refuse`` → ``expected_properties["should_refuse"]``
* ``policy.risk_label``  → ``expected_properties["expected_risk_label"]``
* ``response.refused`` / ``response.risk_label`` → ``parsed_output``

Putting the decision in ``parsed_output`` is not a convenience. ``refusal_decision``
prefers ``parsed_output["refused"]`` — the application's own answer — and falls back
to a small lexical scan of the output text only when the trace does not label one.
Every ``refusal_match`` result records which of the two it used, so an import that
loses the label degrades *visibly*, into ``decision_source="lexical_fallback"``,
rather than silently into a guess. A trace from this source should never see the
fallback, and ``tests/test_integration_contracts.py`` asserts it does not.

**A violation is an application error.** ``violations[].rule`` becomes an
``ErrorRecord`` kind, attributed through :attr:`error_origins`. A bypassed policy is
the application's behavior; a grader that crashed is the harness's.

The committed example is a synthetic boundary prompt about a fictional stored
setting — the same non-exploitative shape the ``refusal_boundary`` fixture suite
uses. Nothing here is an actual attack, and nothing here is a safety authority.
"""

from __future__ import annotations

from typing import Any

from ...schemas import EvalCase, RunRecord
from .base import (
    ImportContext,
    ImportResult,
    TraceImporter,
    TraceImportError,
    as_float,
    as_int,
    as_list,
    as_mapping,
    as_str,
    build_case,
    build_record,
    check_source_version,
    error_record,
    require,
)

__all__ = ["AgentControlBenchImporter"]

_DOCUMENT_MAPPED = ("suite", "format_version", "policy_version", "model",
                    "recorded_at", "trials")
_TRIAL_MAPPED = ("trial_id", "prompt", "policy", "response", "violations",
                 "latency_ms", "tokens")
_POLICY_MAPPED = ("must_refuse", "risk_label")
_RESPONSE_MAPPED = ("refused", "text", "risk_label")
_VIOLATION_MAPPED = ("rule", "detail")


class AgentControlBenchImporter(TraceImporter):
    """Map control-bench trials onto refusal-scored ``(EvalCase, RunRecord)`` pairs."""

    source = "agent_control_bench"
    source_schema = "agent-control-bench"
    supported_source_versions = frozenset({"1.1"})
    ignored_fields = ()
    absent_fields = ("tool_calls", "retrieval_sources", "cost_estimate_usd")
    error_origins = {
        "policy_bypass": "app",
        "unsafe_completion": "app",
        "over_refusal": "app",
        "grader_crashed": "infra",
    }

    def parse(self, document: Any, context: ImportContext) -> ImportResult:
        doc = as_mapping(document, path=self.source_schema)
        warnings: list[str] = []
        unknown: set[str] = set()
        self.unknown(
            doc, _DOCUMENT_MAPPED, path="document", context=context,
            warnings=warnings, unknown=unknown,
        )
        version = as_str(doc.get("format_version", ""), path="format_version")
        check_source_version(
            version, supported=self.supported_source_versions, source=self.source,
            context=context, warnings=warnings,
        )
        model_id, timestamp = context.stamp(
            model_id=as_str(doc.get("model", ""), path="model"),
            timestamp=as_str(doc.get("recorded_at", ""), path="recorded_at"),
        )
        # The policy version is what the *tools* — the guard rails — were on.
        prompt_version, tool_version = context.versions(
            tool_version=as_str(doc.get("policy_version", ""), path="policy_version")
        )

        records: list[RunRecord] = []
        cases: list[EvalCase] = []
        for index, raw in enumerate(as_list(doc.get("trials", []), path="trials")):
            path = f"trials[{index}]"
            trial = as_mapping(raw, path=path)
            self.unknown(
                trial, _TRIAL_MAPPED, path=path, context=context, warnings=warnings,
                unknown=unknown,
            )
            case_id = as_str(require(trial, "trial_id", path=path),
                             path=f"{path}.trial_id")
            policy = as_mapping(trial.get("policy", {}), path=f"{path}.policy")
            self.unknown(
                policy, _POLICY_MAPPED, path=f"{path}.policy", context=context,
                warnings=warnings, unknown=unknown,
            )
            cases.append(self._case(case_id, trial, policy, path=path))
            records.append(
                self._record(
                    trial, case_id=case_id, path=path, model_id=model_id,
                    prompt_version=prompt_version, tool_version=tool_version,
                    timestamp=timestamp, context=context, warnings=warnings,
                    unknown=unknown,
                )
            )

        return self.result(
            source_version=version, records=records, cases=cases,
            warnings=warnings, unknown=unknown,
        )

    def _case(
        self, case_id: str, trial: dict[str, Any], policy: dict[str, Any], *, path: str
    ) -> EvalCase:
        expected: dict[str, Any] = {}
        must_refuse = policy.get("must_refuse")
        if must_refuse is not None:
            if not isinstance(must_refuse, bool):
                raise TraceImportError(f"{path}.policy.must_refuse: expected a boolean")
            expected["should_refuse"] = must_refuse
        label = policy.get("risk_label")
        if label is not None:
            expected["expected_risk_label"] = as_str(
                label, path=f"{path}.policy.risk_label"
            )
        return build_case(
            case_id=case_id,
            version=self.source_schema,
            prompt=as_str(trial.get("prompt", ""), path=f"{path}.prompt"),
            risk_category="refusal",
            expected_properties=expected,
            tags=["imported", self.source],
        )

    def _record(
        self,
        trial: dict[str, Any],
        *,
        case_id: str,
        path: str,
        model_id: str,
        prompt_version: str,
        tool_version: str,
        timestamp: str,
        context: ImportContext,
        warnings: list[str],
        unknown: set[str],
    ) -> RunRecord:
        response = as_mapping(trial.get("response", {}), path=f"{path}.response")
        self.unknown(
            response, _RESPONSE_MAPPED, path=f"{path}.response", context=context,
            warnings=warnings, unknown=unknown,
        )
        refused = response.get("refused")
        if refused is not None and not isinstance(refused, bool):
            raise TraceImportError(f"{path}.response.refused: expected a boolean")

        # ``parsed_output`` is where ``evaluators/refusal.py`` looks first. Losing
        # these two keys would silently demote the scorer to its lexical fallback.
        parsed: dict[str, Any] = {}
        if refused is not None:
            parsed["refused"] = refused
        label = response.get("risk_label")
        if label is not None:
            parsed["risk_label"] = as_str(label, path=f"{path}.response.risk_label")

        errors = []
        for index, raw in enumerate(
            as_list(trial.get("violations", []), path=f"{path}.violations")
        ):
            where = f"{path}.violations[{index}]"
            violation = as_mapping(raw, path=where)
            self.unknown(
                violation, _VIOLATION_MAPPED, path=where, context=context,
                warnings=warnings, unknown=unknown,
            )
            rule = as_str(require(violation, "rule", path=where), path=f"{where}.rule")
            errors.append(
                error_record(
                    kind=rule,
                    message=as_str(violation.get("detail", ""), path=f"{where}.detail"),
                    origin=self.origin(rule, warnings=warnings),
                )
            )

        latency_ms = trial.get("latency_ms")
        tokens = as_mapping(trial.get("tokens", {}), path=f"{path}.tokens")
        return build_record(
            run_id=context.run_id,
            case_id=case_id,
            app_version=context.app_version,
            model_id=model_id,
            prompt_version=prompt_version,
            tool_version=tool_version,
            timestamp=timestamp,
            input_tokens_estimate=as_int(tokens.get("input", 0),
                                         path=f"{path}.tokens.input"),
            output_tokens_estimate=as_int(tokens.get("output", 0),
                                          path=f"{path}.tokens.output"),
            latency_ms=self.latency(
                None if latency_ms is None
                else as_float(latency_ms, path=f"{path}.latency_ms"),
                case_id=case_id, context=context, warnings=warnings,
            ),
            final_output=as_str(response.get("text", ""), path=f"{path}.response.text"),
            parsed_output=parsed or None,
            errors=errors,
        )
