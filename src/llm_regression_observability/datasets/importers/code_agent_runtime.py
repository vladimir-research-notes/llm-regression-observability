"""``code_agent_runtime`` — a coding agent's session, as a step list.

The shape (declared by *this* repository; ``docs/INTEGRATION_CONTRACTS.md`` says
why that is a contract rather than a guess):

.. code-block:: json

    {"schema": "code-agent-runtime/trace", "schema_version": "1",
     "session_id": "...", "agent_version": "...", "model": "...",
     "recorded_at": "2026-06-01T12:00:00Z",
     "tasks": [{"task_id": "...", "elapsed_ms": 200.0, "cost_usd": 0.002,
                "usage": {"input_tokens": 150, "output_tokens": 60},
                "steps": [{"kind": "tool", "tool": "search", "input": {},
                           "outcome": "ok"},
                          {"kind": "message", "text": "..."}],
                "result": {"text": "...", "json": null},
                "failures": [{"type": "tool_retry", "detail": "..."}]}]}

Three mappings are decisions rather than renames, and each is a place the import
could have quietly lied.

**Steps are heterogeneous; tool order is not.** ``steps`` interleaves ``tool`` and
``message`` entries. :attr:`~...schemas.ToolCallTrace.order_index` is a tool's
position *among tool calls*, not among steps — ``evaluators/tool_calls.py`` diffs
sequences of tools, and a message the agent emitted between two of them is not a
gap in that sequence. Message steps are transcript: free model text that no scorer
reads, so carrying it into a trace would add a leak surface and no signal. Dropped,
by name, in :attr:`ignored_fields`.

**``agent_version`` becomes ``tool_version``.** A code agent's tool surface — which
tools exist, what arguments they take — ships with the agent binary, and that is
what ``tool_version`` means downstream. ``prompt_version`` this source does not
carry at all, so it comes from :class:`~.base.ImportContext`, where a caller can be
honest about not knowing it. Calling ``agent_version`` a *prompt* version would have
been a lie no test could catch, because both are opaque strings.

**Failures are typed, and the type decides attribution.** ``tool_timeout`` and
``sandbox_error`` are the harness failing; ``tool_retry`` and ``assertion_failed``
are the agent failing. That table is :attr:`error_origins`; a type absent from it is
attributed to the application with a warning, because ``infra`` withholds flags and
guessing it would hide a regression (see :func:`.base.resolve_origin`).
"""

from __future__ import annotations

from typing import Any

from ...schemas import RunRecord, ToolCallTrace
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
    build_record,
    check_source_version,
    error_record,
    require,
    tool_call,
)

__all__ = ["CodeAgentRuntimeImporter", "STEP_KINDS", "OUTCOME_STATUS"]

#: Step kinds this importer understands. A ``message`` step is transcript.
STEP_KINDS: frozenset[str] = frozenset({"tool", "message"})

#: ``steps[].outcome`` → ``ToolCallTrace.status``.
OUTCOME_STATUS: dict[str, str] = {"ok": "ok", "error": "failed", "skipped": "skipped"}

_DOCUMENT_MAPPED = ("schema", "schema_version", "agent_version", "model", "recorded_at",
                    "tasks")
_TASK_MAPPED = ("task_id", "steps", "result", "usage", "cost_usd", "elapsed_ms",
                "failures")
_STEP_MAPPED = ("kind", "tool", "input", "outcome", "error")
_RESULT_MAPPED = ("text", "json")
_FAILURE_MAPPED = ("type", "detail")


