"""Spans: a nested, OpenTelemetry-shaped *view* over a recorded trace.

A run is decomposed into one span tree per case (``docs/OBSERVABILITY.md``):

.. code-block:: text

    request                one per case invocation
    ├── model_call         the (mock/recorded) model call
    ├── tool_call (0..n)   each tool invocation, in trace order
    ├── retrieval (0..n)   each retrieved source
    └── evaluator (1..n)   one per metric scored on the output

Three decisions define what these spans are, and are worth stating plainly
because each one is a place where a telemetry layer usually starts lying.

**Spans are derived, not emitted.** Nothing in this package instruments a live
call — there is no live call. :func:`build_case_spans` is a pure function of
``(EvalCase, RunRecord, metrics)``, so the span tree is a *projection* of the
artifact on disk, rebuildable at any time and byte-identical every time. That is
why spans can be a derived artifact at all, and why the stage that owns them is
the one holding both the trace and the scores (:mod:`.aggregation`), not the
executor, which has never scored anything.

**Only one duration was ever recorded.** ``RunRecord.latency_ms`` is the single
timing the trace carries; ``ToolCallTrace`` and ``RetrievalTrace`` carry none. So
the request span's interval is *recorded* (``timing="recorded"``) and its
application children get a **contiguous, non-overlapping partition** of that
interval in trace order (``timing="derived"``). The partition asserts *ordering*,
which the trace records, and it does not assert *duration*, which the trace does
not — a derived span's width is ``latency_ms / step_count`` and means nothing on
its own. Evaluator spans are zero-width at the request's end
(``timing="none"``): scoring happens afterwards, in the harness, and is not part
of the application's latency. Reading a derived width as a measurement is the one
misuse this module cannot prevent, so it is labeled on every span.
:data:`TIMING_SOURCES` is that vocabulary. No clock is consulted anywhere.

**Spans carry symbols, never free text.** A span attribute may hold an
identifier (``tool_name``, ``source_id``, ``metric``, ``error_kind``), a count, a
status, or a boolean. It never holds a prompt, a model output, a tool argument
*value*, a retrieved snippet, or an error *message*. Tool argument **keys** are
carried because they are schema, not content — and because Milestone 9's redactor
masks strings inside ``arguments`` keys included, so a key that was ever a secret is
a placeholder by the time it reaches here. :func:`write_spans` gates the rendered
file against the same certain-tier table the report gate uses: spans are masked
nowhere and refused everywhere, because every string in them is a join key.

Span status is about whether the *operation* succeeded, not whether the *score*
passed: an evaluator span that reports ``passed=False`` has ``status="ok"``,
because the scorer ran fine and found a real failure. Errors attach to the
``request`` span with their origin (``app`` / ``infra``) and their kinds, since
:class:`~..schemas.ErrorRecord` does not name the step it occurred in — inventing
a parent for it would be a guess the trace cannot support.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from ..evaluators.base import rounded
from ..evaluators.errors import app_errors, attribution, infra_errors
from ..schemas import EvalCase, MetricResult, RunRecord, Schema
from ..traces.redaction import assert_clean
from ..traces.storage import read_jsonl, render_jsonl, write_jsonl
from .metrics import signal_of

__all__ = [
    "ObservabilityError",
    "SPANS_JSONL",
    "SPAN_NAMES",
    "TIMING_SOURCES",
    "TIMING_RECORDED",
    "TIMING_DERIVED",
    "TIMING_NONE",
    "TRACE_ID_WIDTH",
    "SPAN_ID_WIDTH",
    "Span",
    "trace_id_for",
    "span_id_for",
    "step_bounds",
    "build_case_spans",
    "build_run_spans",
    "validate_spans",
    "span_depths",
    "write_spans",
    "read_spans",
]


class ObservabilityError(RuntimeError):
    """Raised on a trace that cannot be projected into a valid span tree."""


#: Artifact file name, written next to ``run.jsonl``.
SPANS_JSONL = "spans.jsonl"

#: The closed span vocabulary. Adding a name is a contract change.
SPAN_NAMES: tuple[str, ...] = (
    "request",
    "model_call",
    "tool_call",
    "retrieval",
    "evaluator",
)

#: ``recorded`` — the interval is a value the trace carries (``latency_ms``).
#: ``derived`` — the interval orders a step within its parent; its *width* is an
#: equal share of the parent, not a measurement.
#: ``none`` — the step has no place on the application's timeline at all.
TIMING_RECORDED = "recorded"
TIMING_DERIVED = "derived"
TIMING_NONE = "none"
TIMING_SOURCES: tuple[str, ...] = (TIMING_RECORDED, TIMING_DERIVED, TIMING_NONE)

#: OpenTelemetry id widths, in hex characters.
TRACE_ID_WIDTH = 32
SPAN_ID_WIDTH = 16

_OK = "ok"
_ERROR = "error"


@dataclass(kw_only=True)
class Span(Schema):
    """One span. Written to ``results/runs/<run_id>/spans.jsonl``.

    ``start_ms`` / ``end_ms`` are relative to the start of the case's request and
    are read off the trace, never off the clock. ``attributes["timing"]`` names
    which of :data:`TIMING_SOURCES` produced them.
    """

    trace_id: str
    span_id: str
    parent_id: str | None = None
    name: str
    case_id: str
    run_id: str
    start_ms: float = 0.0
    end_ms: float = 0.0
    status: str = _OK
    attributes: dict = field(default_factory=dict)

    _ALLOWED: ClassVar[dict[str, frozenset[str]]] = {
        "name": frozenset(SPAN_NAMES),
        "status": frozenset({_OK, _ERROR}),
    }

    @property
    def duration_ms(self) -> float:
        return rounded(self.end_ms - self.start_ms)

    @property
    def timing(self) -> str:
        return str(self.attributes.get("timing", TIMING_NONE))


# --------------------------------------------------------------------------- #
# Deterministic identifiers
# --------------------------------------------------------------------------- #


def _digest(parts: Sequence[str], width: int) -> str:
    """A stable hex id derived from ``parts``. Content-addressed, not random."""
    joined = "|".join(parts).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()[:width]


def trace_id_for(run_id: str, case_id: str) -> str:
    """The 32-hex trace id of one (run, case). One case is one trace."""
    return _digest((run_id, case_id), TRACE_ID_WIDTH)


def span_id_for(run_id: str, case_id: str, path: str) -> str:
    """The 16-hex span id of ``path`` within one (run, case).

    ``path`` is the span's position in the tree — ``"request"``,
    ``"request/tool_call/1"``, ``"request/evaluator/task_success"`` — so an id is
    a function of *where the span sits*, not of when it was generated.
    """
    return _digest((run_id, case_id, path), SPAN_ID_WIDTH)


# --------------------------------------------------------------------------- #
# Timing layout
# --------------------------------------------------------------------------- #


def step_bounds(latency_ms: float, steps: int) -> list[float]:
    """``steps + 1`` boundaries partitioning ``[0, latency_ms]`` evenly.

    Shared boundaries (rather than per-span arithmetic) are what make adjacent
    spans exactly contiguous after rounding: span ``i`` is ``[b[i], b[i+1]]``.
    """
    if latency_ms < 0.0:
        raise ObservabilityError(f"negative latency_ms ({latency_ms}) in trace")
    if steps <= 0:
        return [0.0]
    inner = [rounded(latency_ms * i / steps) for i in range(steps)]
    return [*inner, rounded(latency_ms)]


# --------------------------------------------------------------------------- #
# Projection
# --------------------------------------------------------------------------- #


def _request_attributes(record: RunRecord, case: EvalCase) -> dict[str, Any]:
    errors = record.errors
    return {
        "app_version": record.app_version,
        "model_id": record.model_id,
        "prompt_version": record.prompt_version,
        "tool_version": record.tool_version,
        "risk_category": case.risk_category,
        "redaction_status": record.redaction_status,
        "error_count": len(errors),
        "app_error_count": len(app_errors(record)),
        "infra_error_count": len(infra_errors(record)),
        # Errors carry no step, so they attach here with their origin and kind.
        "error_kinds": sorted({error.kind for error in errors}),
        "attribution": attribution(record),
        "timing": TIMING_RECORDED,
    }


def _tool_allowed(case: EvalCase, name: str) -> bool | None:
    """``None`` when the case declares no allow-list — unknown, not permitted."""
    if case.allowed_tools is None:
        return None
    return name in case.allowed_tools


def build_case_spans(
    case: EvalCase,
    record: RunRecord,
    results: Sequence[MetricResult] = (),
) -> list[Span]:
    """Project one (case, record) into a span tree, in depth-first order.

    ``results`` are the case's :class:`~..schemas.MetricResult`s; pass none and
    the tree carries no evaluator spans, which is the correct shape for a run
    that has not been scored yet.
    """
    if case.case_id != record.case_id:
        raise ObservabilityError(
            f"case/record mismatch: case {case.case_id!r} vs "
            f"record {record.case_id!r}"
        )

    run_id, case_id = record.run_id, record.case_id
    trace_id = trace_id_for(run_id, case_id)
    root_id = span_id_for(run_id, case_id, "request")

    def make(
        path: str,
        name: str,
        parent_id: str | None,
        start: float,
        end: float,
        status: str,
        attributes: dict[str, Any],
    ) -> Span:
        return Span(
            trace_id=trace_id,
            span_id=span_id_for(run_id, case_id, path),
            parent_id=parent_id,
            name=name,
            case_id=case_id,
            run_id=run_id,
            start_ms=start,
            end_ms=end,
            status=status,
            attributes=attributes,
        )

    # One step per application child: the model call, then tools, then sources.
    # Cross-category interleaving is not recorded, so this order is canonical,
    # not observed.
    steps = 1 + len(record.tool_calls) + len(record.retrieval_sources)
    bounds = step_bounds(record.latency_ms, steps)
    end_ms = bounds[-1]

    spans = [
        make(
            "request",
            "request",
            None,
            bounds[0],
            end_ms,
            _ERROR if record.errors else _OK,
            _request_attributes(record, case),
        ),
        make(
            "request/model_call",
            "model_call",
            root_id,
            bounds[0],
            bounds[1],
            _OK,
            {
                "model_id": record.model_id,
                "input_tokens": record.input_tokens_estimate,
                "output_tokens": record.output_tokens_estimate,
                "cost_estimate_usd": record.cost_estimate_usd,
                "output_chars": len(record.final_output),
                "parsed_output_present": record.parsed_output is not None,
                "timing": TIMING_DERIVED,
            },
        ),
    ]

    step = 1
    for index, call in enumerate(record.tool_calls):
        spans.append(
            make(
                f"request/tool_call/{index}",
                "tool_call",
                root_id,
                bounds[step],
                bounds[step + 1],
                _ERROR if call.status == "failed" else _OK,
                {
                    "tool_name": call.name,
                    "order_index": call.order_index,
                    "tool_status": call.status,
                    "allowed": _tool_allowed(case, call.name),
                    # Keys are schema; values are content and stay in the trace.
                    "argument_keys": sorted(call.arguments),
                    "has_error": bool(call.error),
                    "timing": TIMING_DERIVED,
                },
            )
        )
        step += 1

    for index, source in enumerate(record.retrieval_sources):
        spans.append(
            make(
                f"request/retrieval/{index}",
                "retrieval",
                root_id,
                bounds[step],
                bounds[step + 1],
                _OK,
                {
                    "source_id": source.source_id,
                    "supported": source.supported,
                    "claim_present": source.claim is not None,
                    "snippet_chars": len(source.snippet),
                    "timing": TIMING_DERIVED,
                },
            )
        )
        step += 1

    for result in results:
        spans.append(
            make(
                f"request/evaluator/{result.metric}",
                "evaluator",
                root_id,
                # Scoring is post hoc: it has an order, but no place on the
                # application's timeline.
                end_ms,
                end_ms,
                # A failing score is a result, not a span error.
                _OK,
                {
                    "metric": result.metric,
                    "value": result.value,
                    "passed": result.passed,
                    "signal": signal_of(result.metric),
                    "timing": TIMING_NONE,
                },
            )
        )

    return spans


def build_run_spans(
    cases: Sequence[EvalCase],
    records: Sequence[RunRecord],
    scored: dict[str, list[MetricResult]] | None = None,
) -> list[Span]:
    """Project a whole run, in record order. Raises on a record with no case."""
    by_id = {case.case_id: case for case in cases}
    spans: list[Span] = []
    for record in records:
        case = by_id.get(record.case_id)
        if case is None:
            raise ObservabilityError(
                f"record for unknown case_id {record.case_id!r}; "
                f"suite defines {sorted(by_id)}"
            )
        results = (scored or {}).get(record.case_id, [])
        spans.extend(build_case_spans(case, record, results))
    return spans


# --------------------------------------------------------------------------- #
# Structural checks
# --------------------------------------------------------------------------- #


def span_depths(spans: Sequence[Span]) -> dict[str, int]:
    """Depth of every span (root ``request`` is ``1``), keyed by ``span_id``."""
    by_id = {span.span_id: span for span in spans}
    depths: dict[str, int] = {}

    def depth_of(span: Span, seen: frozenset[str]) -> int:
        if span.span_id in depths:
            return depths[span.span_id]
        if span.span_id in seen:
            raise ObservabilityError(f"span {span.span_id} is its own ancestor")
        if span.parent_id is None:
            depths[span.span_id] = 1
            return 1
        parent = by_id.get(span.parent_id)
        if parent is None:
            raise ObservabilityError(
                f"span {span.span_id} ({span.name}) names a parent "
                f"{span.parent_id} that is not in the trace"
            )
        depths[span.span_id] = depth_of(parent, seen | {span.span_id}) + 1
        return depths[span.span_id]

    for span in spans:
        depth_of(span, frozenset())
    return depths


def validate_spans(spans: Sequence[Span]) -> None:
    """Assert the invariants a consumer of ``spans.jsonl`` may rely on.

    Every parent exists and shares the child's trace; no span is its own
    ancestor; a child's interval is contained in its parent's; and the *derived*
    siblings of one request tile its interval contiguously, in emission order.
    A violation means the projection or the trace is wrong, and it is raised
    rather than rendered.
    """
    by_id = {span.span_id: span for span in spans}
    if len(by_id) != len(spans):
        raise ObservabilityError("duplicate span_id in trace")
    span_depths(spans)

    children: dict[str, list[Span]] = {}
    for span in spans:
        if span.start_ms > span.end_ms:
            raise ObservabilityError(
                f"span {span.span_id} ({span.name}) ends before it starts"
            )
        if span.parent_id is None:
            continue
        parent = by_id[span.parent_id]
        if parent.trace_id != span.trace_id:
            raise ObservabilityError(
                f"span {span.span_id} ({span.name}) and its parent are in "
                "different traces"
            )
        if span.start_ms < parent.start_ms or span.end_ms > parent.end_ms:
            raise ObservabilityError(
                f"span {span.span_id} ({span.name}) [{span.start_ms}, "
                f"{span.end_ms}] escapes its parent [{parent.start_ms}, "
                f"{parent.end_ms}]"
            )
        children.setdefault(span.parent_id, []).append(span)

    for parent_id, kids in children.items():
        parent = by_id[parent_id]
        derived = [kid for kid in kids if kid.timing == TIMING_DERIVED]
        if not derived:
            continue
        if derived[0].start_ms != parent.start_ms:
            raise ObservabilityError(
                f"derived children of {parent_id} do not start at the parent"
            )
        if derived[-1].end_ms != parent.end_ms:
            raise ObservabilityError(
                f"derived children of {parent_id} do not end at the parent"
            )
        for left, right in zip(derived, derived[1:], strict=False):
            if left.end_ms != right.start_ms:
                raise ObservabilityError(
                    f"derived children of {parent_id} overlap or leave a gap "
                    f"between {left.span_id} and {right.span_id}"
                )


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def write_spans(path: Path | str, spans: Sequence[Span]) -> Path:
    """Validate, gate, then write ``spans.jsonl``.

    Neither an invalid tree nor a credential-like string is ever written. Spans are a
    **gate** target and not a **mask** target, which is the answer Milestone 9 owed
    Milestone 8's open question: every attribute here is a symbol — a tool name, a
    source id, a metric name, an error kind — and every one of them is a join key that
    the trace, the comparison and the report all index by. Masking a tool name would
    silently break tool-call comparison in exchange for protecting a string that, by
    construction, holds no free text.

    So the rule is the report's rule: the record was masked upstream (the executor
    redacts before it hashes), and what lands here is scanned and refused. A secret in
    a tool name means the *source trace* is wrong, and that is worth stopping for.
    """
    validate_spans(spans)
    assert_clean(render_jsonl(spans), source=str(path))
    return write_jsonl(path, spans)


def read_spans(path: Path | str) -> list[Span]:
    """Read and validate ``spans.jsonl``."""
    spans = read_jsonl(path, Span)
    validate_spans(spans)
    return spans
