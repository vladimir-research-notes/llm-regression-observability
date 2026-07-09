"""``bioscientist_os`` — a multi-node workflow, flattened into one record per run.

.. code-block:: json

    {"system": "BioScientistOS", "trace_version": "2026.1",
     "workflow_id": "wf-synthetic-1", "recorded_at": "2026-06-01T12:00:00Z",
     "runs": [{"case": "...", "prompt": "...", "status": "completed",
               "nodes": [{"type": "llm", "model": "synthetic-planner-1",
                          "duration_ms": 120.0, "usd": 0.0008,
                          "tokens": {"in": 90, "out": 20}},
                         {"type": "tool", "tool": "run_query", "arguments": {},
                          "duration_ms": 40.0, "status": "ok"}],
               "output": {"text": "...", "structured": {}}}]}

This source is the lossy one, and it is in the contract set precisely so the loss
has somewhere to be written down.

**One run, many models.** A workflow calls a planner and then a writer.
``RunRecord.model_id`` is a single string, because every artifact downstream keys
provenance by it. So a multi-model run is recorded as ``multi:<names>`` (sorted,
``+``-joined) and a warning says that per-model attribution was lost. The
alternative — silently keeping the first or the last model — would produce a
``model_id`` that is true of one node and false of the record.

**Duration and cost are per node, and additive.** ``latency_ms`` is the sum of the
nodes' ``duration_ms``; ``cost_estimate_usd`` is the sum of their ``usd``. That is a
*claim about a serial workflow*: if the source ever runs nodes concurrently, the sum
overstates wall-clock. The claim is stated here rather than buried, and a workflow
that parallelizes needs a ``duration_ms`` on the run, not on the nodes.

**A failed workflow is an application error.** ``status: "failed"`` becomes an
``ErrorRecord(kind="workflow_failed", origin="app")``. The system reports no error
taxonomy, so there is nothing to look up in :attr:`error_origins`, and ``app`` is
the direction that raises a flag rather than withholding one.

The example is a synthetic query-and-summarize workflow. Nothing biomedical, nothing
clinical — a shape, not a domain.
"""

from __future__ import annotations

from typing import Any

