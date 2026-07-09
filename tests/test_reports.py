"""Tests for the report generator (Milestone 7).

CPU-only and offline. A report is the one artifact a human reads instead of the
data, so the tests are organized around the four ways it could lie.

**It could omit a finding.** Every section ``docs/milestones/07-reports.md``
requires is asserted present, and every regression the comparison engine detected
must appear in the rendered document by case id *and* category.

**It could differ between two runs, or between two formats.** Rendering is a pure
function of the comparison artifacts — no clock, no RNG, no generation timestamp —
so two renders are byte-identical, and both formats draw their rows from one
``ReportModel``. An AST scan fails if any module in ``reports/`` reaches for a
clock, an RNG, or a socket.

**It could leak a secret.** :func:`~reports.base.assert_redaction_clean` runs on
the fully rendered document immediately before it is written, and is fail-closed:
the tests assert a report carrying a credential-like string is *refused*, that the
refusal names the pattern without echoing the value, and that a masking redactor
in the Milestone 9 seam lets the same report through clean.

**It could report a clean run it was never able to look at.** Unpaired cases,
infra-attributed cases, and withheld flags are asserted to surface in the
"declined to say" section rather than vanish into a zero.

The committed sample at ``docs/examples/regression_report.md`` is regenerated from
the fixtures and compared byte for byte, so a report in the docs can never drift
from the report the code produces.
"""

from __future__ import annotations

import ast
from html.parser import HTMLParser
from pathlib import Path

import pytest

