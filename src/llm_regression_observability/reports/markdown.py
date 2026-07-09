"""Markdown rendering: a :class:`~.model.ReportModel` in, one document out.

The layout answers a reader's questions in the order a reader asks them, and the
order is not negotiable:

1. **Did anything regress?** One sentence, before anything else.
2. **What, and how badly?** Per-category counts, then the worst flags by severity.
3. **Show me one.** A worked example per dimension — structured output, tool-call
   diff, evidence grounding — each naming the case to open first.
4. **What did it cost?** Latency and cost tables, including the cases that moved
   *without* flagging, so the tolerance is visible rather than implied.
5. **What did you not tell me?** Unpaired cases, infra-attributed cases,
   suppressed categories, comparability notes, and the thresholds actually applied.
6. **What can this not see?** The limitations note.

Section 5 exists because an evaluation harness's most dangerous output is a clean
report that was quietly unable to look. A comparison that paired 3 of 8 cases and
suppressed 2 more has a legitimate headline of "1 regression" — and is nearly
worthless. So the silences are printed, not appended to a log.

This module chooses no rows and computes no verdict; :mod:`.model` did that. It
lays out tables, escapes ``|``, and adds no fact of its own.
"""

from __future__ import annotations

from collections.abc import Iterable

from .base import (
    fmt_delta,
    fmt_ms,
    fmt_ms_delta,
    fmt_usd,
    fmt_usd_delta,
    fmt_value,
    one_line,
)
from .model import ReportModel

__all__ = ["render_markdown", "cell"]


def cell(text: str) -> str:
    """Make ``text`` safe inside a pipe table: no newlines, no bare ``|``."""
    return one_line(text).replace("|", "\\|")


def _code(text: str) -> str:
    return f"`{text}`"


def _table(header: Iterable[str], rows: Iterable[Iterable[str]]) -> list[str]:
    header = list(header)
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _severity(severity: str | None) -> str:
    return f"**{severity}**" if severity == "critical" else (severity or "—")


def _cases(case_ids: Iterable[str]) -> str:
    ids = list(case_ids)
    return ", ".join(_code(case_id) for case_id in ids) if ids else "—"


def _headline(model: ReportModel) -> list[str]:
    lines = [
        f"# {model.title}",
        "",
        f"**Verdict: {model.verdict}.** {model.headline}",
        "",
    ]
    lines.extend(
        _table(
            ("", ""),
            (
                ("comparison id", _code(model.comparison_id)),
                ("suite", f"{_code(model.suite_id)} ({_code(model.suite_path)})"),
                (
                    "baseline",
                    f"{_code(model.baseline_run_id)} — version "
                    f"{_code(model.baseline_version)}, adapter "
                    f"{_code(model.baseline_adapter)}",
                ),
                (
                    "candidate",
                    f"{_code(model.candidate_run_id)} — version "
                    f"{_code(model.candidate_version)}, adapter "
                    f"{_code(model.candidate_adapter)}",
                ),
                ("cases paired", str(model.case_count)),
                ("cases regressed", str(model.regressed_case_count)),
                (
                    "flags",
                    f"{model.flag_count} ({model.critical_count} critical, "
                    f"{model.warn_count} warn)",
                ),
                ("failure clusters", str(len(model.clusters))),
                ("records digest", _code(model.records_sha256)),
                ("generator", _code(model.generator)),
            ),
        )
    )
    lines.append("")
    return lines


def _categories(model: ReportModel) -> list[str]:
    lines = ["## Regressions by category", ""]
    lines.extend(
        _table(
            ("Category", "Flags", "Worst severity", "Cases"),
            (
                (
                    _code(row.category),
                    str(row.flag_count),
                    _severity(row.worst_severity),
                    _cases(row.case_ids),
                )
                for row in model.categories
            ),
        )
    )
    lines += [
        "",
        "All eight taxonomy categories are listed. A `0` row means no flag fired; "
        "it does not distinguish a dimension that was evaluated and clean from one "
        "no case in this suite declared.",
        "",
    ]
    return lines


