"""Baseline vs candidate: deltas, regression flags, clusters, and a summary.

This package is where the project's question is operationalized. Two runs of the
same suite come in — a **baseline** (known-good) and a **candidate** (the change)
— and what comes out is a per-case answer to *did a measurable behavioral property
get worse, by how much, and whose fault was it?*

The pipeline, one module per stage:

=======================  ====================================================
:mod:`.baseline`         load + score the reference side (and resolve its suite)
:mod:`.candidate`        load the side under test; decide what may be compared
:mod:`.diff`             pair by ``case_id``; compute what moved (no thresholds)
:mod:`.regression`       apply thresholds; raise ``RegressionFlag``s; attribute
:mod:`.clustering`       group flags by failure signature into ``FailureCluster``s
:mod:`.aggregate`        summarize and write ``results/comparisons/<id>/``
=======================  ====================================================

The stages are separate because the evidence and the verdict must be separable:
:mod:`.diff` can be tested without a threshold in sight, and :mod:`.regression`
can be tested on a hand-built :class:`~.diff.CaseDiff` without a run on disk.

Four properties the whole package holds to (``docs/REGRESSION_DETECTION.md``):

* **Deterministic.** No clock, no RNG, no network, no learned model. Two
  comparisons of the same two runs produce byte-identical artifacts.
* **Direction-aware.** Only *worse* moves flag. A faster, cheaper, more valid
  candidate raises nothing.
* **Total on pairing.** A case present on one side only is reported as
  ``unpaired``, never scored as a success or a failure.
* **Noise-aware.** A candidate record whose every error is ``origin="infra"`` has
  its flags withheld and recorded, not silently dropped — research question 9.
"""

from __future__ import annotations

from .aggregate import (
    COMPARISON_JSONL,
    COMPARISON_META_JSON,
    SUMMARY_JSON,
    ComparisonMeta,
    ComparisonResult,
    compare,
    compare_runs,
    default_comparison_id,
    write_comparison,
)
from .base import (
    BEHAVIORAL_FIELDS,
    COST_DECIMALS,
    DELTA_DECIMALS,
    IDENTITY_FIELDS,
    PROVENANCE_FIELDS,
    ComparisonError,
    RunSide,
    delta,
    metric_detail,
    metric_value,
    relative,
)
from .baseline import load_baseline, load_run_side, resolve_suite_path
from .candidate import Comparability, check_comparable, load_candidate
from .clustering import SIGNATURE_LABELS, cluster_failures, cluster_id_for
from .diff import CaseDiff, Pairing, changed_fields, diff_case, diff_sides, pair_cases
from .regression import (
    COST_REGRESSION,
    DEFAULT_THRESHOLDS,
    ERROR_RATE_REGRESSION,
    EVIDENCE_GROUNDING_REGRESSION,
    FINAL_ANSWER_REGRESSION,
    LATENCY_REGRESSION,
    PROPERTY_CATEGORY,
    REFUSAL_BEHAVIOR_SHIFT,
    REGRESSION_CATEGORIES,
    SEVERITY_ORDER,
    STRUCTURED_OUTPUT_REGRESSION,
    TOOL_CALL_REGRESSION,
    TRIGGER_METRICS,
    CaseOutcome,
    FlagEntry,
    Thresholds,
    Trigger,
    evaluate_flags,
)

__all__ = [
    # Errors and shared types
    "ComparisonError",
    "RunSide",
    "delta",
    "relative",
    "metric_value",
    "metric_detail",
    "BEHAVIORAL_FIELDS",
    "IDENTITY_FIELDS",
    "PROVENANCE_FIELDS",
    "DELTA_DECIMALS",
    "COST_DECIMALS",
    # Loading
    "load_baseline",
    "load_candidate",
    "load_run_side",
    "resolve_suite_path",
    "Comparability",
    "check_comparable",
    # Diffing
    "Pairing",
    "CaseDiff",
    "pair_cases",
    "changed_fields",
    "diff_case",
    "diff_sides",
    # Rules
    "Thresholds",
    "DEFAULT_THRESHOLDS",
    "Trigger",
    "FlagEntry",
    "CaseOutcome",
    "evaluate_flags",
    "PROPERTY_CATEGORY",
    "TRIGGER_METRICS",
    "REGRESSION_CATEGORIES",
    "SEVERITY_ORDER",
    "STRUCTURED_OUTPUT_REGRESSION",
    "TOOL_CALL_REGRESSION",
    "EVIDENCE_GROUNDING_REGRESSION",
    "REFUSAL_BEHAVIOR_SHIFT",
    "LATENCY_REGRESSION",
    "COST_REGRESSION",
    "ERROR_RATE_REGRESSION",
    "FINAL_ANSWER_REGRESSION",
    # Clustering
    "cluster_failures",
    "cluster_id_for",
    "SIGNATURE_LABELS",
    # Aggregation and artifacts
    "ComparisonMeta",
    "ComparisonResult",
    "compare",
    "compare_runs",
    "write_comparison",
    "default_comparison_id",
    "COMPARISON_JSONL",
    "SUMMARY_JSON",
    "COMPARISON_META_JSON",
]