from ...schemas import EvalCase, RunRecord, ToolCallTrace
from .base import (
    UNKNOWN_VERSION,
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

__all__ = ["BioScientistOsImporter", "NODE_TYPES", "MULTI_MODEL_PREFIX"]

#: Node types this importer understands.
NODE_TYPES: frozenset[str] = frozenset({"llm", "tool"})

#: What a ``model_id`` looks like when one run used more than one model.
MULTI_MODEL_PREFIX = "multi:"

#: ``status`` values on a run.
_RUN_STATUSES: frozenset[str] = frozenset({"completed", "failed"})

_DOCUMENT_MAPPED = ("system", "trace_version", "recorded_at", "runs")
_RUN_MAPPED = ("case", "prompt", "nodes", "output", "status")
_NODE_MAPPED = ("type", "model", "tool", "arguments", "duration_ms", "usd", "tokens",
                "status", "error")
_OUTPUT_MAPPED = ("text", "structured")


class BioScientistOsImporter(TraceImporter):
    """Flatten a workflow run into one ``RunRecord``, recording what flattening cost."""

    source = "bioscientist_os"
    source_schema = "BioScientistOS"
    supported_source_versions = frozenset({"2026.1"})
    ignored_fields = ("workflow_id",)
    absent_fields = ("prompt_version", "tool_version", "retrieval_sources")
    error_origins = {"workflow_failed": "app"}

    def parse(self, document: Any, context: ImportContext) -> ImportResult:
        doc = as_mapping(document, path=self.source_schema)
        warnings: list[str] = []
        unknown: set[str] = set()
        self.unknown(
            doc, _DOCUMENT_MAPPED, ignored=("workflow_id",), path="document",
            context=context, warnings=warnings, unknown=unknown,
        )
        version = as_str(doc.get("trace_version", ""), path="trace_version")
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
        for index, raw in enumerate(as_list(doc.get("runs", []), path="runs")):
            path = f"runs[{index}]"
            run = as_mapping(raw, path=path)
            self.unknown(
                run, _RUN_MAPPED, path=path, context=context, warnings=warnings,
                unknown=unknown,
            )
            case_id = as_str(require(run, "case", path=path), path=f"{path}.case")
            cases.append(
                # The source carries a prompt but no expected properties, so the
                # case declares nothing to check and ``task_success`` comes back
                # vacuous (``passed=None``) rather than a fabricated 1.0-with-a-verdict.
                build_case(
                    case_id=case_id,
                    version=self.source_schema,
                    prompt=as_str(run.get("prompt", ""), path=f"{path}.prompt"),
                    risk_category="agent",
                    tags=["imported", self.source],
                )
            )
            records.append(
                self._record(
                    run, case_id=case_id, path=path, timestamp=timestamp,
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
        run: dict[str, Any],
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
        models: list[str] = []
        calls: list[ToolCallTrace] = []
        duration = 0.0
        cost = 0.0
        input_tokens = 0
        output_tokens = 0
        has_duration = False

        nodes = as_list(run.get("nodes", []), path=f"{path}.nodes")
        for index, raw in enumerate(nodes):
            where = f"{path}.nodes[{index}]"
            node = as_mapping(raw, path=where)
            self.unknown(
                node, _NODE_MAPPED, path=where, context=context, warnings=warnings,
                unknown=unknown,
            )
            node_type = as_str(require(node, "type", path=where), path=f"{where}.type")
            if node_type not in NODE_TYPES:
                raise TraceImportError(
                    f"{self.source}: {where} has type {node_type!r}; known types are "
                    f"{sorted(NODE_TYPES)}"
                )
            if "duration_ms" in node:
                duration += as_float(node["duration_ms"], path=f"{where}.duration_ms")
                has_duration = True
            cost += as_float(node.get("usd", 0.0), path=f"{where}.usd")

            if node_type == "llm":
                models.append(as_str(node.get("model", ""), path=f"{where}.model"))
                tokens = as_mapping(node.get("tokens", {}), path=f"{where}.tokens")
                input_tokens += as_int(tokens.get("in", 0), path=f"{where}.tokens.in")
                output_tokens += as_int(
                    tokens.get("out", 0), path=f"{where}.tokens.out"
                )
                continue

            status = as_str(node.get("status", "ok"), path=f"{where}.status")
            if status not in {"ok", "failed", "skipped"}:
                raise TraceImportError(
                    f"{self.source}: {where} has status {status!r}"
                )
            error = node.get("error")
            if error is not None:
                error = as_str(error, path=f"{where}.error")
            calls.append(
                tool_call(
                    name=as_str(
                        require(node, "tool", path=where), path=f"{where}.tool"
                    ),
                    arguments=as_mapping(node.get("arguments", {}),
                                         path=f"{where}.arguments"),
                    order_index=len(calls),
                    status=status,
                    error=error,
                )
            )

        latency = self.latency(
            duration if has_duration else None,
            case_id=case_id, context=context, warnings=warnings,
        )

        output = as_mapping(run.get("output", {}), path=f"{path}.output")
        self.unknown(
            output, _OUTPUT_MAPPED, path=f"{path}.output", context=context,
            warnings=warnings, unknown=unknown,
        )

        status = as_str(run.get("status", "completed"), path=f"{path}.status")
        if status not in _RUN_STATUSES:
            raise TraceImportError(
                f"{self.source}: {path}.status is {status!r}; known statuses are "
                f"{sorted(_RUN_STATUSES)}"
            )
        errors = []
        if status == "failed":
            errors.append(
                error_record(
                    kind="workflow_failed",
                    message=f"workflow run for case {case_id} reported status 'failed'",
                    origin=self.origin("workflow_failed", warnings=warnings),
                )
            )

        return build_record(
            run_id=context.run_id,
            case_id=case_id,
            app_version=context.app_version,
            model_id=self._model_id(models, case_id=case_id, context=context,
                                    warnings=warnings),
            prompt_version=prompt_version,
            tool_version=tool_version,
            timestamp=timestamp,
            input_tokens_estimate=input_tokens,
            output_tokens_estimate=output_tokens,
            latency_ms=round(latency, 6),
            cost_estimate_usd=round(cost, 8),
            final_output=as_str(output.get("text", ""), path=f"{path}.output.text"),
            parsed_output=output.get("structured"),
            tool_calls=calls,
            errors=errors,
        )

    def _model_id(
        self,
        models: list[str],
        *,
        case_id: str,
        context: ImportContext,
        warnings: list[str],
    ) -> str:
        """One string for a run that may have used several models. See the docstring."""
        distinct = sorted({name for name in models if name})
        if context.model_id:
            return context.model_id
        if not distinct:
            return UNKNOWN_VERSION
        if len(distinct) == 1:
            return distinct[0]
        warnings.append(
            f"{self.source}: case {case_id!r} ran {len(distinct)} models "
            f"({', '.join(distinct)}); RunRecord carries one model_id, recorded as "
            f"{MULTI_MODEL_PREFIX}{'+'.join(distinct)}. Per-model attribution is lost "
            "on import and cannot be recovered downstream"
        )
        return MULTI_MODEL_PREFIX + "+".join(distinct)