def _worst(model: ReportModel) -> list[str]:
    lines = ["## Worst regressions", ""]
    if not model.worst:
        lines += ["No regression flag was raised.", ""]
        return lines
    lines.extend(
        _table(
            ("Case", "Category", "Severity", "Metric", "Observed", "Threshold", "Why"),
            (
                (
                    _code(row.case_id),
                    _code(row.category),
                    _severity(row.severity),
                    _code(row.metric),
                    fmt_value(row.observed),
                    fmt_value(row.threshold),
                    cell(row.explanation),
                )
                for row in model.worst
            ),
        )
    )
    lines.append("")
    if model.hidden_flag_count:
        lines += [
            f"Showing {len(model.worst)} of {model.worst_total} flags; "
            f"{model.hidden_flag_count} more are in `comparison.jsonl`.",
            "",
        ]
    return lines


def _examples(model: ReportModel) -> list[str]:
    lines = ["## Representative examples", ""]
    for example in model.examples:
        lines += [f"### {example.title}", ""]
        if example.absent:
            lines += [example.explanation, ""]
            continue
        lines += [
            f"Case {_code(example.case_id)} — {_code(example.category)}, "
            f"severity {_severity(example.severity)}.",
            "",
        ]
        lines.extend(
            _table(
                ("", ""),
                (
                    ("triggering metric", _code(example.metric)),
                    (
                        "observed vs threshold",
                        f"{fmt_value(example.observed)} vs "
                        f"{fmt_value(example.threshold)}",
                    ),
                    (
                        _code(example.delta_name),
                        fmt_delta(example.delta),
                    ),
                    ("`score_delta`", fmt_delta(example.score_delta)),
                    (
                        "changed fields",
                        ", ".join(_code(f) for f in example.changed_fields) or "none",
                    ),
                ),
            )
        )
        lines += ["", f"> {cell(example.explanation)}", ""]
    return lines


def _structured(model: ReportModel) -> list[str]:
    lines = ["## Structured-output validity", ""]
    if not model.structured_rows:
        lines += [
            "No case moved on the structured-output dimension.",
            "",
        ]
        return lines
    lines.extend(
        _table(
            ("Case", "`json_validity_delta`", "Flag", "Metric", "Detail"),
            (
                (
                    _code(row.case_id),
                    fmt_delta(row.delta),
                    _severity(row.severity),
                    _code(row.metric) if row.metric else "—",
                    cell(row.note),
                )
                for row in model.structured_rows
            ),
        )
    )
    lines += [
        "",
        "`json_validity_delta` is the change in `json_schema_validity` where the "
        "case declared a schema, and in `json_parse_success` otherwise. `-1` means "
        "the baseline was valid and the candidate is not.",
        "",
    ]
    return lines


def _latency(model: ReportModel) -> list[str]:
    latency = model.latency
    lines = ["## Latency", ""]
    lines.extend(
        _table(
            ("Statistic", "Baseline", "Candidate", "Delta"),
            (
                (
                    "p50",
                    fmt_ms(latency.baseline_p50),
                    fmt_ms(latency.candidate_p50),
                    fmt_ms_delta(latency.p50_delta),
                ),
                (
                    "p95",
                    fmt_ms(latency.baseline_p95),
                    fmt_ms(latency.candidate_p95),
                    fmt_ms_delta(latency.p95_delta),
                ),
            ),
        )
    )
    lines += [
        "",
        "Latency is **recorded in the trace**, not measured by this process. "
        "Percentiles are over the paired cases only.",
        "",
    ]
    if latency.movers:
        lines += ["Cases whose latency moved:", ""]
        lines.extend(
            _table(
                ("Case", "`latency_delta`", "Flag", "Detail"),
                (
                    (
                        _code(row.case_id),
                        fmt_ms_delta(row.delta),
                        _severity(row.severity),
                        cell(row.note),
                    )
                    for row in latency.movers
                ),
            )
        )
        lines.append("")
    else:
        lines += ["No case's latency moved.", ""]
    return lines