class CodeAgentRuntimeImporter(TraceImporter):
    """Map a code-agent runtime session onto one ``RunRecord`` per task."""

    source = "code_agent_runtime"
    source_schema = "code-agent-runtime/trace"
    supported_source_versions = frozenset({"1"})
    ignored_fields = ("session_id", "tasks[].steps[].text")
    absent_fields = ("prompt_version", "retrieval_sources")
    error_origins = {
        "tool_retry": "app",
        "assertion_failed": "app",
        "invalid_tool_arguments": "app",
        "tool_timeout": "infra",
        "sandbox_error": "infra",
        "rate_limited": "infra",
    }

    def parse(self, document: Any, context: ImportContext) -> ImportResult:
        doc = as_mapping(document, path=self.source_schema)
        warnings: list[str] = []
        unknown: set[str] = set()
        self.unknown(
            doc,
            _DOCUMENT_MAPPED,
            ignored=("session_id",),
            path="document",
            context=context,
            warnings=warnings,
            unknown=unknown,
        )

        schema = as_str(doc.get("schema", ""), path="schema")
        if schema and schema != self.source_schema:
            raise TraceImportError(
                f"{self.source}: document declares schema {schema!r}, expected "
                f"{self.source_schema!r}"
            )
        version = as_str(doc.get("schema_version", ""), path="schema_version")
        check_source_version(
            version,
            supported=self.supported_source_versions,
            source=self.source,
            context=context,
            warnings=warnings,
        )

        model_id, timestamp = context.stamp(
            model_id=as_str(doc.get("model", ""), path="model"),
            timestamp=as_str(doc.get("recorded_at", ""), path="recorded_at"),
        )
        # The agent binary versions the tool surface. See the module docstring.
        prompt_version, tool_version = context.versions(
            tool_version=as_str(doc.get("agent_version", ""), path="agent_version")
        )

        records = [
            self._task(
                as_mapping(task, path=f"tasks[{index}]"),
                path=f"tasks[{index}]",
                model_id=model_id,
                timestamp=timestamp,
                prompt_version=prompt_version,
                tool_version=tool_version,
                context=context,
                warnings=warnings,
                unknown=unknown,
            )
            for index, task in enumerate(as_list(doc.get("tasks", []), path="tasks"))
        ]

        return self.result(
            source_version=version,
            records=records,
            warnings=warnings,
            unknown=unknown,
        )

    def _task(
        self,
        task: dict[str, Any],
        *,
        path: str,
        model_id: str,
        timestamp: str,
        prompt_version: str,
        tool_version: str,
        context: ImportContext,
        warnings: list[str],
        unknown: set[str],
    ) -> RunRecord:
        self.unknown(
            task, _TASK_MAPPED, path=path, context=context, warnings=warnings,
            unknown=unknown,
        )
        case_id = as_str(require(task, "task_id", path=path), path=f"{path}.task_id")

        calls = self._tool_calls(
            as_list(task.get("steps", []), path=f"{path}.steps"),
            path=path,
            context=context,
            warnings=warnings,
            unknown=unknown,
        )

        result = as_mapping(task.get("result", {}), path=f"{path}.result")
        self.unknown(
            result, _RESULT_MAPPED, path=f"{path}.result", context=context,
            warnings=warnings, unknown=unknown,
        )
        usage = as_mapping(task.get("usage", {}), path=f"{path}.usage")

        elapsed = task.get("elapsed_ms")
        latency = self.latency(
            None if elapsed is None else as_float(elapsed, path=f"{path}.elapsed_ms"),
            case_id=case_id,
            context=context,
            warnings=warnings,
        )

        return build_record(
            run_id=context.run_id,
            case_id=case_id,
            app_version=context.app_version,
            model_id=model_id,
            prompt_version=prompt_version,
            tool_version=tool_version,
            timestamp=timestamp,
            input_tokens_estimate=as_int(
                usage.get("input_tokens", 0), path=f"{path}.usage.input_tokens"
            ),
            output_tokens_estimate=as_int(
                usage.get("output_tokens", 0), path=f"{path}.usage.output_tokens"
            ),
            latency_ms=latency,
            cost_estimate_usd=as_float(
                task.get("cost_usd", 0.0), path=f"{path}.cost_usd"
            ),
            final_output=as_str(result.get("text", ""), path=f"{path}.result.text"),
            parsed_output=result.get("json"),
            tool_calls=calls,
            errors=self._failures(
                as_list(task.get("failures", []), path=f"{path}.failures"),
                path=path,
                context=context,
                warnings=warnings,
                unknown=unknown,
            ),
        )

    def _tool_calls(
        self,
        steps: list[Any],
        *,
        path: str,
        context: ImportContext,
        warnings: list[str],
        unknown: set[str],
    ) -> list[ToolCallTrace]:
        calls: list[ToolCallTrace] = []
        for index, raw in enumerate(steps):
            where = f"{path}.steps[{index}]"
            step = as_mapping(raw, path=where)
            self.unknown(
                step, _STEP_MAPPED, ignored=("text",), path=where, context=context,
                warnings=warnings, unknown=unknown,
            )
            kind = as_str(require(step, "kind", path=where), path=f"{where}.kind")
            if kind not in STEP_KINDS:
                raise TraceImportError(
                    f"{self.source}: {where} has kind {kind!r}; known kinds are "
                    f"{sorted(STEP_KINDS)}"
                )
            if kind == "message":
                continue  # transcript; declared in ``ignored_fields``

            outcome = as_str(step.get("outcome", "ok"), path=f"{where}.outcome")
            status = OUTCOME_STATUS.get(outcome)
            if status is None:
                raise TraceImportError(
                    f"{self.source}: {where} has outcome {outcome!r}; known outcomes "
                    f"are {sorted(OUTCOME_STATUS)}"
                )
            error = step.get("error")
            if error is not None:
                error = as_str(error, path=f"{where}.error")
            calls.append(
                tool_call(
                    name=as_str(
                        require(step, "tool", path=where), path=f"{where}.tool"
                    ),
                    arguments=as_mapping(step.get("input", {}), path=f"{where}.input"),
                    # Position among *tool* calls, not among steps.
                    order_index=len(calls),
                    status=status,
                    error=error,
                )
            )
        return calls

    def _failures(
        self,
        failures: list[Any],
        *,
        path: str,
        context: ImportContext,
        warnings: list[str],
        unknown: set[str],
    ) -> list[Any]:
        records = []
        for index, raw in enumerate(failures):
            where = f"{path}.failures[{index}]"
            failure = as_mapping(raw, path=where)
            self.unknown(
                failure, _FAILURE_MAPPED, path=where, context=context,
                warnings=warnings, unknown=unknown,
            )
            kind = as_str(require(failure, "type", path=where), path=f"{where}.type")
            records.append(
                error_record(
                    kind=kind,
                    message=as_str(failure.get("detail", ""), path=f"{where}.detail"),
                    origin=self.origin(kind, warnings=warnings),
                )
            )
        return records
