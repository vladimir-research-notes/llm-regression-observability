"""Error classification: whose fault was it, and how often?

This module is the mechanism behind research question 9 — *distinguish
application regressions from harness/infrastructure noise*. A gateway timeout on
a flaky mock and a tool the agent learned to call wrong both surface as a failed
call and a red trace. Only one of them is a regression in the thing under test.

``ErrorRecord.origin`` carries the answer, so the split is a recorded property of
the trace rather than a guess. :func:`attribution` folds a record's errors into
one verdict, and it is deliberately asymmetric: a record is ``infra`` only when
it has errors and **every** one of them is infra-origin. A single app error makes
the whole record ``app``. Under uncertainty it flags, because a missed regression
costs more than a noisy one (``docs/REGRESSION_DETECTION.md``).

Per-record metrics:

``app_error_count``
    App-origin errors. This is the one that can raise ``error_rate_regression``.
``infra_error_count``
    Infra-origin errors. Surfaced, never blamed — it is in
    :data:`~.base.NEVER_FLAG_METRICS`.
``retry_count``
    Repeated attempts. A *retry* is a call that repeats the name and arguments of
    an earlier **failed** call. Defining it structurally rather than by an error
    ``kind`` string means an imported trace that never writes ``"tool_retry"``
    still gets counted.
``tool_error_rate``
    Failed calls over total calls. Descriptive: an infra timeout fails a tool
    call too, so ``detail["attribution"]`` says which kind of failure this was and
    the flag rule reads ``app_error_count``, not this.

Suite-level: :func:`error_rate` (fraction of cases with an app error),
:func:`infra_error_rate`, :func:`retry_total`.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..schemas import ErrorRecord, EvalCase, MetricResult, RunRecord
from .base import canonical_json, metric, ratio
from .tool_calls import failed_tool_calls

__all__ = [
    "app_errors",
    "infra_errors",
    "attribution",
    "classify_errors",
    "retry_count",
    "error_rate",
    "infra_error_rate",
    "retry_total",
    "evaluate",
]


def app_errors(record: RunRecord) -> list[ErrorRecord]:
    """Errors the application caused."""
    return [error for error in record.errors if error.origin == "app"]


def infra_errors(record: RunRecord) -> list[ErrorRecord]:
    """Errors the harness / infrastructure caused."""
    return [error for error in record.errors if error.origin == "infra"]


def attribution(record: RunRecord) -> str:
    """``"infra"`` only when the record errored and *every* error was infra-origin.

    A clean record attributes to ``"app"``: there is nothing to excuse, and the
    conservative default keeps a silent app failure from being written off as
    noise.
    """
    if record.errors and not app_errors(record):
        return "infra"
    return "app"


def classify_errors(record: RunRecord) -> dict[str, object]:
    """Errors split by origin and by kind, plus the record's attribution."""
    by_kind: dict[str, int] = {}
    for error in record.errors:
        by_kind[error.kind] = by_kind.get(error.kind, 0) + 1
    return {
        "app": len(app_errors(record)),
        "infra": len(infra_errors(record)),
        "by_kind": dict(sorted(by_kind.items())),
        "attribution": attribution(record),
    }


def retry_count(record: RunRecord) -> int:
    """Calls that repeat the name and arguments of an earlier *failed* call.

    Structural, not lexical: the trace does not have to label a retry for one to
    be counted, and a legitimate second call with different arguments is not one.
    """
    failed_before: set[tuple[str, str]] = set()
    retries = 0
    for call in record.tool_calls:
        key = (call.name, canonical_json(call.arguments))
        if key in failed_before:
            retries += 1
        if call.status == "failed":
            failed_before.add(key)
    return retries


# --------------------------------------------------------------------------- #
# Suite-level rates
# --------------------------------------------------------------------------- #


def error_rate(records: Sequence[RunRecord]) -> float:
    """Fraction of records carrying at least one **app-origin** error."""
    failing = sum(1 for record in records if app_errors(record))
    return ratio(failing, len(records), empty=0.0)


def infra_error_rate(records: Sequence[RunRecord]) -> float:
    """Fraction of records carrying at least one infra-origin error. Never flags."""
    noisy = sum(1 for record in records if infra_errors(record))
    return ratio(noisy, len(records), empty=0.0)


def retry_total(records: Sequence[RunRecord]) -> int:
    """Total retries across a run."""
    return sum(retry_count(record) for record in records)


def evaluate(case: EvalCase, record: RunRecord) -> list[MetricResult]:
    """Score error behavior for one record. Emitted for every case."""
    app = app_errors(record)
    infra = infra_errors(record)
    retries = retry_count(record)
    failed = failed_tool_calls(record)
    classification = classify_errors(record)

    return [
        metric(
            record.case_id,
            "app_error_count",
            len(app),
            passed=not app,
            detail={"errors": [e.to_dict() for e in app]},
        ),
        metric(
            record.case_id,
            "infra_error_count",
            len(infra),
            # No pass/fail: harness noise is reported, not judged.
            passed=None,
            detail={"errors": [e.to_dict() for e in infra], "never_flags": True},
        ),
        metric(
            record.case_id,
            "retry_count",
            retries,
            passed=retries == 0,
            detail={"tool_call_count": len(record.tool_calls)},
        ),
        metric(
            record.case_id,
            "tool_error_rate",
            # No tool calls means no failed tool calls.
            ratio(len(failed), len(record.tool_calls), empty=0.0),
            passed=not failed,
            detail={
                "failed_calls": [call.name for call in failed],
                "attribution": classification["attribution"],
            },
        ),
    ]
