"""Self-contained HTML rendering of the same report.

Same :class:`~.model.ReportModel`, same sections, same order as :mod:`.markdown`.
The only differences are presentational: severities become pills, tables get
zebra striping, and the whole thing is one file.

**Self-contained is a requirement, not a convenience.** A regression report gets
attached to a pull request, dropped into a bug tracker, or opened from a CI
artifact bucket — contexts where a stylesheet request either fails or, worse,
succeeds and tells a third party which report someone is reading. So there is no
``<link>``, no ``<script>``, no ``<img>``, no web font, and no ``url()``: the CSS
below is inlined and the document requests nothing over the network.
``tests/test_reports.py`` asserts that.

Every string is escaped with :func:`html.escape` on its way in. The strings a
report renders are rule-authored, but "the input is trusted" is exactly the
sentence that precedes an injection bug, and a report may one day render a tool
name or a case id that came from somewhere less careful.

Deterministic and offline, like everything else here: no clock, no RNG, no
network, no generation timestamp.
"""

from __future__ import annotations

from collections.abc import Iterable
from html import escape as _escape

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

__all__ = ["render_html", "STYLE"]

#: Inlined so the document requests nothing. Deliberately small: system fonts,
#: no colour that carries information a screen reader would miss (severity is
#: spelled out in the text of every pill).
STYLE = """\
:root {
  --fg: #1b1f23; --muted: #5b6570; --line: #e2e6ea; --bg: #ffffff;
  --code: #f4f6f8; --bad: #a4262c; --warn: #8a6100; --ok: #1a7f4b;
}
* { box-sizing: border-box; }
body {
  margin: 0 auto; max-width: 56rem; padding: 2rem 1.25rem 4rem;
  background: var(--bg); color: var(--fg);
  font: 16px/1.6 -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
h1 { font-size: 1.6rem; line-height: 1.3; margin: 0 0 0.5rem; }
h2 { font-size: 1.2rem; margin: 2.5rem 0 0.75rem; padding-bottom: 0.3rem;
     border-bottom: 1px solid var(--line); }
h3 { font-size: 1rem; margin: 1.5rem 0 0.5rem; }
p, li { margin: 0.5rem 0; }
code { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
       font-size: 0.86em; background: var(--code); padding: 0.08em 0.32em;
       border-radius: 3px; }
table { border-collapse: collapse; width: 100%; margin: 0.75rem 0; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--line);
         vertical-align: top; }
th { font-weight: 600; color: var(--muted); font-size: 0.8rem;
     text-transform: uppercase; letter-spacing: 0.03em; }
tbody tr:nth-child(even) { background: #fafbfc; }
.verdict { font-size: 1.02rem; margin: 0 0 1.25rem; padding: 0.7rem 0.9rem;
           border-left: 4px solid var(--muted); background: #fafbfc; }
.verdict.regressed { border-left-color: var(--bad); }
.verdict.clean { border-left-color: var(--ok); }
.pill { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 0.74rem; padding: 0.06rem 0.4rem; border: 1px solid var(--line);
        border-radius: 999px; white-space: nowrap; }
.pill.critical { color: var(--bad); border-color: var(--bad); font-weight: 600; }
.pill.warn { color: var(--warn); border-color: var(--warn); }
.pill.none { color: var(--muted); }
.note { color: var(--muted); font-size: 0.88rem; }
blockquote { margin: 0.75rem 0; padding: 0.4rem 0 0.4rem 0.9rem;
             border-left: 3px solid var(--line); color: var(--muted); }
footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--line);
         color: var(--muted); font-size: 0.85rem; }
"""


def _esc(text: str) -> str:
    return _escape(one_line(str(text)))


def _code(text: str) -> str:
    return f"<code>{_esc(text)}</code>"


def _pill(severity: str | None) -> str:
    if severity is None:
        return '<span class="pill none">no flag</span>'
    return f'<span class="pill {_esc(severity)}">{_esc(severity)}</span>'


def _cases(case_ids: Iterable[str]) -> str:
    ids = list(case_ids)
    return ", ".join(_code(case_id) for case_id in ids) if ids else "—"


def _table(header: Iterable[str] | None, rows: Iterable[Iterable[str]]) -> list[str]:
    """``header`` cells and ``rows`` cells are already-escaped HTML fragments."""
    out = ["<table>"]
    if header is not None:
        out.append(
            "<thead><tr>"
            + "".join(f"<th>{cell}</th>" for cell in header)
            + "</tr></thead>"
        )
    out.append("<tbody>")
    out.extend(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    out += ["</tbody>", "</table>"]
    return out


def _kv(rows: Iterable[tuple[str, str]]) -> list[str]:
    return _table(None, ((f"<strong>{key}</strong>", value) for key, value in rows))


def _headline(model: ReportModel) -> list[str]:
    css = "regressed" if model.regressed else "clean"
    out = [
        f"<h1>{_esc(model.title)}</h1>",
        f'<p class="verdict {css}"><strong>Verdict: {_esc(model.verdict)}.</strong> '
        f"{_esc(model.headline)}</p>",
    ]
    out += _kv(
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
            ("cases paired", _esc(model.case_count)),
            ("cases regressed", _esc(model.regressed_case_count)),
            (
                "flags",
                _esc(
                    f"{model.flag_count} ({model.critical_count} critical, "
                    f"{model.warn_count} warn)"
                ),
            ),
            ("failure clusters", _esc(len(model.clusters))),
            ("records digest", _code(model.records_sha256)),
            ("generator", _code(model.generator)),
        )
    )
    return out


