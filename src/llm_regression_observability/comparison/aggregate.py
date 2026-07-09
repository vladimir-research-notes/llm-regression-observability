"""Aggregation and artifacts: two runs in, three byte-stable files out.

``compare()`` is the whole engine in one call — pair, diff, flag, cluster,
summarize — and ``write_comparison()`` puts the result under
``results/comparisons/<comparison_id>/``:

``comparison.jsonl``
    One :class:`~..schemas.ComparisonRecord` per **paired** case, in the
    baseline's run order.
``summary.json``
    One :class:`~..schemas.ReportSummary`: the headline the report generator
    (Milestone 7) renders — flags by category, latency percentiles, cost total,
    failure clusters.
``comparison_meta.json``
    One :class:`ComparisonMeta`: everything needed to *audit* the comparison
    rather than read it — which runs, which thresholds, which cases went
    unpaired, which degradations were attributed to infrastructure and excluded,
    whether cost was comparable at all, and the digest of ``comparison.jsonl``.

The split is deliberate. ``summary.json`` answers "what regressed"; the sidecar
answers "what did the engine decline to say, and why". An unpaired case and an
infra-suppressed case are both invisible in the summary's counts, and both are
exactly the kind of silence that makes an evaluation harness lie. They get a file.

**The seven deltas.** ``ComparisonRecord`` carries one delta per dimension of the
taxonomy. Each is ``candidate - baseline`` on that dimension's headline metric,
and ``0.0`` when the case never declared the dimension (an absent metric is *not
applicable*, never zero — the flag, not the delta, is the claim):

======================= ======================================================
``score_delta``         ``task_success``
``json_validity_delta`` ``json_schema_validity``, else ``json_parse_success``
``tool_call_delta``     ``tool_sequence_similarity``, else ``allowed_tool_ratio``
``refusal_delta``       ``refusal_match``, else ``risk_label_match``
``evidence_delta``      ``evidence_coverage``
``latency_delta``       recorded ``latency_ms`` (native units)
``cost_delta``          recorded ``cost_estimate_usd`` (native units)
======================= ======================================================

Argument drift and a refusal-decision flip move no delta — they are pairwise, and
they show up as flags. A zero delta is therefore not a claim of "nothing changed";
``regression_flags`` is.

No clock, no RNG, no network. Two comparisons of the same two runs produce
byte-identical files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__
from ..evaluators import cost_stats, latency_stats
from ..schemas import ComparisonRecord, ReportSummary, RunRecord, Schema
from ..traces.storage import (
    file_sha256,
    render_jsonl,
    sha256_hex,
    write_json,
    write_jsonl,
)
from .base import COST_DECIMALS, ComparisonError, RunSide, delta
from .baseline import load_baseline
from .candidate import Comparability, check_comparable, load_candidate
from .clustering import cluster_failures
from .diff import CaseDiff, Pairing, diff_sides, pair_cases
from .regression import (
    REGRESSION_CATEGORIES,
    SEVERITY_ORDER,
    CaseOutcome,
    FlagEntry,
    Thresholds,
    evaluate_flags,
)

__all__ = [
    "COMPARISON_JSONL",
    "SUMMARY_JSON",
    "COMPARISON_META_JSON",
    "ComparisonMeta",
    "ComparisonResult",
    "default_comparison_id",
    "compare",
    "write_comparison",
    "compare_runs",
]

COMPARISON_JSONL = "comparison.jsonl"
SUMMARY_JSON = "summary.json"
COMPARISON_META_JSON = "comparison_meta.json"

#: ``comparison_id`` doubles as a directory name; keep it one safe path segment.
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def default_comparison_id(baseline: RunSide, candidate: RunSide) -> str:
    """``<suite>-<baseline_version>-vs-<candidate_version>`` — deterministic."""
    return f"{baseline.suite_id}-{baseline.app_version}-vs-{candidate.app_version}"


def _check_id(comparison_id: str) -> str:
    if not _SAFE_ID.match(comparison_id):
        raise ComparisonError(
            f"comparison_id {comparison_id!r} is not a safe directory name "
            "(allowed: letters, digits, '.', '_', '-'; must start alphanumeric)"
        )
    return comparison_id


# --------------------------------------------------------------------------- #
# Metadata sidecar
# --------------------------------------------------------------------------- #


@dataclass(kw_only=True)
class ComparisonMeta(Schema):
    """The audit trail for one comparison — written to ``comparison_meta.json``.

    Everything the summary cannot say. ``unpaired_*`` names the cases that could
    not be compared at all; ``infra_attributed_case_ids`` names the candidates
    whose every error was infra-origin, and ``infra_suppressed_categories`` names,
    per case, the flags that were *withheld* because of it. A comparison with an
    empty summary and a populated sidecar has found nothing and hidden a lot; the
    two files together make that impossible to miss.
    """

    comparison_id: str
    suite_id: str
    suite_path: str
    baseline_run_id: str
    candidate_run_id: str
    baseline_version: str
    candidate_version: str
    baseline_adapter: str
    candidate_adapter: str
    paired_case_count: int = 0
    unpaired_baseline_case_ids: list[str] = field(default_factory=list)
    unpaired_candidate_case_ids: list[str] = field(default_factory=list)
    infra_attributed_case_ids: list[str] = field(default_factory=list)
    infra_suppressed_categories: dict[str, list[str]] = field(default_factory=dict)
    cost_comparable: bool = True
    comparability_notes: list[str] = field(default_factory=list)
    thresholds: dict[str, float] = field(default_factory=dict)
    flag_count: int = 0
    critical_count: int = 0
    warn_count: int = 0
    baseline_latency_p50: float = 0.0
    baseline_latency_p95: float = 0.0
    candidate_latency_p50: float = 0.0
    candidate_latency_p95: float = 0.0
    baseline_cost_total_usd: float = 0.0
    candidate_cost_total_usd: float = 0.0
    records_sha256: str = ""
    llmreg_version: str = __version__


@dataclass
class ComparisonResult:
    """The in-memory outcome of a comparison, plus where it was written."""

    meta: ComparisonMeta
    summary: ReportSummary
    records: list[ComparisonRecord]
    diffs: list[CaseDiff]
    outcomes: list[CaseOutcome]
    pairing: Pairing
    comparability: Comparability
    thresholds: Thresholds
    out_dir: Path | None = None

    @property
    def entries(self) -> list[FlagEntry]:
        return [entry for outcome in self.outcomes for entry in outcome.entries]

    @property
    def flag_count(self) -> int:
        return len(self.entries)

    @property
    def regressed_case_ids(self) -> list[str]:
        return [o.case_id for o in self.outcomes if o.entries]

    def flags_for(self, case_id: str) -> tuple[str, ...]:
        for outcome in self.outcomes:
            if outcome.case_id == case_id:
                return outcome.categories
        raise ComparisonError(f"case {case_id!r} was not part of this comparison")

    @property
    def comparison_path(self) -> Path | None:
        return None if self.out_dir is None else self.out_dir / COMPARISON_JSONL

    @property
    def summary_path(self) -> Path | None:
        return None if self.out_dir is None else self.out_dir / SUMMARY_JSON

    @property
    def meta_path(self) -> Path | None:
        return None if self.out_dir is None else self.out_dir / COMPARISON_META_JSON


# --------------------------------------------------------------------------- #
# Per-case record
# --------------------------------------------------------------------------- #


def _dimension_delta(diff: CaseDiff, *names: str) -> float:
    """Delta of the first of ``names`` both sides emitted; ``0.0`` when none did."""
    for name in names:
        if name in diff.metric_deltas:
            return diff.metric_deltas[name]
    return 0.0


def _summarize_case(diff: CaseDiff, outcome: CaseOutcome) -> str:
    """A short, deterministic sentence. Never echoes model-generated text."""
    if outcome.entries:
        worst = max(
            outcome.flags, key=lambda flag: SEVERITY_ORDER[flag.severity]
        )
        heads = ", ".join(f"{f.category} ({f.severity})" for f in outcome.flags)
        return f"regressed: {heads}. {worst.explanation}"
    if outcome.suppressed:
        kinds = ", ".join(diff.infra_error_kinds) or "unknown"
        withheld = ", ".join(outcome.suppressed)
        return (
            "no flag: the candidate degraded, but every error on this record is "
            f"infra-origin ({kinds}); withheld {withheld}"
        )
    if diff.infra_error_kinds:
        kinds = ", ".join(diff.infra_error_kinds)
        return (
            f"no regression; {len(diff.infra_error_kinds)} infra-origin error kind(s) "
            f"surfaced and not attributed to the application ({kinds})"
        )
    if diff.changed_fields:
        fields_ = ", ".join(diff.changed_fields)
        return f"no regression: {fields_} changed; no metric crossed a threshold"
    return "identical: no behavioral field changed"


def _comparison_record(
    diff: CaseDiff, outcome: CaseOutcome, baseline_run_id: str, candidate_run_id: str
) -> ComparisonRecord:
    return ComparisonRecord(
        case_id=diff.case_id,
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        changed_fields=list(diff.changed_fields),
        score_delta=_dimension_delta(diff, "task_success"),
        latency_delta=diff.latency_delta,
        cost_delta=diff.cost_delta,
        json_validity_delta=_dimension_delta(
            diff, "json_schema_validity", "json_parse_success"
        ),
        tool_call_delta=_dimension_delta(
            diff, "tool_sequence_similarity", "allowed_tool_ratio"
        ),
        refusal_delta=_dimension_delta(diff, "refusal_match", "risk_label_match"),
        evidence_delta=_dimension_delta(diff, "evidence_coverage"),
        regression_flags=list(outcome.flags),
        human_readable_summary=_summarize_case(diff, outcome),
    )


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #


def _paired_records(side: RunSide, pairing: Pairing) -> list[RunRecord]:
    return [side.record(case_id) for case_id in pairing.paired]


def compare(
    baseline: RunSide,
    candidate: RunSide,
    *,
    comparison_id: str | None = None,
    thresholds: Thresholds | None = None,
) -> ComparisonResult:
    """Pair, diff, flag, cluster, and summarize. Pure: writes nothing.

    Thresholds default to the baseline suite's manifest block, so a suite tunes
    its own tolerances (``docs/REGRESSION_DETECTION.md``).
    """
    comparability = check_comparable(baseline, candidate)
    resolved = thresholds or Thresholds.from_mapping(baseline.thresholds)
    comparison_id = _check_id(
        comparison_id or default_comparison_id(baseline, candidate)
    )

    pairing = pair_cases(baseline, candidate)
    diffs = diff_sides(baseline, candidate, pairing)
    outcomes = [
        evaluate_flags(
            diff, resolved, cost_comparable=comparability.cost_comparable
        )
        for diff in diffs
    ]
    records = [
        _comparison_record(diff, outcome, baseline.run_id, candidate.run_id)
        for diff, outcome in zip(diffs, outcomes, strict=True)
    ]

    entries = [entry for outcome in outcomes for entry in outcome.entries]
    clusters = cluster_failures(entries)

    flags_by_category: dict[str, int] = {}
    for entry in entries:
        category = entry.flag.category
        flags_by_category[category] = flags_by_category.get(category, 0) + 1
    # Emit in taxonomy order; ``to_json`` sorts keys, this keeps the dict readable.
    flags_by_category = {
        category: flags_by_category[category]
        for category in REGRESSION_CATEGORIES
        if category in flags_by_category
    }

    base_records = _paired_records(baseline, pairing)
    cand_records = _paired_records(candidate, pairing)
    base_latency = latency_stats(base_records)
    cand_latency = latency_stats(cand_records)
    base_cost, cand_cost = cost_stats(base_records), cost_stats(cand_records)

    summary = ReportSummary(
        comparison_id=comparison_id,
        suite_id=baseline.suite_id,
        baseline_version=baseline.app_version,
        candidate_version=candidate.app_version,
        case_count=len(pairing.paired),
        regressed_case_count=sum(1 for outcome in outcomes if outcome.entries),
        flags_by_category=flags_by_category,
        latency_p50_delta=delta(cand_latency.p50_ms, base_latency.p50_ms),
        latency_p95_delta=delta(cand_latency.p95_ms, base_latency.p95_ms),
        cost_total_delta=delta(
            cand_cost.cost_total_usd,
            base_cost.cost_total_usd,
            decimals=COST_DECIMALS,
        ),
        clusters=clusters,
    )

    severities = [entry.flag.severity for entry in entries]
    meta = ComparisonMeta(
        comparison_id=comparison_id,
        suite_id=baseline.suite_id,
        suite_path=baseline.suite_path,
        baseline_run_id=baseline.run_id,
        candidate_run_id=candidate.run_id,
        baseline_version=baseline.app_version,
        candidate_version=candidate.app_version,
        baseline_adapter=baseline.adapter,
        candidate_adapter=candidate.adapter,
        paired_case_count=len(pairing.paired),
        unpaired_baseline_case_ids=sorted(pairing.only_baseline),
        unpaired_candidate_case_ids=sorted(pairing.only_candidate),
        infra_attributed_case_ids=sorted(
            diff.case_id for diff in diffs if diff.attribution == "infra"
        ),
        infra_suppressed_categories={
            outcome.case_id: list(outcome.suppressed)
            for outcome in outcomes
            if outcome.suppressed
        },
        cost_comparable=comparability.cost_comparable,
        comparability_notes=list(comparability.notes),
        thresholds=resolved.as_dict(),
        flag_count=len(entries),
        critical_count=severities.count("critical"),
        warn_count=severities.count("warn"),
        baseline_latency_p50=base_latency.p50_ms,
        baseline_latency_p95=base_latency.p95_ms,
        candidate_latency_p50=cand_latency.p50_ms,
        candidate_latency_p95=cand_latency.p95_ms,
        baseline_cost_total_usd=base_cost.cost_total_usd,
        candidate_cost_total_usd=cand_cost.cost_total_usd,
        records_sha256=sha256_hex(render_jsonl(records)),
    )

    return ComparisonResult(
        meta=meta,
        summary=summary,
        records=records,
        diffs=diffs,
        outcomes=outcomes,
        pairing=pairing,
        comparability=comparability,
        thresholds=resolved,
    )


def write_comparison(
    result: ComparisonResult, out_root: Path | str
) -> ComparisonResult:
    """Write the three artifacts under ``out_root/<comparison_id>/``."""
    out_dir = Path(out_root) / result.meta.comparison_id
    out_dir.mkdir(parents=True, exist_ok=True)
    records_path = write_jsonl(out_dir / COMPARISON_JSONL, result.records)
    write_json(out_dir / SUMMARY_JSON, result.summary)
    write_json(out_dir / COMPARISON_META_JSON, result.meta)

    written = file_sha256(records_path)
    if written != result.meta.records_sha256:  # pragma: no cover - defensive
        raise ComparisonError(
            f"{records_path}: digest mismatch after write "
            f"({written} != {result.meta.records_sha256})"
        )
    result.out_dir = out_dir
    return result


def compare_runs(
    baseline_dir: Path | str,
    candidate_dir: Path | str,
    out_root: Path | str | None = None,
    *,
    comparison_id: str | None = None,
    thresholds: Thresholds | None = None,
    root: Path | None = None,
) -> ComparisonResult:
    """Load two run directories, compare them, and (unless ``out_root`` is ``None``)
    write the artifacts. ``out_root=None`` is the dry-run path."""
    baseline = load_baseline(baseline_dir, root=root)
    candidate = load_candidate(candidate_dir, baseline=baseline, root=root)
    result = compare(
        baseline, candidate, comparison_id=comparison_id, thresholds=thresholds
    )
    if out_root is not None:
        write_comparison(result, out_root)
    return result
