"""The report's data model: comparison artifacts in, one renderable object out.

Both renderers (:mod:`.markdown`, :mod:`.html`) draw from the same
:class:`ReportModel`, so a decision — which regressions are "worst", which case
represents the tool-call dimension, whether a cost row may be shown at all — is
made **once**, here, and cannot come out different in two formats. The renderers
are then dumb: they lay out a table, they do not choose its rows.

**The model is built from artifacts, not from the engine.** :func:`load_comparison`
reads the three files ``results/comparisons/<id>/`` holds and nothing else. A
report is therefore reproducible from a directory a colleague hands you, with no
access to the runs, the fixtures, or the code that produced them — and it can say
nothing the artifacts do not already contain. That is a constraint, and it shows:
the structured-output table reports *deltas* rather than baseline/candidate
validity, because ``ComparisonRecord`` records the delta and the flag, not the two
absolute values.

**Cross-artifact disagreement is an error.** ``summary.json`` and
``comparison_meta.json`` restate several of the same counts. If they disagree, one
of them is stale, and a report that averages over the disagreement is worse than
no report at all. :func:`build_report` checks and raises :class:`ReportError`.

**Free text passes through the redactor.** Every field this module copies out of a
record that could conceivably carry trace-derived content — flag explanations,
per-case summaries, cluster labels, comparability notes — goes through
:data:`~.base.Redactor` on the way in, and the default is now the masking redactor
of Milestone 9. Identifiers (case ids, run ids, metric names) and numbers do not:
they are join keys, and a masked case id would break the report against the
artifacts it was built from. The final rendered document is gated separately by
:func:`~.base.assert_redaction_clean`, which reads identifiers too and is what
actually makes the guarantee.

Deterministic: no clock, no RNG, no network, and no generation timestamp. Two
reports built from the same comparison directory are byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..comparison.aggregate import (
    COMPARISON_JSONL,
    COMPARISON_META_JSON,
    SUMMARY_JSON,
    ComparisonMeta,
)
from ..comparison.regression import (
    EVIDENCE_GROUNDING_REGRESSION,
    REGRESSION_CATEGORIES,
    STRUCTURED_OUTPUT_REGRESSION,
    TOOL_CALL_REGRESSION,
)
from ..schemas import ComparisonRecord, FailureCluster, ReportSummary, SchemaError
from ..traces.storage import read_json, read_jsonl
from .base import (
    Redactor,
    ReportError,
    flag_sort_key,
    masking_redactor,
    severity_rank,
)

__all__ = [
    "DEFAULT_TOP",
    "EXAMPLE_DIMENSIONS",
    "LIMITATIONS",
    "FlagRow",
    "CategoryRow",
    "MoveRow",
    "Example",
    "LatencySection",
    "CostSection",
    "ReportModel",
    "load_comparison",
    "build_report",
    "load_report_model",
]

#: How many rows the "worst regressions" table shows before it says how many it hid.
DEFAULT_TOP = 10

#: The three dimensions ``docs/milestones/07-reports.md`` requires a worked example
#: of, each with the ``ComparisonRecord`` delta that carries it. A dimension with no
#: flag still gets a section, saying so — a missing section reads as an oversight.
EXAMPLE_DIMENSIONS: tuple[tuple[str, str, str], ...] = (
    (STRUCTURED_OUTPUT_REGRESSION, "json_validity_delta", "Structured output"),
    (TOOL_CALL_REGRESSION, "tool_call_delta", "Tool-call diff"),
    (EVIDENCE_GROUNDING_REGRESSION, "evidence_delta", "Evidence grounding"),
)

#: Printed verbatim at the foot of every report. A regression report that omits
#: what it cannot see is a marketing document (``CLAUDE.md``, quality bar).
LIMITATIONS: tuple[str, ...] = (
    "Fixtures are synthetic and their defects are injected. Detecting them shows "
    "the rules fire as specified; it is not a recall estimate on production "
    "regressions.",
    "Cost is an estimate under a synthetic price list, not a bill. Latency is "
    "recorded in the trace, not measured by the process that wrote this report.",
    "When every error on a candidate record is infra-origin, that case's flags are "
    "withheld and listed under \"What the engine declined to say\". A real "
    "application regression co-occurring with harness noise on the same case is "
    "masked.",
    "A delta of 0 means the dimension did not move *or* the case never declared "
    "it. An absent metric is not applicable, never zero. The flags are the claim; "
    "the deltas are the evidence.",
    "No learned model and no semantic judge scored anything here. Every metric is "
    "a deterministic function of the two traces and the case's declared "
    "properties.",
    "Every string in this report comes from the comparison artifacts: case ids, "
    "metric names, thresholds, and rule-authored explanations. Model-generated "
    "output text is never copied into it.",
)


# --------------------------------------------------------------------------- #
# Rows
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FlagRow:
    """One raised flag, flattened for a table."""

    case_id: str
    category: str
    severity: str
    metric: str
    observed: float
    threshold: float
    explanation: str


@dataclass(frozen=True)
class CategoryRow:
    """One row of the per-category breakdown. Emitted for silent categories too."""

    category: str
    flag_count: int
    case_ids: tuple[str, ...]
    worst_severity: str | None

    @property
    def fired(self) -> bool:
        return self.flag_count > 0


@dataclass(frozen=True)
class MoveRow:
    """A case that moved on one dimension, and what the engine made of it."""

    case_id: str
    delta: float
    severity: str | None
    metric: str | None
    note: str


@dataclass(frozen=True)
class Example:
    """A worked example of one regression dimension, or a statement that it stayed
    silent. ``absent`` distinguishes the two; every other field is then unset."""

    category: str
    title: str
    delta_name: str
    absent: bool
    case_id: str | None = None
    severity: str | None = None
    metric: str | None = None
    observed: float | None = None
    threshold: float | None = None
    delta: float | None = None
    score_delta: float | None = None
    changed_fields: tuple[str, ...] = ()
    explanation: str = ""
    summary: str = ""


@dataclass(frozen=True)
class LatencySection:
    baseline_p50: float
    candidate_p50: float
    p50_delta: float
    baseline_p95: float
    candidate_p95: float
    p95_delta: float
    movers: tuple[MoveRow, ...] = ()


@dataclass(frozen=True)
class CostSection:
    comparable: bool
    baseline_total: float
    candidate_total: float
    total_delta: float
    movers: tuple[MoveRow, ...] = ()


# --------------------------------------------------------------------------- #
# The model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReportModel:
    """Everything a renderer needs, already selected, sorted, and redacted."""

    comparison_id: str
    suite_id: str
    suite_path: str

    baseline_run_id: str
    baseline_version: str
    baseline_adapter: str
    candidate_run_id: str
    candidate_version: str
    candidate_adapter: str

    case_count: int
    regressed_case_count: int
    flag_count: int
    critical_count: int
    warn_count: int

    verdict: str
    headline: str

    categories: tuple[CategoryRow, ...]
    worst: tuple[FlagRow, ...]
    worst_total: int
    examples: tuple[Example, ...]
    latency: LatencySection
    cost: CostSection
    structured_rows: tuple[MoveRow, ...]
    clusters: tuple[FailureCluster, ...]

    unpaired_baseline_case_ids: tuple[str, ...]
    unpaired_candidate_case_ids: tuple[str, ...]
    infra_attributed_case_ids: tuple[str, ...]
    infra_suppressed_categories: dict[str, tuple[str, ...]]
    comparability_notes: tuple[str, ...]
    thresholds: dict[str, float]

    records_sha256: str
    generator: str
    limitations: tuple[str, ...] = LIMITATIONS

    @property
    def regressed(self) -> bool:
        return self.flag_count > 0

    @property
    def title(self) -> str:
        return (
            f"Regression report — {self.suite_id}: "
            f"{self.baseline_version} → {self.candidate_version}"
        )

    @property
    def hidden_flag_count(self) -> int:
        """Flags the "worst regressions" table did not have room for."""
        return max(0, self.worst_total - len(self.worst))

    @property
    def has_disclosures(self) -> bool:
        return bool(
            self.unpaired_baseline_case_ids
            or self.unpaired_candidate_case_ids
            or self.infra_attributed_case_ids
            or self.infra_suppressed_categories
            or self.comparability_notes
            or not self.cost.comparable
        )


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def load_comparison(
    comparison_dir: Path | str,
) -> tuple[list[ComparisonRecord], ReportSummary, ComparisonMeta]:
    """Read the three artifacts of one comparison, validating each against its
    schema. Raises :class:`ReportError` naming the file that is missing or bad."""
    directory = Path(comparison_dir)
    if not directory.is_dir():
        raise ReportError(f"{directory}: not a comparison directory")

    paths = {
        COMPARISON_JSONL: directory / COMPARISON_JSONL,
        SUMMARY_JSON: directory / SUMMARY_JSON,
        COMPARISON_META_JSON: directory / COMPARISON_META_JSON,
    }
    missing = sorted(name for name, path in paths.items() if not path.is_file())
    if missing:
        raise ReportError(
            f"{directory}: not a comparison directory (missing {', '.join(missing)}). "
            "Run `llmreg compare` first."
        )

    try:
        records = read_jsonl(paths[COMPARISON_JSONL], ComparisonRecord)
        summary = read_json(paths[SUMMARY_JSON], ReportSummary)
        meta = read_json(paths[COMPARISON_META_JSON], ComparisonMeta)
    except (SchemaError, OSError) as exc:
        raise ReportError(f"{directory}: {exc}") from exc
    return records, summary, meta


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #


def _check_consistent(
    records: list[ComparisonRecord], summary: ReportSummary, meta: ComparisonMeta
) -> None:
    """Refuse to render artifacts that contradict each other."""
    if summary.comparison_id != meta.comparison_id:
        raise ReportError(
            f"summary.json and comparison_meta.json disagree on comparison_id: "
            f"{summary.comparison_id!r} != {meta.comparison_id!r}"
        )
    counts = {
        "comparison.jsonl records": len(records),
        "summary.case_count": summary.case_count,
        "meta.paired_case_count": meta.paired_case_count,
    }
    if len(set(counts.values())) != 1:
        detail = ", ".join(f"{name}={value}" for name, value in counts.items())
        raise ReportError(f"{meta.comparison_id}: artifacts disagree on case count "
                          f"({detail})")

    flags = sum(len(record.regression_flags) for record in records)
    if flags != meta.flag_count:
        raise ReportError(
            f"{meta.comparison_id}: comparison.jsonl holds {flags} flag(s) but "
            f"comparison_meta.json counts {meta.flag_count}"
        )
    regressed = sum(1 for record in records if record.regression_flags)
    if regressed != summary.regressed_case_count:
        raise ReportError(
            f"{meta.comparison_id}: comparison.jsonl holds {regressed} regressed "
            f"case(s) but summary.json counts {summary.regressed_case_count}"
        )


def _flag_rows(records: list[ComparisonRecord], redact: Redactor) -> list[FlagRow]:
    return [
        FlagRow(
            case_id=record.case_id,
            category=flag.category,
            severity=flag.severity,
            metric=flag.metric,
            observed=flag.observed,
            threshold=flag.threshold,
            explanation=redact(flag.explanation),
        )
        for record in records
        for flag in record.regression_flags
    ]


def _category_rows(rows: list[FlagRow]) -> tuple[CategoryRow, ...]:
    """One row per taxonomy category, silent ones included.

    A category with no flag is not the same claim as a category that was never
    evaluated, and the artifacts cannot tell the two apart — so the row says
    "0 flags" and the limitations note says what that does and does not mean.
    """
    out: list[CategoryRow] = []
    for category in REGRESSION_CATEGORIES:
        matching = [row for row in rows if row.category == category]
        out.append(
            CategoryRow(
                category=category,
                flag_count=len(matching),
                case_ids=tuple(sorted({row.case_id for row in matching})),
                worst_severity=(
                    max((row.severity for row in matching), key=severity_rank)
                    if matching
                    else None
                ),
            )
        )
    return tuple(out)


def _worst(rows: list[FlagRow], top: int | None) -> tuple[FlagRow, ...]:
    ordered = sorted(
        rows, key=lambda row: flag_sort_key(row.case_id, row.category, row.severity)
    )
    return tuple(ordered if top is None else ordered[:top])


def _example(
    category: str,
    title: str,
    delta_name: str,
    records: list[ComparisonRecord],
    redact: Redactor,
) -> Example:
    """The most severe case that raised ``category``; ties break on run order."""
    candidates = [
        (record, flag)
        for record in records
        for flag in record.regression_flags
        if flag.category == category
    ]
    if not candidates:
        return Example(
            category=category,
            title=title,
            delta_name=delta_name,
            absent=True,
            explanation=f"No case raised {category} in this comparison.",
        )
    record, flag = max(candidates, key=lambda pair: severity_rank(pair[1].severity))
    return Example(
        category=category,
        title=title,
        delta_name=delta_name,
        absent=False,
        case_id=record.case_id,
        severity=flag.severity,
        metric=flag.metric,
        observed=flag.observed,
        threshold=flag.threshold,
        delta=getattr(record, delta_name),
        score_delta=record.score_delta,
        changed_fields=tuple(record.changed_fields),
        explanation=redact(flag.explanation),
        summary=redact(record.human_readable_summary),
    )


def _flag_for(record: ComparisonRecord, category: str):
    for flag in record.regression_flags:
        if flag.category == category:
            return flag
    return None


def _movers(
    records: list[ComparisonRecord],
    delta_name: str,
    category: str,
    redact: Redactor,
    *,
    sub_threshold_note: str,
) -> tuple[MoveRow, ...]:
    """Cases whose ``delta_name`` moved, worst-sized move first.

    A case that moved without crossing a threshold is *kept* and labelled. It is
    the row a reader needs to see the tolerance working, and dropping it would
    make a report of a clean run indistinguishable from a report of a run nobody
    measured.
    """
    rows: list[MoveRow] = []
    for record in records:
        delta = getattr(record, delta_name)
        if delta == 0.0:
            continue
        flag = _flag_for(record, category)
        rows.append(
            MoveRow(
                case_id=record.case_id,
                delta=delta,
                severity=flag.severity if flag else None,
                metric=flag.metric if flag else None,
                note=redact(flag.explanation) if flag else sub_threshold_note,
            )
        )
    rows.sort(key=lambda row: (-abs(row.delta), row.case_id))
    return tuple(rows)


def _structured_rows(
    records: list[ComparisonRecord], redact: Redactor
) -> tuple[MoveRow, ...]:
    """Structured-output validity: every case that moved or flagged.

    ``json_validity_delta`` is ``json_schema_validity`` where the case declared a
    schema and ``json_parse_success`` otherwise, so ``-1`` means "was valid, is
    not". The absolute values are not in the artifacts; the delta and the flag are.
    """
    rows: list[MoveRow] = []
    for record in records:
        flag = _flag_for(record, STRUCTURED_OUTPUT_REGRESSION)
        if record.json_validity_delta == 0.0 and flag is None:
            continue
        rows.append(
            MoveRow(
                case_id=record.case_id,
                delta=record.json_validity_delta,
                severity=flag.severity if flag else None,
                metric=flag.metric if flag else None,
                note=(
                    redact(flag.explanation)
                    if flag
                    else "moved, but raised no structured-output flag"
                ),
            )
        )
    rows.sort(key=lambda row: (-severity_rank(row.severity), row.delta, row.case_id))
    return tuple(rows)


def build_report(
    records: list[ComparisonRecord],
    summary: ReportSummary,
    meta: ComparisonMeta,
    *,
    top: int | None = DEFAULT_TOP,
    redactor: Redactor = masking_redactor,
) -> ReportModel:
    """Select, sort, and redact. Pure: reads no file and writes none."""
    if top is not None and top < 1:
        raise ReportError(f"top must be >= 1 or None (show all), got {top}")
    _check_consistent(records, summary, meta)
    redact = redactor

    rows = _flag_rows(records, redact)
    if meta.flag_count:
        headline = (
            f"{meta.flag_count} regression flag(s) across "
            f"{summary.regressed_case_count} of {summary.case_count} paired cases "
            f"({meta.critical_count} critical, {meta.warn_count} warn)."
        )
        verdict = "REGRESSED"
    else:
        headline = (
            f"No regression detected across {summary.case_count} paired cases."
        )
        verdict = "CLEAN"

    cost_movers: tuple[MoveRow, ...] = ()
    if meta.cost_comparable:
        cost_movers = _movers(
            records,
            "cost_delta",
            "cost_regression",
            redact,
            sub_threshold_note=(
                "cost moved without crossing both the relative and absolute floors"
            ),
        )

    return ReportModel(
        comparison_id=meta.comparison_id,
        suite_id=meta.suite_id,
        suite_path=meta.suite_path,
        baseline_run_id=meta.baseline_run_id,
        baseline_version=meta.baseline_version,
        baseline_adapter=meta.baseline_adapter,
        candidate_run_id=meta.candidate_run_id,
        candidate_version=meta.candidate_version,
        candidate_adapter=meta.candidate_adapter,
        case_count=summary.case_count,
        regressed_case_count=summary.regressed_case_count,
        flag_count=meta.flag_count,
        critical_count=meta.critical_count,
        warn_count=meta.warn_count,
        verdict=verdict,
        headline=headline,
        categories=_category_rows(rows),
        worst=_worst(rows, top),
        worst_total=len(rows),
        examples=tuple(
            _example(category, title, delta_name, records, redact)
            for category, delta_name, title in EXAMPLE_DIMENSIONS
        ),
        latency=LatencySection(
            baseline_p50=meta.baseline_latency_p50,
            candidate_p50=meta.candidate_latency_p50,
            p50_delta=summary.latency_p50_delta,
            baseline_p95=meta.baseline_latency_p95,
            candidate_p95=meta.candidate_latency_p95,
            p95_delta=summary.latency_p95_delta,
            movers=_movers(
                records,
                "latency_delta",
                "latency_regression",
                redact,
                sub_threshold_note=(
                    "latency moved without crossing both the absolute and relative "
                    "floors"
                ),
            ),
        ),
        cost=CostSection(
            comparable=meta.cost_comparable,
            baseline_total=meta.baseline_cost_total_usd,
            candidate_total=meta.candidate_cost_total_usd,
            total_delta=summary.cost_total_delta,
            movers=cost_movers,
        ),
        structured_rows=_structured_rows(records, redact),
        clusters=tuple(
            FailureCluster(
                cluster_id=cluster.cluster_id,
                label=redact(cluster.label),
                category=cluster.category,
                case_ids=list(cluster.case_ids),
            )
            for cluster in summary.clusters
        ),
        unpaired_baseline_case_ids=tuple(meta.unpaired_baseline_case_ids),
        unpaired_candidate_case_ids=tuple(meta.unpaired_candidate_case_ids),
        infra_attributed_case_ids=tuple(meta.infra_attributed_case_ids),
        infra_suppressed_categories={
            case_id: tuple(categories)
            for case_id, categories in sorted(meta.infra_suppressed_categories.items())
        },
        comparability_notes=tuple(redact(note) for note in meta.comparability_notes),
        thresholds=dict(meta.thresholds),
        records_sha256=meta.records_sha256,
        generator=f"llmreg {meta.llmreg_version}",
    )


def load_report_model(
    comparison_dir: Path | str,
    *,
    top: int | None = DEFAULT_TOP,
    redactor: Redactor = masking_redactor,
) -> ReportModel:
    """:func:`load_comparison` then :func:`build_report`."""
    records, summary, meta = load_comparison(comparison_dir)
    return build_report(records, summary, meta, top=top, redactor=redactor)
