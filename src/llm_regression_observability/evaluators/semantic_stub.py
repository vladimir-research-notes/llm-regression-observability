"""A deterministic stand-in for semantic similarity. **Not a learned judge.**

Comparing two free-text answers for *meaning* needs an embedding model or an
LLM judge. This framework is CPU-only, offline, and dependency-free, so it has
neither, and inventing a score that looked like one would be the exact dishonesty
``CLAUDE.md`` forbids.

What this module computes instead is a lexical overlap ratio: lowercase the text,
split it into alphanumeric tokens, and take the :mod:`difflib` similarity of the
two token sequences. That is a real, reproducible number with an obvious
weakness — it rates a paraphrase low and a fluent contradiction high. It is
useful for exactly one thing: noticing that a candidate's answer moved a long way
from its baseline's, as a *pointer* to a case a human should read.

Accordingly ``semantic_similarity_stub`` is listed in
:data:`~.base.PLACEHOLDER_METRICS`, and Milestone 6 must never raise a regression
flag from it. If a suite wants a real final-answer check it declares structural
``expected_properties`` and gets ``task_success`` (see :mod:`.exact`).
"""

from __future__ import annotations

import re

from ..schemas import EvalCase, MetricResult, RunRecord
from .base import metric, sequence_ratio

__all__ = [
    "normalize_tokens",
    "semantic_similarity_stub",
    "output_similarity",
    "evaluate",
]

_TOKEN = re.compile(r"[a-z0-9]+")


def normalize_tokens(text: str) -> list[str]:
    """Lowercase alphanumeric tokens. Punctuation and case never move the score."""
    return _TOKEN.findall(text.lower())


def semantic_similarity_stub(left: str, right: str) -> float:
    """Lexical token-overlap similarity in ``[0, 1]``. A stand-in, not a judgment."""
    return sequence_ratio(normalize_tokens(left), normalize_tokens(right))


def output_similarity(baseline: RunRecord, candidate: RunRecord) -> MetricResult:
    """Pairwise similarity of two runs' ``final_output``. Never raises a flag."""
    value = semantic_similarity_stub(baseline.final_output, candidate.final_output)
    return metric(
        candidate.case_id,
        "semantic_similarity_stub",
        value,
        passed=None,
        detail={"placeholder": True, "basis": "baseline_final_output"},
    )


def evaluate(case: EvalCase, record: RunRecord) -> list[MetricResult]:
    """Score against ``expected_properties["reference_answer"]``, if the case has it."""
    reference = case.expected_properties.get("reference_answer")
    if not isinstance(reference, str):
        return []
    value = semantic_similarity_stub(reference, record.final_output)
    return [
        metric(
            record.case_id,
            "semantic_similarity_stub",
            value,
            passed=None,
            detail={"placeholder": True, "basis": "reference_answer"},
        )
    ]