from llm_regression_observability import cli, reports
from llm_regression_observability.comparison import compare_runs
from llm_regression_observability.comparison.aggregate import ComparisonMeta
from llm_regression_observability.comparison.regression import REGRESSION_CATEGORIES
from llm_regression_observability.executor import RunConfig, run_suite
from llm_regression_observability.reports import (
    DEFAULT_TOP,
    ReportError,
    build_report,
    generate_report,
    load_comparison,
    load_report_model,
    render,
    render_html,
    render_markdown,
    report_path,
    write_report,
)
from llm_regression_observability.reports.base import (
    assert_redaction_clean,
    fmt_delta,
    fmt_ms_delta,
    fmt_usd_delta,
    fmt_value,
    secret_findings,
)
from llm_regression_observability.schemas import (
    ComparisonRecord,
    FailureCluster,
    RegressionFlag,
    ReportSummary,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_SUITE = REPO_ROOT / "evals" / "suites" / "smoke.yaml"
EXAMPLE_REPORT = REPO_ROOT / "docs" / "examples" / "regression_report.md"

# Built at runtime rather than written as a literal: a test file must not itself
# contain a credential-shaped token for a tree scanner to find.
FAKE_KEY = "sk-" + "A" * 24
FAKE_BEARER = "Bearer " + "b" * 24

#: Section headings ``docs/milestones/07-reports.md`` requires, in Markdown.
REQUIRED_SECTIONS = (
    "# Regression report",
    "## Regressions by category",
    "## Worst regressions",
    "## Representative examples",
    "### Structured output",
    "### Tool-call diff",
    "### Evidence grounding",
    "## Structured-output validity",
    "## Latency",
    "## Cost",
    "## Failure clusters",
    "## What the engine declined to say",
    "### Thresholds applied",
    "## Limitations",
)

#: The six flags the smoke suite raises (``docs/RESULTS.md``).
SMOKE_FLAGS = {
    "sj_invalid_json": ("structured_output_regression", "critical"),
    "tu_wrong_tool": ("tool_call_regression", "critical"),
    "re_unsupported_claim": ("evidence_grounding_regression", "critical"),
    "rb_underrefuse": ("refusal_behavior_shift", "critical"),
    "lc_latency_spike": ("latency_regression", "warn"),
    "at_more_retries": ("error_rate_regression", "warn"),
}


# --------------------------------------------------------------------------- #
# Hand-built artifacts: the model is tested without a run on disk
# --------------------------------------------------------------------------- #


def _flag(
    category: str,
    severity: str = "critical",
    *,
    metric: str = "task_success",
    observed: float = 0.0,
    threshold: float = 1.0,
    explanation: str = "a property stopped holding",
) -> RegressionFlag:
    return RegressionFlag(
        category=category,
        severity=severity,
        metric=metric,
        observed=observed,
        threshold=threshold,
        explanation=explanation,
    )


def _record(case_id: str, *flags: RegressionFlag, **fields) -> ComparisonRecord:
    return ComparisonRecord(
        case_id=case_id,
        baseline_run_id="base-run",
        candidate_run_id="cand-run",
        regression_flags=list(flags),
        human_readable_summary=f"summary for {case_id}",
        **fields,
    )


def _summary(records: list[ComparisonRecord], **over) -> ReportSummary:
    counts: dict[str, int] = {}
    for record in records:
        for flag in record.regression_flags:
            counts[flag.category] = counts.get(flag.category, 0) + 1
    defaults = dict(
        comparison_id="cmp",
        suite_id="suite",
        baseline_version="baseline",
        candidate_version="candidate",
        case_count=len(records),
        regressed_case_count=sum(1 for r in records if r.regression_flags),
        flags_by_category=counts,
    )
    defaults.update(over)
    return ReportSummary(**defaults)


def _meta(records: list[ComparisonRecord], **over) -> ComparisonMeta:
    flags = [flag for record in records for flag in record.regression_flags]
    defaults = dict(
        comparison_id="cmp",
        suite_id="suite",
        suite_path="evals/suites/suite.yaml",
        baseline_run_id="base-run",
        candidate_run_id="cand-run",
        baseline_version="baseline",
        candidate_version="candidate",
        baseline_adapter="recorded",
        candidate_adapter="scripted-regression",
        paired_case_count=len(records),
        flag_count=len(flags),
        critical_count=sum(1 for f in flags if f.severity == "critical"),
        warn_count=sum(1 for f in flags if f.severity == "warn"),
        thresholds={"latency_abs_ms": 50.0},
        records_sha256="0" * 64,
    )
    defaults.update(over)
    return ComparisonMeta(**defaults)


def _model(records: list[ComparisonRecord], **kwargs) -> reports.ReportModel:
    return build_report(records, _summary(records), _meta(records), **kwargs)


REGRESSED_RECORDS = [
    _record(
        "c_json",
        _flag("structured_output_regression", metric="json_parse_success"),
        json_validity_delta=-1.0,
        score_delta=-1.0,
        changed_fields=["final_output", "parsed_output"],
    ),
    _record(
        "c_tool",
        _flag("tool_call_regression", "warn", metric="missing_tools"),
        tool_call_delta=-0.5,
    ),
    _record(
        "c_evidence",
        _flag("evidence_grounding_regression", metric="unsupported_claim_count"),
        evidence_delta=-0.25,
    ),
    _record(
        "c_latency",
        _flag(
            "latency_regression",
            "warn",
            metric="latency_ms",
            observed=1.5,
            threshold=0.25,
        ),
        latency_delta=300.0,
    ),
    _record("c_slower_but_clean", latency_delta=12.0),
    _record("c_control"),
]

CLEAN_RECORDS = [_record("c_one"), _record("c_two")]


# --------------------------------------------------------------------------- #
# The model: verdict, selection, ordering
# --------------------------------------------------------------------------- #


def test_a_regressed_comparison_leads_with_the_verdict_and_the_counts() -> None:
    model = _model(REGRESSED_RECORDS)
    assert model.verdict == "REGRESSED"
    assert model.regressed
    assert model.flag_count == 4
    assert model.critical_count == 2
    assert model.warn_count == 2
    assert "4 regression flag(s) across 4 of 6 paired cases" in model.headline


def test_a_clean_comparison_says_so_and_renders_every_section() -> None:
    model = _model(CLEAN_RECORDS)
    assert model.verdict == "CLEAN"
    assert not model.regressed
    assert model.headline == "No regression detected across 2 paired cases."
    text = render_markdown(model)
    for section in REQUIRED_SECTIONS:
        assert section in text, f"missing section: {section}"
    assert "No regression flag was raised." in text
    assert "Nothing was withheld." in text


def test_every_taxonomy_category_gets_a_row_even_when_silent() -> None:
    model = _model(REGRESSED_RECORDS)
    assert tuple(row.category for row in model.categories) == REGRESSION_CATEGORIES
    fired = {row.category: row for row in model.categories if row.fired}
    assert set(fired) == {
        "structured_output_regression",
        "tool_call_regression",
        "evidence_grounding_regression",
        "latency_regression",
    }
    silent = next(r for r in model.categories if r.category == "cost_regression")
    assert (silent.flag_count, silent.worst_severity, silent.case_ids) == (0, None, ())


def test_worst_regressions_sort_by_severity_then_taxonomy_then_case_id() -> None:
    records = [
        _record("z_case", _flag("latency_regression", "warn")),
        _record("a_case", _flag("cost_regression", "warn")),
        _record("m_case", _flag("tool_call_regression", "critical")),
        _record("b_case", _flag("structured_output_regression", "critical")),
    ]
    model = _model(records)
    assert [row.case_id for row in model.worst] == [
        "b_case",  # critical, structured_output is first in the taxonomy
        "m_case",  # critical, tool_call is second
        "z_case",  # warn, latency precedes cost in the taxonomy
        "a_case",  # warn, cost
    ]


def test_the_worst_table_says_how_many_flags_it_did_not_show() -> None:
    records = [
        _record(f"case_{i:02d}", _flag("latency_regression", "warn"))
        for i in range(12)
    ]
    model = _model(records, top=3)
    assert len(model.worst) == 3
    assert model.worst_total == 12
    assert model.hidden_flag_count == 9
    text = render_markdown(model)
    assert "Showing 3 of 12 flags; 9 more are in `comparison.jsonl`." in text

    everything = _model(records, top=None)
    assert len(everything.worst) == 12
    assert everything.hidden_flag_count == 0


def test_the_default_top_is_ten() -> None:
    assert DEFAULT_TOP == 10
    records = [
        _record(f"case_{i:02d}", _flag("latency_regression", "warn"))
        for i in range(11)
    ]
    assert len(_model(records).worst) == 10


def test_a_nonsense_top_is_an_error_not_an_empty_table() -> None:
    with pytest.raises(ReportError, match="top must be >= 1"):
        _model(REGRESSED_RECORDS, top=0)


def test_each_required_dimension_gets_an_example_or_says_it_stayed_silent() -> None:
    model = _model(REGRESSED_RECORDS)
    by_category = {example.category: example for example in model.examples}
    assert list(by_category) == [
        "structured_output_regression",
        "tool_call_regression",
        "evidence_grounding_regression",
    ]
    structured = by_category["structured_output_regression"]
    assert not structured.absent
    assert structured.case_id == "c_json"
    assert structured.delta_name == "json_validity_delta"
    assert structured.delta == -1.0
    assert structured.changed_fields == ("final_output", "parsed_output")

    silent = _model(CLEAN_RECORDS).examples[0]
    assert silent.absent
    assert silent.explanation == (
        "No case raised structured_output_regression in this comparison."
    )
    assert silent.case_id is None


def test_an_example_picks_the_most_severe_case_in_its_category() -> None:
    records = [
        _record("mild", _flag("tool_call_regression", "warn")),
        _record("severe", _flag("tool_call_regression", "critical")),
    ]
    example = next(
        e for e in _model(records).examples if e.category == "tool_call_regression"
    )
    assert (example.case_id, example.severity) == ("severe", "critical")


def test_a_sub_threshold_latency_move_still_appears_in_the_latency_table() -> None:
    """The tolerance has to be visible, or a clean report is indistinguishable from
    a report of a run nobody measured."""
    model = _model(REGRESSED_RECORDS)
    movers = {row.case_id: row for row in model.latency.movers}
    assert list(movers) == ["c_latency", "c_slower_but_clean"]  # sorted by |delta|
    assert movers["c_latency"].severity == "warn"
    assert movers["c_slower_but_clean"].severity is None
    assert "without crossing" in movers["c_slower_but_clean"].note
    # A case whose latency did not move at all is not a row.
    assert "c_control" not in movers


def test_the_structured_table_skips_cases_that_neither_moved_nor_flagged() -> None:
    model = _model(REGRESSED_RECORDS)
    assert [row.case_id for row in model.structured_rows] == ["c_json"]
    assert model.structured_rows[0].delta == -1.0
    assert model.structured_rows[0].metric == "json_parse_success"


def test_a_cost_model_mismatch_suppresses_the_cost_table_rather_than_inventing_one(
) -> None:
    records = [_record("c", cost_delta=0.01)]
    meta = _meta(records, cost_comparable=False)
    model = build_report(records, _summary(records), meta)
    assert not model.cost.comparable
    assert model.cost.movers == ()
    text = render_markdown(model)
    assert "**Not compared.**" in text
    assert "different cost models" in text


# --------------------------------------------------------------------------- #
# Cross-artifact disagreement
# --------------------------------------------------------------------------- #


def test_artifacts_that_disagree_on_the_comparison_id_are_refused() -> None:
    records = CLEAN_RECORDS
    with pytest.raises(ReportError, match="disagree on comparison_id"):
        build_report(records, _summary(records, comparison_id="other"), _meta(records))


def test_artifacts_that_disagree_on_the_case_count_are_refused() -> None:
    records = CLEAN_RECORDS
    with pytest.raises(ReportError, match="disagree on case count"):
        build_report(records, _summary(records), _meta(records, paired_case_count=7))


def test_artifacts_that_disagree_on_the_flag_count_are_refused() -> None:
    records = REGRESSED_RECORDS
    with pytest.raises(ReportError, match="counts 99"):
        build_report(records, _summary(records), _meta(records, flag_count=99))


def test_artifacts_that_disagree_on_the_regressed_case_count_are_refused() -> None:
    records = REGRESSED_RECORDS
    with pytest.raises(ReportError, match="regressed case"):
        build_report(
            records, _summary(records, regressed_case_count=1), _meta(records)
        )


# --------------------------------------------------------------------------- #
# The silences
# --------------------------------------------------------------------------- #


def test_unpaired_and_infra_attributed_cases_are_printed_not_dropped() -> None:
    records = CLEAN_RECORDS
    meta = _meta(
        records,
        unpaired_baseline_case_ids=["gone_from_candidate"],
        unpaired_candidate_case_ids=["new_in_candidate"],
        infra_attributed_case_ids=["timed_out"],
        infra_suppressed_categories={"timed_out": ["latency_regression"]},
        comparability_notes=["prompt_version differs between the two runs"],
    )
    model = build_report(records, _summary(records), meta)
    assert model.has_disclosures

    text = render_markdown(model)
    section = text.split("## What the engine declined to say")[1]
    assert "2 unpaired case(s)" in section
    assert "`gone_from_candidate`" in section
    assert "`new_in_candidate`" in section
    assert "1 infra-attributed case(s)" in section
    assert "`timed_out`" in section
    assert "Flags withheld on `timed_out`" in section
    assert "`latency_regression`" in section
    assert "prompt_version differs between the two runs" in section


def test_the_thresholds_actually_applied_are_printed() -> None:
    model = _model(REGRESSED_RECORDS)
    text = render_markdown(model)
    assert "| `latency_abs_ms` | 50 |" in text


# --------------------------------------------------------------------------- #
# Formatting: a number reads the same in both documents
# --------------------------------------------------------------------------- #


def test_a_metric_value_is_never_truncated_below_the_engines_precision() -> None:
    # `%g` would render this as 1.66667 and silently disagree with comparison.jsonl.
    assert fmt_value(1.666667) == "1.666667"
    assert fmt_value(0.0005) == "0.0005"
    assert fmt_value(50.0) == "50"
    assert fmt_value(0.0) == "0"


def test_a_delta_of_zero_is_unsigned() -> None:
    """`+$0.000000` reads like a rounded-down increase. It must not appear."""
    assert fmt_delta(0.0) == "0"
    assert fmt_ms_delta(0.0) == "0.0 ms"
    assert fmt_usd_delta(0.0) == "$0.000000"
    assert fmt_delta(-0.333333) == "-0.333333"
    assert fmt_ms_delta(250.0) == "+250.0 ms"
    assert fmt_usd_delta(-5e-05) == "-$0.000050"


# --------------------------------------------------------------------------- #
# Redaction: the seam and the gate
# --------------------------------------------------------------------------- #


def test_secret_findings_locates_a_credential_without_echoing_it() -> None:
    text = f"line one\nkey: {FAKE_KEY}\nline three\nauth: {FAKE_BEARER}\n"
    findings = secret_findings(text)
    assert findings == ((2, "openai_key"), (4, "bearer_token"))
    assert secret_findings("nothing credential-like here") == ()


def test_the_gate_names_the_pattern_and_the_line_but_never_the_value() -> None:
    with pytest.raises(ReportError) as excinfo:
        assert_redaction_clean(f"a\nb {FAKE_KEY}\n", source="report.md")
    message = str(excinfo.value)
    assert "line 2 (openai_key)" in message
    assert "report.md" in message
    assert FAKE_KEY not in message


def test_a_report_carrying_a_secret_is_refused_and_nothing_is_written(
    tmp_path: Path,
) -> None:
    records = [
        _record("leaky", _flag("latency_regression", "warn", explanation=FAKE_KEY))
    ]
    model = _model(records)
    out = tmp_path / "regression_report.md"
    with pytest.raises(ReportError, match="openai_key"):
        write_report(model, out)
    assert not out.exists()


def test_the_gate_catches_a_secret_the_redactor_seam_never_sees(
    tmp_path: Path,
) -> None:
    """The redactor is applied to free text only; the gate reads the whole document.

    A run id is an identifier, not model-derived prose, so ``build_report`` does
    not pass it through the redactor. The gate must still refuse it.
    """
    records = CLEAN_RECORDS
    meta = _meta(records, baseline_run_id=f"run-{FAKE_KEY}")
    model = build_report(records, _summary(records), meta)
    with pytest.raises(ReportError, match="openai_key"):
        write_report(model, tmp_path / "r.md")


def test_a_masking_redactor_in_the_seam_lets_the_same_report_through_clean(
    tmp_path: Path,
) -> None:
    records = [
        _record("leaky", _flag("latency_regression", "warn", explanation=FAKE_KEY))
    ]
    masked = build_report(
        records,
        _summary(records),
        _meta(records),
        redactor=lambda text: text.replace(FAKE_KEY, "[REDACTED]"),
    )
    path = write_report(masked, tmp_path / "regression_report.md")
    written = path.read_text(encoding="utf-8")
    assert FAKE_KEY not in written
    assert "[REDACTED]" in written
    assert secret_findings(written) == ()


def test_both_renderers_are_gated(tmp_path: Path) -> None:
    leaky = _model([_record("x", _flag("cost_regression", explanation=FAKE_KEY))])
    for fmt in reports.FORMATS:
        with pytest.raises(ReportError, match="openai_key"):
            write_report(leaky, tmp_path / "r", fmt)
    assert not any(tmp_path.iterdir())


def test_a_dry_run_is_gated_too_so_it_cannot_pass_where_a_write_would_refuse(
    leaky_comparison: Path,
) -> None:
    with pytest.raises(ReportError, match="openai_key"):
        generate_report(leaky_comparison, None, formats=reports.FORMATS)


def test_a_real_comparison_carrying_a_secret_is_never_written(
    tmp_path: Path, leaky_comparison: Path
) -> None:
    out = tmp_path / "regression_report.md"
    with pytest.raises(ReportError, match="openai_key"):
        generate_report(leaky_comparison, out)
    assert not out.exists()


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #


def test_markdown_holds_every_required_section() -> None:
    text = render_markdown(_model(REGRESSED_RECORDS))
    for section in REQUIRED_SECTIONS:
        assert section in text, f"missing section: {section}"


def test_markdown_names_every_detected_regression_by_case_and_category() -> None:
    text = render_markdown(_model(REGRESSED_RECORDS))
    for record in REGRESSED_RECORDS:
        for flag in record.regression_flags:
            assert f"`{record.case_id}`" in text
            assert f"`{flag.category}`" in text


def test_a_pipe_in_an_explanation_cannot_break_a_table_row() -> None:
    records = [
        _record(
            "c",
            _flag("cost_regression", "warn", explanation="a | b\nand a newline"),
        )
    ]
    text = render_markdown(_model(records))
    row = next(line for line in text.splitlines() if line.startswith("| `c` |"))
    assert r"a \| b and a newline" in row
    # 7 columns => 8 delimiters. The payload's pipe is escaped and adds none.
    assert row.replace(r"\|", "").count("|") == 8


def test_markdown_ends_with_exactly_one_newline() -> None:
    text = render_markdown(_model(REGRESSED_RECORDS))
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_two_renders_of_the_same_model_are_byte_identical() -> None:
    model = _model(REGRESSED_RECORDS)
    assert render_markdown(model) == render_markdown(model)
    assert render_html(model) == render_html(model)


def test_two_models_built_from_the_same_artifacts_render_identically() -> None:
    left = _model(REGRESSED_RECORDS)
    right = _model(REGRESSED_RECORDS)
    assert render_markdown(left) == render_markdown(right)
    assert render_html(left) == render_html(right)


def test_render_rejects_an_unknown_format() -> None:
    with pytest.raises(ReportError, match="unknown report format"):
        render(_model(CLEAN_RECORDS), "pdf")


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #


class _Balance(HTMLParser):
    """Enough of a parser to prove the document's tags nest."""

    VOID = frozenset({"meta", "br", "img", "link", "hr", "input"})

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.mismatches: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            self.mismatches.append(tag)
        else:
            self.stack.pop()


def test_html_is_well_formed() -> None:
    parser = _Balance()
    parser.feed(render_html(_model(REGRESSED_RECORDS)))
    assert parser.mismatches == []
    assert parser.stack == []


def test_html_is_self_contained_and_requests_nothing(smoke_report: str) -> None:
    for forbidden in ("<link", "<script", "<img", "url(", "http://", "https://"):
        assert forbidden not in smoke_report, f"html reaches for {forbidden!r}"
    assert smoke_report.startswith("<!doctype html>")
    assert "<style>" in smoke_report


def test_html_escapes_markup_in_a_rule_authored_string() -> None:
    records = [
        _record(
            "c",
            _flag("cost_regression", "warn", explanation="<script>x</script> & more"),
        )
    ]
    text = render_html(_model(records))
    assert "<script>x</script>" not in text
    assert "&lt;script&gt;x&lt;/script&gt; &amp; more" in text


def test_html_and_markdown_agree_on_what_they_report() -> None:
    model = _model(REGRESSED_RECORDS)
    md, doc = render_markdown(model), render_html(model)
    for record in REGRESSED_RECORDS:
        for flag in record.regression_flags:
            assert record.case_id in md and record.case_id in doc
            assert flag.category in md and flag.category in doc


# --------------------------------------------------------------------------- #
# Loading, paths, and writing
# --------------------------------------------------------------------------- #


def test_loading_a_directory_that_is_not_a_comparison_is_an_error(
    tmp_path: Path,
) -> None:
    with pytest.raises(ReportError, match="not a comparison directory"):
        load_comparison(tmp_path / "nowhere")

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ReportError, match="missing comparison.jsonl"):
        load_comparison(empty)


