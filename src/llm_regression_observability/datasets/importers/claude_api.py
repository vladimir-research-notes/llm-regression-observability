"""``claude_api`` — *recorded* Messages-API responses. Nothing here calls an API.

.. code-block:: json

    {"provider": "anthropic", "api_version": "2023-06-01",
     "recorded_at": "2026-06-01T12:00:00Z",
     "records": [{"case_id": "...", "prompt": "...", "latency_ms": 310.0,
                  "request": {"model": "...", "max_tokens": 256},
                  "response": {"id": "msg_...", "model": "...",
                               "stop_reason": "tool_use",
                               "content": [{"type": "text", "text": "..."},
                                           {"type": "tool_use", "id": "toolu_...",
                                            "name": "search", "input": {}}],
                               "usage": {"input_tokens": 40, "output_tokens": 18}}}]}

This importer is a **contract, not a client**. It reads a document somebody else
recorded. The paid-API adapter — dry-run mode, token estimator, hard max-cost guard,
response cache, explicit opt-in — is Milestone 11 and lives in ``adapters/``. Nothing
in this module opens a socket, and the committed example is hand-written and synthetic:
``CLAUDE.md`` forbids committing a real API response, and a recorded response is the
single most likely place for a credential to enter this repository. Which is why
:func:`.base.finalize` redacts every imported record and refuses one whose join key
holds a secret.

Two mappings carry the weight.

**Content is a list of blocks, not a string.** ``text`` blocks concatenate (joined by
a blank line) into ``final_output``; ``tool_use`` blocks become
:class:`~...schemas.ToolCallTrace`s in block order. A block type this importer has
never seen refuses in strict mode rather than vanishing — a dropped block is a
dropped tool call, and a dropped tool call is a ``tool_call_regression`` that never
fires.

**A response carries no duration.** The API returns tokens and a stop reason; the
*client* is what held a stopwatch. So ``latency_ms`` sits beside the response, not
inside it, and a record without one is refused in strict mode
(:data:`~.base.LATENCY_HAZARD`) rather than imported as ``0.0``. That refusal is the
whole reason this source is in the contract set: it is the one whose natural shape
tempts an importer into inventing a measurement.

``stop_reason`` is mapped only where it denotes a failure (``max_tokens`` → a
truncated-output error). A ``refusal`` stop reason is deliberately *not* turned into
``parsed_output["refused"]``: this source carries no risk label, and
``evaluators/refusal.py`` will read the text and record
``decision_source="lexical_fallback"``, which is the honest description of what
happened.
"""

from __future__ import annotations

from typing import Any

from ...schemas import EvalCase, RunRecord, ToolCallTrace
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
    tool_call,
)

__all__ = ["ClaudeApiImporter", "CONTENT_BLOCK_TYPES", "STOP_REASON_ERRORS"]

#: Content block types this importer maps. Anything else refuses in strict mode.
CONTENT_BLOCK_TYPES: frozenset[str] = frozenset({"text", "tool_use", "thinking"})

#: Stop reasons that denote a failed generation, and the error kind they become.
STOP_REASON_ERRORS: dict[str, str] = {"max_tokens": "truncated_output"}

_DOCUMENT_MAPPED = ("provider", "api_version", "recorded_at", "records")
_RECORD_MAPPED = ("case_id", "prompt", "request", "response", "latency_ms", "cost_usd")
_REQUEST_MAPPED = ("model", "max_tokens", "system", "tools")
_RESPONSE_MAPPED = ("id", "model", "role", "type", "stop_reason", "stop_sequence",
                    "content", "usage")
_BLOCK_MAPPED = ("type", "text", "id", "name", "input", "thinking", "signature")


