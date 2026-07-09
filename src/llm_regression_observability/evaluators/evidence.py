"""Retrieval / evidence scoring: citation presence, correctness, coverage, support.

A grounded answer is one whose claims are each backed by a snippet from a source
the case supplied. Four metrics take that apart, because the ways grounding fails
are not interchangeable:

``citation_presence``
    Fraction of claims that carry a ``source_id`` at all. Catches the answer that
    asserts without citing.
``citation_correctness``
    Fraction of citations naming a ``source_id`` that exists among the case's
    ``retrieval_docs``. Catches the fabricated source id.
``evidence_coverage``
    Fraction of *required* claims backed by a supporting snippet. Catches the
    dropped citation.
``unsupported_claim_count``
    Claims whose cited snippet does not support them (``supported`` is not
    ``True``). Catches the real-source-cited-for-the-wrong-claim case, which
    ``citation_correctness`` cannot see — ``doc-b`` exists, it just does not say
    what the answer claims it says.

**What "required" means.** If the case declares
``expected_properties["required_sources"]``, coverage is the fraction of those
ids that appear among the record's *supporting* sources — a candidate that drops
a citation is caught even though the claim it dropped is gone from the trace too.
Otherwise coverage is the fraction of the claims the answer actually made that
are supported. Both readings are "required claims backed by a supporting
snippet"; the first is the stricter, and a suite that cares should declare it.

**The ceiling.** ``supported`` is a recorded field of the trace, not a judgment
this module makes: nothing here reads a snippet and decides whether it entails a
claim. Deterministic entailment is out of scope (``docs/LIMITATIONS.md``); an
imported trace that never sets ``supported`` scores every claim as unsupported,
which is the conservative direction.
"""

from __future__ import annotations

from ..schemas import EvalCase, MetricResult, RetrievalTrace, RunRecord
from .base import metric, ratio

__all__ = [
    "claims",
    "unsupported_claims",
    "supported_source_ids",
    "known_source_ids",
    "evaluate",
]


def claims(record: RunRecord) -> list[RetrievalTrace]:
    """Retrieval entries that assert a claim (``claim`` is set and non-empty)."""
    return [source for source in record.retrieval_sources if source.claim]


def unsupported_claims(record: RunRecord) -> list[RetrievalTrace]:
    """Claims whose cited snippet does not support them.

    ``supported is None`` counts as unsupported: an unmarked claim has not been
    shown to be grounded, and the conservative reading is the safe one.
    """
    return [source for source in claims(record) if source.supported is not True]


def supported_source_ids(record: RunRecord) -> set[str]:
    """Source ids the record cites for a claim it marks as supported."""
    return {
        source.source_id
        for source in record.retrieval_sources
        if source.supported is True and source.source_id
    }


def known_source_ids(case: EvalCase) -> set[str]:
    """Source ids of the documents the case provided."""
    return {doc.source_id for doc in case.retrieval_docs or []}


def _required_sources(case: EvalCase) -> list[str] | None:
    required = case.expected_properties.get("required_sources")
    if isinstance(required, list):
        return [str(source_id) for source_id in required]
    return None


def evaluate(case: EvalCase, record: RunRecord) -> list[MetricResult]:
    """Score evidence grounding. Silent for cases with no retrieval dimension."""
    if not case.retrieval_docs and not record.retrieval_sources:
        return []

    sources = record.retrieval_sources
    stated = claims(record)
    unsupported = unsupported_claims(record)
    results: list[MetricResult] = []

    cited = [source for source in stated if source.source_id]
    presence = ratio(len(cited), len(stated), empty=1.0)
    results.append(
        metric(
            record.case_id,
            "citation_presence",
            presence,
            passed=presence == 1.0,
            detail={
                "claim_count": len(stated),
                "uncited_claims": [s.claim for s in stated if not s.source_id],
            },
        )
    )

    if case.retrieval_docs:
        known = known_source_ids(case)
        correct = [s for s in sources if s.source_id in known]
        correctness = ratio(len(correct), len(sources), empty=1.0)
        results.append(
            metric(
                record.case_id,
                "citation_correctness",
                correctness,
                passed=correctness == 1.0,
                detail={
                    "citation_count": len(sources),
                    "unknown_source_ids": sorted(
                        {s.source_id for s in sources if s.source_id not in known}
                    ),
                },
            )
        )

    required = _required_sources(case)
    if required is not None:
        supported = supported_source_ids(record)
        covered = [source_id for source_id in required if source_id in supported]
        coverage = ratio(len(covered), len(required), empty=1.0)
        coverage_detail = {
            "basis": "required_sources",
            "required": required,
            "uncovered": [s for s in required if s not in supported],
        }
    else:
        supported_claims = [s for s in stated if s.supported is True]
        coverage = ratio(len(supported_claims), len(stated), empty=1.0)
        coverage_detail = {
            "basis": "stated_claims",
            "claim_count": len(stated),
            "unsupported": [s.claim for s in unsupported],
        }
    results.append(
        metric(
            record.case_id,
            "evidence_coverage",
            coverage,
            passed=coverage == 1.0,
            detail=coverage_detail,
        )
    )

    results.append(
        metric(
            record.case_id,
            "unsupported_claim_count",
            len(unsupported),
            passed=not unsupported,
            detail={
                "claims": [
                    {"claim": s.claim, "source_id": s.source_id} for s in unsupported
                ]
            },
        )
    )
    return results