def _categories(model: ReportModel) -> list[str]:
    out = ["<h2>Regressions by category</h2>"]
    out += _table(
        ("Category", "Flags", "Worst severity", "Cases"),
        (
            (
                _code(row.category),
                _esc(row.flag_count),
                _pill(row.worst_severity),
                _cases(row.case_ids),
            )
            for row in model.categories
        ),
    )
    out.append(
        '<p class="note">All eight taxonomy categories are listed. A <code>0</code> '
        "row means no flag fired; it does not distinguish a dimension that was "
        "evaluated and clean from one no case in this suite declared.</p>"
    )
    return out


def _worst(model: ReportModel) -> list[str]:
    out = ["<h2>Worst regressions</h2>"]
    if not model.worst:
        out.append("<p>No regression flag was raised.</p>")
        return out
    out += _table(
        ("Case", "Category", "Severity", "Metric", "Observed", "Threshold", "Why"),
        (
            (
                _code(row.case_id),
                _code(row.category),
                _pill(row.severity),
                _code(row.metric),
                _esc(fmt_value(row.observed)),
                _esc(fmt_value(row.threshold)),
                _esc(row.explanation),
            )
            for row in model.worst
        ),
    )
    if model.hidden_flag_count:
        out.append(
            f'<p class="note">Showing {len(model.worst)} of {model.worst_total} '
            f"flags; {model.hidden_flag_count} more are in "
            "<code>comparison.jsonl</code>.</p>"
        )
    return out


def _examples(model: ReportModel) -> list[str]:
    out = ["<h2>Representative examples</h2>"]
    for example in model.examples:
        out.append(f"<h3>{_esc(example.title)}</h3>")
        if example.absent:
            out.append(f"<p>{_esc(example.explanation)}</p>")
            continue
        out.append(
            f"<p>Case {_code(example.case_id)} — {_code(example.category)}, severity "
            f"{_pill(example.severity)}</p>"
        )
        out += _kv(
            (
                ("triggering metric", _code(example.metric)),
                (
                    "observed vs threshold",
                    _esc(
                        f"{fmt_value(example.observed)} vs "
                        f"{fmt_value(example.threshold)}"
                    ),
                ),
                (_code(example.delta_name), _esc(fmt_delta(example.delta))),
                ("score_delta", _esc(fmt_delta(example.score_delta))),
                (
                    "changed fields",
                    ", ".join(_code(f) for f in example.changed_fields) or "none",
                ),
            )
        )
        out.append(f"<blockquote>{_esc(example.explanation)}</blockquote>")
    return out


def _structured(model: ReportModel) -> list[str]:
    out = ["<h2>Structured-output validity</h2>"]
    if not model.structured_rows:
        out.append("<p>No case moved on the structured-output dimension.</p>")
        return out
    out += _table(
        ("Case", "json_validity_delta", "Flag", "Metric", "Detail"),
        (
            (
                _code(row.case_id),
                _esc(fmt_delta(row.delta)),
                _pill(row.severity),
                _code(row.metric) if row.metric else "—",
                _esc(row.note),
            )
            for row in model.structured_rows
        ),
    )
    out.append(
        '<p class="note"><code>json_validity_delta</code> is the change in '
        "<code>json_schema_validity</code> where the case declared a schema, and in "
        "<code>json_parse_success</code> otherwise. <code>-1</code> means the "
        "baseline was valid and the candidate is not.</p>"
    )
    return out


def _latency(model: ReportModel) -> list[str]:
    latency = model.latency
    out = ["<h2>Latency</h2>"]
    out += _table(
        ("Statistic", "Baseline", "Candidate", "Delta"),
        (
            (
                "p50",
                _esc(fmt_ms(latency.baseline_p50)),
                _esc(fmt_ms(latency.candidate_p50)),
                _esc(fmt_ms_delta(latency.p50_delta)),
            ),
            (
                "p95",
                _esc(fmt_ms(latency.baseline_p95)),
                _esc(fmt_ms(latency.candidate_p95)),
                _esc(fmt_ms_delta(latency.p95_delta)),
            ),
        ),
    )
    out.append(
        '<p class="note">Latency is <strong>recorded in the trace</strong>, not '
        "measured by this process. Percentiles are over the paired cases only.</p>"
    )
    if not latency.movers:
        out.append("<p>No case's latency moved.</p>")
        return out
    out.append("<p>Cases whose latency moved:</p>")
    out += _table(
        ("Case", "latency_delta", "Flag", "Detail"),
        (
            (
                _code(row.case_id),
                _esc(fmt_ms_delta(row.delta)),
                _pill(row.severity),
                _esc(row.note),
            )
            for row in latency.movers
        ),
    )
    return out


