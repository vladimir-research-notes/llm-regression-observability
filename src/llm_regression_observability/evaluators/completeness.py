"""Trace completeness: is the *observation* trustworthy, before we score it?

Every other metric assumes the trace says what happened. This one asks whether it
does. A drop in ``trace_completeness`` means the harness or the exporter is
broken — a run whose latency was never recorded scores as the fastest run in the
suite, and a run whose tool calls were dropped scores as perfectly obedient. That
is not an application regression, and Milestone 6 must not read it as one.

The score is ``passed / total`` over a fixed checklist. Nine checks always apply;
two more are added when the case gives them meaning (a case with ``allowed_tools``
should have called a tool; a case with ``retrieval_docs`` should have cited one).
Each check is named, so ``detail["failed"]`` points at the field to fix rather
than at a number.

``parsed_output_consistent`` is the subtle one. It fires only in one direction:
*if* ``final_output`` parses as JSON, then ``parsed_output`` must be ``None`` or
equal to it. It deliberately does **not** require ``parsed_output`` to be a parse
of ``final_output`` in general — a refusal trace legitimately carries
``{"refused": true}`` beside prose. And a candidate whose JSON output genuinely
broke has ``parsed_output = null``, which is a correct trace of a real failure,
not an incomplete one. Conflating the two would make
``structured_output_regression`` and a harness bug indistinguishable.

The executor's ``--continue-on-error`` failure record scores well below ``1.0``
here (no latency, no output tokens), which is the intended signal: it is a record
of the harness failing, and Milestone 6 can exclude it from scoring rather than
count its zeros as an application regression.
"""

from __future__ import annotations

import re

from ..schemas import EvalCase, MetricResult, RunRecord
from .base import metric, parse_json, ratio

__all__ = ["BASE_CHECKS", "CONDITIONAL_CHECKS", "completeness_checks", "evaluate"]

#: ISO-8601 with an explicit offset or ``Z``. A naive timestamp is ambiguous and
#: therefore incomplete.
_ISO_8601 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)

_IDENTITY_FIELDS = (
    "run_id",
    "case_id",
    "app_version",
    "model_id",
    "prompt_version",
    "tool_version",
    "timestamp",
)

#: Checks applied to every record.
BASE_CHECKS: tuple[str, ...] = (
    "identity_fields",
    "timestamp_iso8601",
    "output_present",
    "latency_recorded",
    "tokens_recorded",
    "cost_recorded",
    "tool_order_contiguous",
    "failed_tool_has_error",
    "parsed_output_consistent",
)

#: Checks applied only when the case makes them meaningful.
CONDITIONAL_CHECKS: tuple[str, ...] = (
    "tool_calls_present",
    "retrieval_sources_present",
)


def _identity_fields(record: RunRecord) -> bool:
    return all(str(getattr(record, name)).strip() for name in _IDENTITY_FIELDS)


def _timestamp_iso8601(record: RunRecord) -> bool:
    return _ISO_8601.match(record.timestamp) is not None


def _output_present(record: RunRecord) -> bool:
    """A run produced an answer, or explained why it did not."""
    return bool(record.final_output.strip()) or bool(record.errors)


def _tool_order_contiguous(record: RunRecord) -> bool:
    return all(
        call.order_index == i for i, call in enumerate(record.tool_calls)
    )


def _failed_tool_has_error(record: RunRecord) -> bool:
    return all(
        call.error for call in record.tool_calls if call.status == "failed"
    )


def _parsed_output_consistent(record: RunRecord) -> bool:
    ok, parsed, _ = parse_json(record.final_output)
    if not ok:
        return True  # No constraint: parsed_output is app state, not a parse.
    return record.parsed_output is None or record.parsed_output == parsed


def completeness_checks(case: EvalCase, record: RunRecord) -> dict[str, bool]:
    """Run the checklist. Keys are stable; conditional entries appear only when
    the case supplies the context that makes them meaningful."""
    checks: dict[str, bool] = {
        "identity_fields": _identity_fields(record),
        "timestamp_iso8601": _timestamp_iso8601(record),
        "output_present": _output_present(record),
        "latency_recorded": record.latency_ms > 0.0,
        "tokens_recorded": (
            record.input_tokens_estimate > 0 and record.output_tokens_estimate > 0
        ),
        "cost_recorded": record.cost_estimate_usd > 0.0,
        "tool_order_contiguous": _tool_order_contiguous(record),
        "failed_tool_has_error": _failed_tool_has_error(record),
        "parsed_output_consistent": _parsed_output_consistent(record),
    }
    if case.allowed_tools is not None:
        checks["tool_calls_present"] = bool(record.tool_calls)
    if case.retrieval_docs:
        checks["retrieval_sources_present"] = bool(record.retrieval_sources)
    return checks


def evaluate(case: EvalCase, record: RunRecord) -> list[MetricResult]:
    """Emit ``trace_completeness`` — a harness signal, not an application score."""
    checks = completeness_checks(case, record)
    passed = [name for name, ok in checks.items() if ok]
    failed = sorted(name for name, ok in checks.items() if not ok)
    value = ratio(len(passed), len(checks), empty=1.0)
    return [
        metric(
            record.case_id,
            "trace_completeness",
            value,
            passed=not failed,
            detail={
                "checks_run": len(checks),
                "failed": failed,
                "signal": "harness",
            },
        )
    ]
