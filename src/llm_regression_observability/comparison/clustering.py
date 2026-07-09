"""Grouping correlated failures so one root cause reads as one finding.

Eight cases that each lost a required JSON field are one bug, not eight. A report
that lists them separately buries the signal it exists to surface, and a report
that lists only the count hides which cases to look at. So flags are grouped by
their **failure signature** — the specific way the candidate broke, as recorded on
the :class:`~.regression.Trigger` that raised the flag.

The signature is a short, deterministic string: ``schema_invalid``,
``disallowed_tool:shell_exec``, ``coverage_drop``, ``app_error:tool_retry``. It
carries the offending *symbol* where one exists (a tool name, a property name),
because "the model started calling ``shell_exec``" is a root cause and "a tool was
wrong" is not. It never carries model-generated prose, so a cluster label is safe
to print before the Milestone 9 redactor runs.

Clustering is stdlib-only and has no notion of distance or similarity. A metric
learned from the data would have to be fitted, and a fitted grouping cannot be
reproduced from the artifact alone — the same objection this project raises to
learned judges. Two flags cluster when they share ``(category, signature)``,
period. A case that raised flags in two categories appears in two clusters: it
really did fail in two ways.

Clusters are emitted in :data:`~.regression.REGRESSION_CATEGORIES` order, then by
signature, and their ``case_ids`` are sorted — so ``summary.json`` is byte-stable.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..schemas import FailureCluster
from .regression import REGRESSION_CATEGORIES, FlagEntry

__all__ = ["SIGNATURE_LABELS", "cluster_failures", "cluster_id_for"]

#: A human phrase per signature *kind* (the part before any ``:symbol`` suffix).
SIGNATURE_LABELS: dict[str, str] = {
    "parse_failure": "output stopped parsing as JSON",
    "schema_invalid": "output no longer validates against the case schema",
    "disallowed_tool": "a tool outside allowed_tools was called",
    "missing_tool": "an expected tool call disappeared",
    "extra_tool": "an unexpected tool call appeared",
    "sequence_drift": "the tool-call sequence changed",
    "argument_drift": "tool arguments drifted",
    "new_unsupported_claim": "a new claim appeared with no supporting source",
    "coverage_drop": "evidence coverage dropped",
    "citation_incorrect": "a citation named a source the case never provided",
    "citation_missing": "claims went uncited",
    "claim_lost_support": "a previously supported claim lost its support",
    "under_refusal": "the candidate answered a request the baseline refused",
    "over_refusal": "the candidate refused a request the baseline answered",
    "refusal_decision_flip": "the refuse-or-answer decision flipped",
    "risk_label_shift": "the assigned risk label moved",
    "latency_increase": "latency rose past both floors",
    "cost_increase": "estimated cost rose past the tolerance",
    "app_error": "a new app-origin error appeared",
    "retry_increase": "tool retries increased",
    "property": "a declared expected_property stopped holding",
}

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _split(signature: str) -> tuple[str, str | None]:
    """``"disallowed_tool:shell_exec"`` -> ``("disallowed_tool", "shell_exec")``."""
    kind, sep, symbol = signature.partition(":")
    return kind, (symbol if sep else None)


def cluster_id_for(category: str, signature: str) -> str:
    """A stable, readable cluster id, safe as a filename or an HTML anchor.

    Derived from the content rather than an ordinal, so adding a case to a run
    never renumbers the clusters a previous report referred to.
    """
    return f"{category}__{_UNSAFE.sub('-', signature)}"


def _label(signature: str, count: int) -> str:
    kind, symbol = _split(signature)
    phrase = SIGNATURE_LABELS.get(kind, kind.replace("_", " "))
    if symbol:
        phrase = f"{phrase} ({symbol})"
    cases = "case" if count == 1 else "cases"
    return f"{phrase} — {count} {cases}"


def cluster_failures(entries: Iterable[FlagEntry]) -> list[FailureCluster]:
    """Group flags by ``(category, signature)`` into deterministic clusters."""
    grouped: dict[tuple[str, str], set[str]] = {}
    for entry in entries:
        grouped.setdefault((entry.flag.category, entry.signature), set()).add(
            entry.case_id
        )

    order = {category: i for i, category in enumerate(REGRESSION_CATEGORIES)}
    keys = sorted(grouped, key=lambda key: (order.get(key[0], len(order)), key[1]))
    return [
        FailureCluster(
            cluster_id=cluster_id_for(category, signature),
            label=_label(signature, len(grouped[(category, signature)])),
            category=category,
            case_ids=sorted(grouped[(category, signature)]),
        )
        for category, signature in keys
    ]