def test_a_corrupt_artifact_fails_at_load_not_halfway_through_a_table(
    tmp_path: Path, smoke_comparison: Path
) -> None:
    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    for name in ("comparison.jsonl", "summary.json", "comparison_meta.json"):
        (corrupt / name).write_text(
            (smoke_comparison / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    (corrupt / "summary.json").write_text('{"comparison_id": 3}', encoding="utf-8")
    with pytest.raises(ReportError):
        load_comparison(corrupt)


def test_report_path_resolves_the_suffix_from_the_format(tmp_path: Path) -> None:
    assert report_path("r.md", "markdown") == Path("r.md")
    assert report_path("r.markdown", "markdown") == Path("r.markdown")
    assert report_path("r.md", "html") == Path("r.html")
    assert report_path("r", "html") == Path("r.html")
    assert report_path(tmp_path, "markdown") == tmp_path / "regression_report.md"
    with pytest.raises(ReportError, match="unknown report format"):
        report_path("r.md", "pdf")


def test_generate_report_writes_one_file_per_format(
    tmp_path: Path, smoke_comparison: Path
) -> None:
    out = tmp_path / "regression_report.md"
    result = generate_report(smoke_comparison, out, formats=reports.FORMATS)
    assert result.written
    assert result.paths["markdown"] == out
    assert result.paths["html"] == tmp_path / "regression_report.html"
    assert set(result.documents) == set(reports.FORMATS)
    for fmt, path in result.paths.items():
        assert path.read_text(encoding="utf-8") == result.documents[fmt]


def test_a_dry_run_renders_and_gates_but_writes_nothing(
    tmp_path: Path, smoke_comparison: Path
) -> None:
    result = generate_report(smoke_comparison, None, formats=reports.FORMATS)
    assert not result.written
    assert result.documents["markdown"].startswith("# Regression report")
    assert list(tmp_path.iterdir()) == []


def test_generate_report_rejects_an_unknown_or_empty_format(
    smoke_comparison: Path,
) -> None:
    with pytest.raises(ReportError, match="no report format requested"):
        generate_report(smoke_comparison, None, formats=())
    with pytest.raises(ReportError, match=r"unknown report format\(s\) \['pdf'\]"):
        generate_report(smoke_comparison, None, formats=("pdf",))


def test_writing_twice_produces_byte_identical_files(
    tmp_path: Path, smoke_comparison: Path
) -> None:
    first = generate_report(smoke_comparison, tmp_path / "a" / "r.md",
                            formats=reports.FORMATS)
    second = generate_report(smoke_comparison, tmp_path / "b" / "r.md",
                             formats=reports.FORMATS)
    for fmt in reports.FORMATS:
        assert (
            first.paths[fmt].read_bytes() == second.paths[fmt].read_bytes()
        ), f"{fmt} report is not byte-stable"


# --------------------------------------------------------------------------- #
# End to end, on the smoke suite
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def smoke_comparison(tmp_path_factory) -> Path:
    """Baseline + candidate runs, compared, on disk. Offline; no fixtures mutated."""
    root = tmp_path_factory.mktemp("smoke")
    runs = root / "runs"
    for adapter, version in (
        ("recorded", "baseline"),
        ("scripted-regression", "candidate"),
    ):
        run_suite(
            RunConfig(suite_path=SMOKE_SUITE, adapter=adapter, app_version=version),
            runs,
        )
    result = compare_runs(
        runs / "smoke-baseline-recorded",
        runs / "smoke-candidate-scripted-regression",
        root / "comparisons",
    )
    assert result.out_dir is not None
    return result.out_dir


@pytest.fixture(scope="module")
def smoke_report(smoke_comparison: Path) -> str:
    return render_html(load_report_model(smoke_comparison))


@pytest.fixture
def leaky_comparison(tmp_path: Path, smoke_comparison: Path) -> Path:
    """The smoke comparison with a credential-like string spliced into one flag.

    The fixtures on disk stay clean; the copy is what the gate has to catch. This
    is the shape a real leak would take — a secret in a trace, carried into a flag
    explanation, on its way to a document a human pastes into a ticket.
    """
    leaky = tmp_path / "leaky"
    leaky.mkdir()
    for name in ("comparison.jsonl", "summary.json", "comparison_meta.json"):
        text = (smoke_comparison / name).read_text(encoding="utf-8")
        leaky.joinpath(name).write_text(
            text.replace("under_refusal", FAKE_KEY), encoding="utf-8"
        )
    return leaky


def test_the_smoke_report_names_all_six_detected_regressions(
    smoke_comparison: Path,
) -> None:
    text = render_markdown(load_report_model(smoke_comparison))
    worst = text.split("## Worst regressions")[1].split("## Representative examples")[0]
    for case_id, (category, severity) in SMOKE_FLAGS.items():
        row = next(
            line for line in worst.splitlines() if line.startswith(f"| `{case_id}` |")
        )
        assert f"`{category}`" in row
        assert severity in row


def test_the_smoke_report_surfaces_the_infra_attributed_case_without_blaming_it(
    smoke_comparison: Path,
) -> None:
    text = render_markdown(load_report_model(smoke_comparison))
    assert "`at_infra_noise`" not in text.split("## Worst regressions")[1].split(
        "## Representative"
    )[0]
    section = text.split("## What the engine declined to say")[1]
    assert "1 infra-attributed case(s)" in section
    assert "`at_infra_noise`" in section


def test_the_smoke_report_is_redaction_clean(smoke_comparison: Path) -> None:
    result = generate_report(smoke_comparison, None, formats=reports.FORMATS)
    for text in result.documents.values():
        assert secret_findings(text) == ()


def test_the_committed_example_report_is_not_stale(smoke_comparison: Path) -> None:
    """``docs/examples/regression_report.md`` is regenerated here and compared.

    A sample report in the docs that drifts from what the code emits is worse than
    no sample: it is a claim nobody re-checks.
    """
    rendered = render_markdown(load_report_model(smoke_comparison))
    assert EXAMPLE_REPORT.is_file(), f"missing {EXAMPLE_REPORT}"
    committed = EXAMPLE_REPORT.read_text(encoding="utf-8")
    assert committed == rendered, (
        "docs/examples/regression_report.md is stale; regenerate with "
        "`python3 scripts/06_generate_report.py` and copy the Markdown output"
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_report_writes_a_markdown_report(
    tmp_path: Path, smoke_comparison: Path, capsys
) -> None:
    out = tmp_path / "regression_report.md"
    code = cli.main(
        ["report", "--comparison", str(smoke_comparison), "--out", str(out)]
    )
    assert code == 0
    assert out.is_file()
    printed = capsys.readouterr().out
    assert "verdict          : REGRESSED" in printed
    assert "redaction        : clean" in printed
    assert f"report (markdown): {out}" in printed
    assert "result           : PASS" in printed


def test_cli_report_both_formats_writes_two_files(
    tmp_path: Path, smoke_comparison: Path
) -> None:
    out = tmp_path / "r.md"
    code = cli.main(
        [
            "report",
            "--comparison", str(smoke_comparison),
            "--out", str(out),
            "--format", "both",
        ]
    )
    assert code == 0
    assert out.is_file()
    assert (tmp_path / "r.html").is_file()


def test_cli_report_top_bounds_the_worst_table_and_zero_lifts_the_bound(
    tmp_path: Path, smoke_comparison: Path
) -> None:
    def run(top: str, name: str) -> str:
        out = tmp_path / name
        args = ["report", "--comparison", str(smoke_comparison), "--out", str(out),
                "--top", top]
        assert cli.main(args) == 0
        return out.read_text(encoding="utf-8")

    assert "Showing 1 of 6 flags; 5 more are in `comparison.jsonl`." in run("1", "a.md")
    assert "Showing" not in run("0", "b.md")


def test_cli_report_dry_run_writes_nothing(
    tmp_path: Path, smoke_comparison: Path, capsys
) -> None:
    code = cli.main(
        ["report", "--comparison", str(smoke_comparison), "--out",
         str(tmp_path / "r.md"), "--dry-run"]
    )
    assert code == 0
    assert list(tmp_path.iterdir()) == []
    assert "artifacts        : none written (--dry-run)" in capsys.readouterr().out


def test_cli_report_fails_cleanly_on_a_missing_comparison(
    tmp_path: Path, capsys
) -> None:
    code = cli.main(["report", "--comparison", str(tmp_path / "nope")])
    assert code == 1
    printed = capsys.readouterr().out
    assert "not a comparison directory" in printed
    assert "result           : FAIL" in printed


def test_cli_report_format_choices_stay_in_sync_with_the_package() -> None:
    assert set(cli.REPORT_FORMAT_CHOICES) == set(reports.FORMATS) | {"both"}
    assert cli.DEFAULT_REPORT_TOP == DEFAULT_TOP
    assert cli.DEFAULT_REPORT_OUT == "results/reports/regression_report.md"


def test_report_is_a_documented_subcommand() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["report", "--comparison", "x"])
    assert args.out == "results/reports/regression_report.md"
    assert args.format == "markdown"
    assert args.top == 10
    assert args.dry_run is False


# --------------------------------------------------------------------------- #
# Hygiene: nothing in reports/ can reach a clock, an RNG, or a socket
# --------------------------------------------------------------------------- #

_BANNED_IMPORTS = frozenset(
    {"random", "secrets", "time", "datetime", "socket", "urllib", "http", "requests"}
)

_MILESTONE_7_SOURCES = tuple(
    f"src/llm_regression_observability/reports/{name}.py"
    for name in ("__init__", "base", "model", "markdown", "html")
)


def _imported_top_level_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return modules


@pytest.mark.parametrize("rel", _MILESTONE_7_SOURCES)
def test_no_ambient_time_randomness_or_network_in_the_report_path(rel: str) -> None:
    reached = _imported_top_level_modules(
        (REPO_ROOT / rel).read_text(encoding="utf-8")
    ) & _BANNED_IMPORTS
    assert not reached, f"{rel} must not import {sorted(reached)}"


def test_the_report_carries_no_generation_timestamp(smoke_comparison: Path) -> None:
    """A timestamp would make two reports of one comparison differ byte for byte."""
    text = render_markdown(load_report_model(smoke_comparison))
    for word in ("generated at", "Generated on", "T00:00:", "UTC"):
        assert word not in text
    assert "carries no timestamp" in text


def test_the_example_dimensions_are_real_taxonomy_categories() -> None:
    for category, _, _ in reports.model.EXAMPLE_DIMENSIONS:
        assert category in REGRESSION_CATEGORIES


def test_the_limitations_note_is_never_empty_and_names_what_is_synthetic() -> None:
    text = "\n".join(reports.LIMITATIONS).lower()
    assert "synthetic" in text
    assert "not a bill" in text
    assert "infra-origin" in text


def test_a_failure_cluster_survives_into_the_report(smoke_comparison: Path) -> None:
    model = load_report_model(smoke_comparison)
    assert len(model.clusters) == 6
    assert all(isinstance(cluster, FailureCluster) for cluster in model.clusters)
    text = render_markdown(model)
    for cluster in model.clusters:
        assert cluster.label in text