def _cost(model: ReportModel) -> list[str]:
    cost = model.cost
    lines = ["## Cost", ""]
    if not cost.comparable:
        lines += [
            "**Not compared.** The two runs were priced under different cost "
            "models, so a dollar delta between them would be an artifact of the "
            "price list rather than of the change under test. The `cost_regression` "
            "rule was disabled for this comparison.",
            "",
        ]
        return lines
    lines.extend(
        _table(
            ("", "Baseline", "Candidate", "Delta"),
            (
                (
                    "total (estimate)",
                    fmt_usd(cost.baseline_total),
                    fmt_usd(cost.candidate_total),
                    fmt_usd_delta(cost.total_delta),
                ),
            ),
        )
    )
    lines += [
        "",
        "Cost is an **estimate under a synthetic price list**, not a bill.",
        "",
    ]
    if cost.movers:
        lines += ["Cases whose estimated cost moved:", ""]
        lines.extend(
            _table(
                ("Case", "`cost_delta`", "Flag", "Detail"),
                (
                    (
                        _code(row.case_id),
                        fmt_usd_delta(row.delta),
                        _severity(row.severity),
                        cell(row.note),
                    )
                    for row in cost.movers
                ),
            )
        )
        lines.append("")
    else:
        lines += ["No case's estimated cost moved.", ""]
    return lines


def _clusters(model: ReportModel) -> list[str]:
    lines = ["## Failure clusters", ""]
    if not model.clusters:
        lines += ["No failures to cluster.", ""]
        return lines
    lines.extend(
        _table(
            ("Cluster", "Category", "Cases"),
            (
                (
                    cell(cluster.label),
                    _code(cluster.category),
                    _cases(cluster.case_ids),
                )
                for cluster in model.clusters
            ),
        )
    )
    lines += [
        "",
        "Flags are grouped by `(category, failure signature)`: cases that broke the "
        "same way are one finding. A case that failed in two ways appears twice.",
        "",
    ]
    return lines


def _disclosures(model: ReportModel) -> list[str]:
    lines = ["## What the engine declined to say", ""]
    if not model.has_disclosures:
        lines += [
            "Every case was paired, no case was attributed to infrastructure, and "
            "both runs were comparable on every dimension. Nothing was withheld.",
            "",
        ]
    else:
        items: list[str] = []
        unpaired = (
            model.unpaired_baseline_case_ids + model.unpaired_candidate_case_ids
        )
        if unpaired:
            items.append(
                f"**{len(unpaired)} unpaired case(s)** — present in one run only, so "
                f"neither scored nor flagged: {_cases(sorted(unpaired))}."
            )
        if model.infra_attributed_case_ids:
            items.append(
                f"**{len(model.infra_attributed_case_ids)} infra-attributed case(s)** "
                "— every error on the candidate record was harness-origin, so the "
                "application was not blamed: "
                f"{_cases(model.infra_attributed_case_ids)}."
            )
        for case_id, categories in model.infra_suppressed_categories.items():
            withheld = ", ".join(_code(category) for category in categories)
            items.append(
                f"**Flags withheld on {_code(case_id)}** — the candidate degraded, "
                f"but its errors were infra-origin: {withheld}."
            )
        if not model.cost.comparable:
            items.append(
                "**Cost was not compared** — the two runs used different cost models."
            )
        items.extend(f"**Comparability note** — {note}" for note in
                     model.comparability_notes)
        lines.extend(f"- {item}" for item in items)
        lines.append("")

    lines += ["### Thresholds applied", ""]
    lines.extend(
        _table(
            ("Threshold", "Value"),
            (
                (_code(name), fmt_value(value))
                for name, value in sorted(model.thresholds.items())
            ),
        )
    )
    lines += [
        "",
        f"Read from the suite manifest ({_code(model.suite_path)}). A flag means a "
        "metric crossed one of these numbers.",
        "",
    ]
    return lines


def _limitations(model: ReportModel) -> list[str]:
    lines = ["## Limitations", ""]
    lines.extend(f"- {item}" for item in model.limitations)
    lines += [
        "",
        "---",
        "",
        f"Rendered from `{model.comparison_id}` by {_code(model.generator)}. "
        "This report carries no timestamp: it is a pure function of the comparison "
        "artifacts, so two renderings of the same comparison are byte-identical.",
    ]
    return lines


def render_markdown(model: ReportModel) -> str:
    """Render ``model`` as a Markdown document ending in exactly one newline."""
    lines: list[str] = []
    lines += _headline(model)
    lines += _categories(model)
    lines += _worst(model)
    lines += _examples(model)
    lines += _structured(model)
    lines += _latency(model)
    lines += _cost(model)
    lines += _clusters(model)
    lines += _disclosures(model)
    lines += _limitations(model)
    return "\n".join(lines).rstrip("\n") + "\n"
