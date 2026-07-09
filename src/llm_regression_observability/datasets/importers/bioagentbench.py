"""``bioagentbench`` — a benchmark episode: a task definition *and* an answer.

.. code-block:: json

    {"benchmark": "BioAgentBench", "format_version": "0.3", "split": "synthetic-dev",
     "model": "synthetic-bio-1", "recorded_at": "2026-06-01T12:00:00Z",
     "episodes": [{"task": {"id": "...", "instruction": "...",
                            "allowed_tools": ["search_corpus"],
                            "expected": {"required_sources": ["doc-a"]},
                            "documents": [{"doc_id": "doc-a", "title": "...",
                                           "text": "..."}]},
                   "answer": "...", "wallclock_seconds": 0.42,
                   "citations": [{"doc_id": "doc-a", "quote": "...", "claim": "...",
                                  "supports_claim": true}],
                   "tool_invocations": [{"name": "search_corpus", "params": {},
                                         "error": null}],
                   "tokens": {"prompt": 180, "completion": 55}, "usd": 0.0012,
                   "errors": []}]}

This is the only source that ships its own **task definition**, so it is the one
that yields :class:`~...schemas.EvalCase`s as well as records: ``task.expected``
becomes ``expected_properties``, ``task.documents`` become ``retrieval_docs``, and
the evaluators score the episode without a suite manifest anywhere in sight. That
is the milestone's claim made concrete — an external trace, scored by the existing
evaluators, unchanged.

Two mappings earn their keep.

**Seconds are not milliseconds.** ``wallclock_seconds`` is multiplied by 1000. A
unit mismatch is the least dramatic and most expensive bug an importer can have:
it does not raise, it does not look wrong, and it silently turns a 420 ms answer
into a 0.42 ms one — which is to say, into a latency improvement of three orders of
magnitude against any baseline. ``tests/test_integration_contracts.py`` pins the
conversion.

**``supports_claim`` is recorded, not judged.** It becomes
``RetrievalTrace.supported``. ``evaluators/evidence.py`` treats an unmarked claim as
unsupported, which is the conservative direction; nothing here reads a quote and
decides whether it entails a claim, and this importer does not start.

The task text, the corpus and the citations in the committed example are synthetic
and deliberately non-clinical: a benchmark shape, not biomedical content.
"""

from __future__ import annotations

from typing import Any

from ...schemas import EvalCase, RetrievalDoc, RunRecord
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
    retrieval,
    tool_call,
)

__all__ = ["BioAgentBenchImporter", "MS_PER_SECOND"]

#: The conversion this importer exists to get right.
MS_PER_SECOND = 1000.0

_DOCUMENT_MAPPED = ("benchmark", "format_version", "model", "recorded_at", "episodes")
_EPISODE_MAPPED = ("task", "answer", "citations", "tool_invocations",
                   "wallclock_seconds", "tokens", "usd", "errors")
_TASK_MAPPED = ("id", "instruction", "allowed_tools", "expected", "documents")
_DOC_MAPPED = ("doc_id", "title", "text")
_CITATION_MAPPED = ("doc_id", "quote", "claim", "supports_claim")
_INVOCATION_MAPPED = ("name", "params", "error")
_ERROR_MAPPED = ("type", "message")