def _cost(model: ReportModel) -> list[str]:
    cost = model.cost
    out = ["<h2>Cost</h2>"]
    if not cost.comparable:
        out.append(
            "<p><strong>Not compared.</strong> The two runs were priced under "
            "different cost models, so a dollar delta between them would be an "
            "artifact of the price list rather than of the change under test. The "
            "<code>cost_regression</code> rule was disabled for this comparison.</p>"
        )
        return out
    out += _table(
        ("", "Baseline", "Candidate", "Delta"),
        (
            (
                "total (estimate)",
                _esc(fmt_usd(cost.baseline_total)),
                _esc(fmt_usd(cost.candidate_total)),
                _esc(fmt_usd_delta(cost.total_delta)),
            ),
        ),
    )
    out.append(
        '<p class="note">Cost is an <strong>estimate under a synthetic price '
        "list</strong>, not a bill.</p>"
    )
    if not cost.movers:
        out.append("<p>No case's estimated cost moved.</p>")
        return out
    out.append("<p>Cases whose estimated cost moved:</p>")
    out += _table(
        ("Case", "cost_delta", "Flag", "Detail"),
        (
            (
                _code(row.case_id),
                _esc(fmt_usd_delta(row.delta)),
                _pill(row.severity),
                _esc(row.note),
            )
            for row in cost.movers
        ),
    )
    return out


def _clusters(model: ReportModel) -> list[str]:
    out = ["<h2>Failure clusters</h2>"]
    if not model.clusters:
        out.append("<p>No failures to cluster.</p>")
        return out
    out += _table(
        ("Cluster", "Category", "Cases"),
        (
            (_esc(cluster.label), _code(cluster.category), _cases(cluster.case_ids))
            for cluster in model.clusters
        ),
    )
    out.append(
        '<p class="note">Flags are grouped by <code>(category, failure '
        "signature)</code>: cases that broke the same way are one finding. A case "
        "that failed in two ways appears twice.</p>"
    )
    return out


def _disclosures(model: ReportModel) -> list[str]:
    out = ["<h2>What the engine declined to say</h2>"]
    if not model.has_disclosures:
        out.append(
            "<p>Every case was paired, no case was attributed to infrastructure, and "
            "both runs were comparable on every dimension. Nothing was withheld.</p>"
        )
    else:
        items: list[str] = []
        unpaired = (
            model.unpaired_baseline_case_ids + model.unpaired_candidate_case_ids
        )
        if unpaired:
            items.append(
                f"<strong>{len(unpaired)} unpaired case(s)</strong> — present in one "
                "run only, so neither scored nor flagged: "
                f"{_cases(sorted(unpaired))}."
            )
        if model.infra_attributed_case_ids:
            items.append(
                f"<strong>{len(model.infra_attributed_case_ids)} infra-attributed "
                "case(s)</strong> — every error on the candidate record was "
                "harness-origin, so the application was not blamed: "
                f"{_cases(model.infra_attributed_case_ids)}."
            )
        for case_id, categories in model.infra_suppressed_categories.items():
            withheld = ", ".join(_code(category) for category in categories)
            items.append(
                f"<strong>Flags withheld on {_code(case_id)}</strong> — the candidate "
                f"degraded, but its errors were infra-origin: {withheld}."
            )
        if not model.cost.comparable:
            items.append(
                "<strong>Cost was not compared</strong> — the two runs used different "
                "cost models."
            )
        items.extend(
            f"<strong>Comparability note</strong> — {_esc(note)}"
            for note in model.comparability_notes
        )
        out.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")

    out.append("<h3>Thresholds applied</h3>")
    out += _table(
        ("Threshold", "Value"),
        (
            (_code(name), _esc(fmt_value(value)))
            for name, value in sorted(model.thresholds.items())
        ),
    )
    out.append(
        f'<p class="note">Read from the suite manifest ({_code(model.suite_path)}). '
        "A flag means a metric crossed one of these numbers.</p>"
    )
    return out


def _limitations(model: ReportModel) -> list[str]:
    out = ["<h2>Limitations</h2>", "<ul>"]
    out.extend(f"<li>{_esc(item)}</li>" for item in model.limitations)
    out.append("</ul>")
    out.append(
        f"<footer>Rendered from <code>{_esc(model.comparison_id)}</code> by "
        f"{_code(model.generator)}. This report carries no timestamp: it is a pure "
        "function of the comparison artifacts, so two renderings of the same "
        "comparison are byte-identical.</footer>"
    )
    return out


def render_html(model: ReportModel) -> str:
    """Render ``model`` as one self-contained HTML document (no external assets)."""
    body: list[str] = []
    body += _headline(model)
    body += _categories(model)
    body += _worst(model)
    body += _examples(model)
    body += _structured(model)
    body += _latency(model)
    body += _cost(model)
    body += _clusters(model)
    body += _disclosures(model)
    body += _limitations(model)

    document = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_esc(model.title)}</title>",
        f"<style>\n{STYLE}</style>",
        "</head>",
        "<body>",
        *body,
        "</body>",
        "</html>",
    ]
    return "\n".join(document) + "\n"