class ClaudeApiImporter(TraceImporter):
    """Map recorded Messages-API responses onto ``RunRecord``s. Never calls an API."""

    source = "claude_api"
    source_schema = "anthropic/messages"
    supported_source_versions = frozenset({"2023-06-01"})
    ignored_fields = (
        "response.id",
        "response.role",
        "response.type",
        "response.stop_sequence",
        "request.system",
        "request.tools",
        "request.max_tokens",
        "content[].id",
        "content[].signature",
        "content[].thinking",
    )
    #: The API response carries neither of these. Cost the executor's ``CostModel``
    #: supplies; latency nothing can, which is why it is refused rather than zeroed.
    absent_fields = ("latency_ms", "cost_estimate_usd", "prompt_version",
                     "tool_version", "retrieval_sources")
    error_origins = {
        "truncated_output": "app",
        "overloaded_error": "infra",
        "api_error": "infra",
        "rate_limit_error": "infra",
    }

    def parse(self, document: Any, context: ImportContext) -> ImportResult:
        doc = as_mapping(document, path=self.source_schema)
        warnings: list[str] = []
        unknown: set[str] = set()
        self.unknown(
            doc, _DOCUMENT_MAPPED, path="document", context=context,
            warnings=warnings, unknown=unknown,
        )
        version = as_str(doc.get("api_version", ""), path="api_version")
        check_source_version(
            version, supported=self.supported_source_versions, source=self.source,
            context=context, warnings=warnings,
        )
        _, timestamp = context.stamp(
            timestamp=as_str(doc.get("recorded_at", ""), path="recorded_at")
        )
        prompt_version, tool_version = context.versions()

        records: list[RunRecord] = []
        cases: list[EvalCase] = []
        for index, raw in enumerate(as_list(doc.get("records", []), path="records")):
            path = f"records[{index}]"
            entry = as_mapping(raw, path=path)
            self.unknown(
                entry, _RECORD_MAPPED, path=path, context=context, warnings=warnings,
                unknown=unknown,
            )
            case_id = as_str(
                require(entry, "case_id", path=path), path=f"{path}.case_id"
            )
            cases.append(
                # No expected properties: an API response is an answer, not a task
                # definition. ``task_success`` comes back vacuous and says so.
                build_case(
                    case_id=case_id,
                    version=self.source_schema,
                    prompt=as_str(entry.get("prompt", ""), path=f"{path}.prompt"),
                    risk_category="benign",
                    tags=["imported", self.source],
                )
            )
            records.append(
                self._record(
                    entry, case_id=case_id, path=path, timestamp=timestamp,
                    prompt_version=prompt_version, tool_version=tool_version,
                    context=context, warnings=warnings, unknown=unknown,
                )
            )

        return self.result(
            source_version=version, records=records, cases=cases,
            warnings=warnings, unknown=unknown,
        )

    def _record(
        self,
        entry: dict[str, Any],
        *,
        case_id: str,
        path: str,
        timestamp: str,
        prompt_version: str,
        tool_version: str,
        context: ImportContext,
        warnings: list[str],
        unknown: set[str],
    ) -> RunRecord:
        request = as_mapping(entry.get("request", {}), path=f"{path}.request")
        self.unknown(
            request, _REQUEST_MAPPED, path=f"{path}.request", context=context,
            warnings=warnings, unknown=unknown,
        )
        response = as_mapping(require(entry, "response", path=path),
                              path=f"{path}.response")
        self.unknown(
            response, _RESPONSE_MAPPED, path=f"{path}.response", context=context,
            warnings=warnings, unknown=unknown,
        )

        model_id, _ = context.stamp(
            model_id=as_str(
                response.get("model", "") or request.get("model", ""),
                path=f"{path}.response.model",
            )
        )

        texts, calls = self._content(
            as_list(response.get("content", []), path=f"{path}.response.content"),
            path=f"{path}.response", context=context, warnings=warnings,
            unknown=unknown,
        )

        errors = []
        stop_reason = as_str(response.get("stop_reason", "") or "",
                             path=f"{path}.response.stop_reason")
        kind = STOP_REASON_ERRORS.get(stop_reason)
        if kind is not None:
            errors.append(
                error_record(
                    kind=kind,
                    message=f"generation stopped on {stop_reason!r}",
                    origin=self.origin(kind, warnings=warnings),
                )
            )

        usage = as_mapping(response.get("usage", {}), path=f"{path}.response.usage")
        latency_ms = entry.get("latency_ms")
        return build_record(
            run_id=context.run_id,
            case_id=case_id,
            app_version=context.app_version,
            model_id=model_id,
            prompt_version=prompt_version,
            tool_version=tool_version,
            timestamp=timestamp,
            input_tokens_estimate=as_int(usage.get("input_tokens", 0),
                                         path=f"{path}.response.usage.input_tokens"),
            output_tokens_estimate=as_int(usage.get("output_tokens", 0),
                                          path=f"{path}.response.usage.output_tokens"),
            latency_ms=self.latency(
                None if latency_ms is None
                else as_float(latency_ms, path=f"{path}.latency_ms"),
                case_id=case_id, context=context, warnings=warnings,
            ),
            # Left at 0.0 on purpose: the response carries no price. The executor's
            # ``CostModel`` prices it from the token counts, and ``check_comparable``
            # already disables the cost rule across two different price lists.
            cost_estimate_usd=as_float(entry.get("cost_usd", 0.0),
                                       path=f"{path}.cost_usd"),
            final_output="\n\n".join(texts),
            tool_calls=calls,
            errors=errors,
        )

    def _content(
        self,
        blocks: list[Any],
        *,
        path: str,
        context: ImportContext,
        warnings: list[str],
        unknown: set[str],
    ) -> tuple[list[str], list[ToolCallTrace]]:
        texts: list[str] = []
        calls: list[ToolCallTrace] = []
        for index, raw in enumerate(blocks):
            where = f"{path}.content[{index}]"
            block = as_mapping(raw, path=where)
            self.unknown(
                block, _BLOCK_MAPPED, ignored=("id", "signature"), path=where,
                context=context, warnings=warnings, unknown=unknown,
            )
            block_type = as_str(
                require(block, "type", path=where), path=f"{where}.type"
            )
            if block_type not in CONTENT_BLOCK_TYPES:
                raise TraceImportError(
                    f"{self.source}: {where} has type {block_type!r}; known block "
                    f"types are {sorted(CONTENT_BLOCK_TYPES)}. A block this importer "
                    "cannot read may be a tool call, and a dropped tool call is a "
                    "regression that never fires"
                )
            if block_type == "text":
                texts.append(as_str(block.get("text", ""), path=f"{where}.text"))
            elif block_type == "tool_use":
                calls.append(
                    tool_call(
                        name=as_str(require(block, "name", path=where),
                                    path=f"{where}.name"),
                        arguments=as_mapping(block.get("input", {}),
                                             path=f"{where}.input"),
                        order_index=len(calls),
                        status="ok",
                        error=None,
                    )
                )
            else:
                # ``thinking``: reasoning text. Not an answer, not a tool call, and
                # nothing any scorer reads. Dropped; named in ``ignored_fields``.
                continue
        return texts, calls