class BioAgentBenchImporter(TraceImporter):
    """Map benchmark episodes onto ``(EvalCase, RunRecord)`` pairs."""

    source = "bioagentbench"
    source_schema = "BioAgentBench"
    supported_source_versions = frozenset({"0.3"})
    ignored_fields = ("split",)
    absent_fields = ("prompt_version", "tool_version")
    error_origins = {
        "grader_error": "infra",
        "harness_timeout": "infra",
        "tool_error": "app",
        "invalid_answer": "app",
    }

    def parse(self, document: Any, context: ImportContext) -> ImportResult:
        doc = as_mapping(document, path=self.source_schema)
        warnings: list[str] = []
        unknown: set[str] = set()
        self.unknown(
            doc, _DOCUMENT_MAPPED, ignored=("split",), path="document",
            context=context, warnings=warnings, unknown=unknown,
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
        prompt_version, tool_version = context.versions()

        records: list[RunRecord] = []
        cases: list[EvalCase] = []
        for index, raw in enumerate(as_list(doc.get("episodes", []), path="episodes")):
            path = f"episodes[{index}]"
            episode = as_mapping(raw, path=path)
            self.unknown(
                episode, _EPISODE_MAPPED, path=path, context=context,
                warnings=warnings, unknown=unknown,
            )
            case = self._case(
                as_mapping(require(episode, "task", path=path), path=f"{path}.task"),
                path=f"{path}.task", context=context, warnings=warnings,
                unknown=unknown,
            )
            cases.append(case)
            records.append(
                self._record(
                    episode, case_id=case.case_id, path=path, model_id=model_id,
                    timestamp=timestamp, prompt_version=prompt_version,
                    tool_version=tool_version, context=context, warnings=warnings,
                    unknown=unknown,
                )
            )

        return self.result(
            source_version=version, records=records, cases=cases,
            warnings=warnings, unknown=unknown,
        )

    def _case(
        self,
        task: dict[str, Any],
        *,
        path: str,
        context: ImportContext,
        warnings: list[str],
        unknown: set[str],
    ) -> EvalCase:
        self.unknown(
            task, _TASK_MAPPED, path=path, context=context, warnings=warnings,
            unknown=unknown,
        )
        docs: list[RetrievalDoc] = []
        documents = as_list(task.get("documents", []), path=f"{path}.documents")
        for index, raw in enumerate(documents):
            where = f"{path}.documents[{index}]"
            source_doc = as_mapping(raw, path=where)
            self.unknown(
                source_doc, _DOC_MAPPED, path=where, context=context,
                warnings=warnings, unknown=unknown,
            )
            docs.append(
                RetrievalDoc(
                    source_id=as_str(require(source_doc, "doc_id", path=where),
                                     path=f"{where}.doc_id"),
                    text=as_str(source_doc.get("text", ""), path=f"{where}.text"),
                    title=source_doc.get("title"),
                )
            )
        allowed = task.get("allowed_tools")
        return build_case(
            case_id=as_str(require(task, "id", path=path), path=f"{path}.id"),
            version=self.source_schema,
            prompt=as_str(task.get("instruction", ""), path=f"{path}.instruction"),
            # Every BioAgentBench episode is a grounded-answer task.
            risk_category="retrieval",
            expected_properties=as_mapping(
                task.get("expected", {}), path=f"{path}.expected"
            ),
            allowed_tools=None if allowed is None else [
                as_str(name, path=f"{path}.allowed_tools[]") for name in
                as_list(allowed, path=f"{path}.allowed_tools")
            ],
            retrieval_docs=docs or None,
            tags=["imported", self.source],
        )

    def _record(
        self,
        episode: dict[str, Any],
        *,
        case_id: str,
        path: str,
        model_id: str,
        timestamp: str,
        prompt_version: str,
        tool_version: str,
        context: ImportContext,
        warnings: list[str],
        unknown: set[str],
    ) -> RunRecord:
        seconds = episode.get("wallclock_seconds")
        latency = self.latency(
            None if seconds is None
            else as_float(seconds, path=f"{path}.wallclock_seconds") * MS_PER_SECOND,
            case_id=case_id, context=context, warnings=warnings,
        )
        tokens = as_mapping(episode.get("tokens", {}), path=f"{path}.tokens")

        sources = []
        citations = as_list(episode.get("citations", []), path=f"{path}.citations")
        for index, raw in enumerate(citations):
            where = f"{path}.citations[{index}]"
            citation = as_mapping(raw, path=where)
            self.unknown(
                citation, _CITATION_MAPPED, path=where, context=context,
                warnings=warnings, unknown=unknown,
            )
            supported = citation.get("supports_claim")
            if supported is not None and not isinstance(supported, bool):
                raise TraceImportError(f"{where}.supports_claim: expected a boolean")
            sources.append(
                retrieval(
                    source_id=as_str(require(citation, "doc_id", path=where),
                                     path=f"{where}.doc_id"),
                    snippet=as_str(citation.get("quote", ""), path=f"{where}.quote"),
                    claim=citation.get("claim"),
                    supported=supported,
                )
            )

        calls = []
        invocations = as_list(
            episode.get("tool_invocations", []), path=f"{path}.tool_invocations"
        )
        for index, raw in enumerate(invocations):
            where = f"{path}.tool_invocations[{index}]"
            invocation = as_mapping(raw, path=where)
            self.unknown(
                invocation, _INVOCATION_MAPPED, path=where, context=context,
                warnings=warnings, unknown=unknown,
            )
            error = invocation.get("error")
            status = "ok" if error is None else "failed"
            if error is not None:
                error = as_str(error, path=f"{where}.error")
            calls.append(
                tool_call(
                    name=as_str(require(invocation, "name", path=where),
                                path=f"{where}.name"),
                    arguments=as_mapping(invocation.get("params", {}),
                                         path=f"{where}.params"),
                    order_index=index,
                    status=status,
                    error=error,
                )
            )

        errors = []
        for index, raw in enumerate(
            as_list(episode.get("errors", []), path=f"{path}.errors")
        ):
            where = f"{path}.errors[{index}]"
            entry = as_mapping(raw, path=where)
            self.unknown(
                entry, _ERROR_MAPPED, path=where, context=context, warnings=warnings,
                unknown=unknown,
            )
            kind = as_str(require(entry, "type", path=where), path=f"{where}.type")
            errors.append(
                error_record(
                    kind=kind,
                    message=as_str(entry.get("message", ""), path=f"{where}.message"),
                    origin=self.origin(kind, warnings=warnings),
                )
            )

        return build_record(
            run_id=context.run_id,
            case_id=case_id,
            app_version=context.app_version,
            model_id=model_id,
            prompt_version=prompt_version,
            tool_version=tool_version,
            timestamp=timestamp,
            input_tokens_estimate=as_int(tokens.get("prompt", 0),
                                         path=f"{path}.tokens.prompt"),
            output_tokens_estimate=as_int(tokens.get("completion", 0),
                                          path=f"{path}.tokens.completion"),
            latency_ms=latency,
            cost_estimate_usd=as_float(episode.get("usd", 0.0), path=f"{path}.usd"),
            final_output=as_str(episode.get("answer", ""), path=f"{path}.answer"),
            tool_calls=calls,
            retrieval_sources=sources,
            errors=errors,
        )
