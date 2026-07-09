"""Tests that the top-level docs cite numbers the code produces (Milestone 12).

CPU-only and offline. The README, ``docs/RESULTS.md`` and the website are the
artifact most readers will ever see, and they are the one place in this repository
where a number can be wrong without anything failing. The committed sample report
already has ``test_the_committed_example_report_is_not_stale`` guarding it; this
file extends the same discipline to the prose around it.

A doc is checked one of two ways, and never by matching prose:

**Structured blocks are parsed and compared.** The ``ComparisonRecord`` the README
prints for ``tu_wrong_tool`` is extracted from its fenced ``json`` block and
compared field by field against the record the engine writes right here. If the
engine's explanation string, threshold or delta changes, the README fails rather
than quietly describing a system that no longer exists.

**Load-bearing counts are derived, then required to appear.** 18 detected defects,
0 false positives on 8 controls, 19 flags, 16 clusters, and the smoke suite's
verdict are all computed in-process from the fixtures and asserted present in the
documents that quote them. Nothing here asserts a number that is not first
measured.

What this file deliberately does *not* do is check English. It cannot tell whether
a sentence is honest; it can only tell whether a number is current.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from llm_regression_observability.comparison import RunSide, compare, compare_runs
from llm_regression_observability.datasets import (
    SUITE_NAMES,
    load_suite_fixture,
    resolve_fixture_roots,
)
from llm_regression_observability.executor import RunConfig, run_suite

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_SUITE = REPO_ROOT / "evals" / "suites" / "smoke.yaml"
SITE = REPO_ROOT / "site"
README = REPO_ROOT / "README.md"
RESULTS = REPO_ROOT / "docs" / "RESULTS.md"

EVALS_ROOT, DATA_ROOT = resolve_fixture_roots(None)

#: The pages `CLAUDE.md` requires. `cost-guard.html` and `integration-contracts.html`
#: were added by Milestones 11 and 10 and are required by their own doc pages' links.
REQUIRED_PAGES: tuple[str, ...] = (
    "index.html",
    "architecture.html",
    "trace-format.html",
    "metrics.html",
    "regression-categories.html",
    "example-comparison.html",
    "results.html",
    "limitations.html",
    "safety.html",
    "roadmap.html",
    "milestones.html",
)

#: Strings that mean "this document is not finished". `docs/milestones/13-*.md` may
#: legitimately say "planned"; a page a reader lands on may not.
PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "TODO",
    "TBD",
    "FIXME",
    "XXX",
    "coming soon",
    "to be filled",
    "will be reported next",
    "lorem ipsum",
)


# --------------------------------------------------------------------------- #
# Ground truth, measured here rather than quoted
# --------------------------------------------------------------------------- #


def side(suite: str, records, cases, version: str) -> RunSide:
    return RunSide.from_records(
        run_id=f"{suite}-{version}-recorded",
        app_version=version,
        suite_id=suite,
        adapter="recorded",
        cases=cases,
        records=records,
    )


@pytest.fixture(scope="module")
def fixture_comparisons() -> dict[str, object]:
    """Compare the committed baseline/candidate fixture pair of every suite."""
    results = {}
    for suite in SUITE_NAMES:
        fixture = load_suite_fixture(suite, EVALS_ROOT, DATA_ROOT)
        cases = {case.case_id: case for case in fixture.cases}
        results[suite] = compare(
            side(suite, fixture.baseline, cases, "baseline"),
            side(suite, fixture.candidate, cases, "candidate"),
        )
    return results


@pytest.fixture(scope="module")
def smoke_run(tmp_path_factory) -> tuple[Path, Path]:
    """The smoke baseline + candidate runs, compared, on disk. Offline."""
    root = tmp_path_factory.mktemp("docs-smoke")
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
    return runs, result.out_dir


def flags_of(results: dict[str, object]) -> list[tuple[str, object]]:
    return [
        (record.case_id, flag)
        for result in results.values()
        for record in result.records
        for flag in record.regression_flags
    ]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def prose(path: Path) -> str:
    """Reading text with the typography removed.

    A claim wrapped across two lines, emboldened, or inside a `<code>` element is
    the same claim. Matching raw bytes would make these tests fail on a reflow and
    pass on a lie, which is exactly backwards.
    """
    text = read(path)
    # Tag-shaped only, and never across a newline: the docs write `+20% < 25%`,
    # and a greedy `<[^>]+>` would eat every word up to the next `>` in the file.
    text = re.sub(r"</?[a-zA-Z][^>\n]*>", " ", text)
    text = re.sub(r"[*`>|]", " ", text)  # markdown emphasis, quotes, table pipes
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text)


def fenced_blocks(markdown: str, language: str) -> list[str]:
    pattern = re.compile(rf"^```{language}\n(.*?)^```", re.DOTALL | re.MULTILINE)
    return [match.group(1) for match in pattern.finditer(markdown)]


# --------------------------------------------------------------------------- #
# The README's trace example is the trace the pipeline writes
# --------------------------------------------------------------------------- #


def test_the_readme_trace_example_matches_the_recorded_run(
    smoke_run: tuple[Path, Path],
) -> None:
    """The two `RunRecord`s the README abridges are real, and say what it says.

    The README's argument rests on one fact: `final_output` is byte-identical
    across the two records while `tool_calls` is not. If a fixture edit broke that,
    the README would be arguing from an example that no longer makes the point.
    """
    runs, _ = smoke_run
    records = {}
    for run_id in ("smoke-baseline-recorded", "smoke-candidate-scripted-regression"):
        for line in (runs / run_id / "run.jsonl").read_text().splitlines():
            record = json.loads(line)
            if record["case_id"] == "tu_wrong_tool":
                records[run_id] = record

    baseline = records["smoke-baseline-recorded"]
    candidate = records["smoke-candidate-scripted-regression"]

    assert baseline["final_output"] == candidate["final_output"], (
        "the README claims the outputs are byte-identical"
    )
    assert baseline["latency_ms"] == candidate["latency_ms"] == 200.0
    assert baseline["cost_estimate_usd"] == candidate["cost_estimate_usd"] == 0.002
    assert [call["name"] for call in baseline["tool_calls"]] == ["search", "summarize"]
    assert [call["name"] for call in candidate["tool_calls"]] == ["search"]

    readme = read(README)
    assert '"latency_ms": 200.0' in readme
    assert '"cost_estimate_usd": 0.002' in readme
    assert readme.count('"name": "summarize"') == 1, (
        "the README must show `summarize` on the baseline side only"
    )


def test_the_readme_comparison_record_is_the_one_the_engine_writes(
    smoke_run: tuple[Path, Path],
) -> None:
    """Parse the README's JSON block; compare it to the live `ComparisonRecord`.

    Field for field, including the flag's explanation string. This is the strongest
    claim the README makes and the easiest one to let rot.
    """
    _, comparison = smoke_run
    live = next(
        json.loads(line)
        for line in (comparison / "comparison.jsonl").read_text().splitlines()
        if json.loads(line)["case_id"] == "tu_wrong_tool"
    )

    quoted = next(
        parsed
        for block in fenced_blocks(read(README), "json")
        if (parsed := json.loads(block)).get("case_id") == "tu_wrong_tool"
    )

    for field, value in quoted.items():
        if field == "regression_flags":
            continue
        assert live[field] == value, f"README's {field} is stale: {live[field]!r}"

    assert len(quoted["regression_flags"]) == len(live["regression_flags"]) == 1
    for field, value in quoted["regression_flags"][0].items():
        assert live["regression_flags"][0][field] == value, (
            f"README's flag.{field} is stale: {live['regression_flags'][0][field]!r}"
        )


def test_the_example_page_quotes_the_report_verbatim() -> None:
    """`example-comparison.html` says "verbatim", so it had better be verbatim.

    The page shows an excerpt of `docs/examples/regression_report.md` — itself
    regenerated byte for byte by `test_the_committed_example_report_is_not_stale`.
    Every line of the excerpt, elisions aside, must appear in the report *in order*.
    An excerpt that silently prettifies a table is a report nobody re-checks.
    """
    page = read(SITE / "example-comparison.html")
    block = re.search(
        r"<pre><code>(# Regression report.*?)</code></pre>", page, re.DOTALL
    )
    assert block is not None, "the rendered-report excerpt is missing"
    excerpt = (
        block.group(1)
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .splitlines()
    )

    report = read(REPO_ROOT / "docs" / "examples" / "regression_report.md").splitlines()
    cursor = 0
    for line in excerpt:
        if not line.strip() or line.strip().startswith("[..."):
            continue
        remaining = report[cursor:]
        assert line in remaining, (
            f"excerpt line missing from the report, or out of order: {line!r}"
        )
        cursor += remaining.index(line) + 1


def test_the_readme_control_case_still_regresses_nothing(
    fixture_comparisons: dict[str, object],
) -> None:
    """`sj_control_reorder`: the string changed, nothing regressed. The other half."""
    record = next(
        record
        for record in fixture_comparisons["structured_json"].records
        if record.case_id == "sj_control_reorder"
    )
    assert record.changed_fields == ["final_output"]
    assert record.regression_flags == []
    assert 'changed_fields = ["final_output"]' in prose(README)
    assert "regression_flags = []" in prose(README)


# --------------------------------------------------------------------------- #
# The counts the docs quote are the counts the engine measures
# --------------------------------------------------------------------------- #


def test_the_docs_cite_the_detection_counts_the_oracle_asserts(
    fixture_comparisons: dict[str, object],
) -> None:
    defects, controls = 0, 0
    for suite in SUITE_NAMES:
        fixture = load_suite_fixture(suite, EVALS_ROOT, DATA_ROOT)
        for expectation in fixture.expectations:
            if expectation.is_control:
                controls += 1
            else:
                defects += 1

    detected = {case_id for case_id, _ in flags_of(fixture_comparisons)}
    assert (defects, controls) == (18, 8)
    assert len(detected) == defects

    for doc in (README, RESULTS, SITE / "index.html", SITE / "results.html"):
        text = prose(doc)
        assert f"{defects} of {defects} injected defects" in text or (
            f"{defects}/{defects} injected defects" in text
        )
        assert "0 false positives" in text
        assert f"{controls} negative controls" in text


def test_the_docs_cite_the_flag_and_cluster_counts_the_engine_produces(
    fixture_comparisons: dict[str, object],
) -> None:
    flags = flags_of(fixture_comparisons)
    clusters = [
        cluster
        for result in fixture_comparisons.values()
        for cluster in result.summary.clusters
    ]
    assert (len(flags), len(clusters)) == (19, 16)

    for doc in (RESULTS, SITE / "results.html"):
        text = prose(doc)
        assert f"{len(flags)} flags across 18 defect cases" in text
        assert f"{len(clusters)} clusters over the {len(flags)} flags" in text
        # Every cluster the engine emits is listed by id, not just counted.
        for cluster in clusters:
            assert cluster.cluster_id in text, f"unlisted cluster {cluster.cluster_id}"


def test_the_docs_cite_the_smoke_verdict_the_pipeline_produces(
    smoke_run: tuple[Path, Path],
) -> None:
    _, comparison = smoke_run
    summary = json.loads((comparison / "summary.json").read_text())
    records = [
        json.loads(line)
        for line in (comparison / "comparison.jsonl").read_text().splitlines()
    ]
    flags = [flag for record in records for flag in record["regression_flags"]]
    critical = sum(1 for flag in flags if flag["severity"] == "critical")
    warn = sum(1 for flag in flags if flag["severity"] == "warn")

    assert summary["case_count"] == 8
    assert summary["regressed_case_count"] == 6
    assert (len(flags), critical, warn) == (6, 4, 2)
    assert len(summary["clusters"]) == 6

    headline = (
        f"{len(flags)} regression flag(s) across {summary['regressed_case_count']} "
        f"of {summary['case_count']} paired cases ({critical} critical, {warn} warn)"
    )
    for doc in (README, SITE / "index.html", SITE / "results.html"):
        assert headline in prose(doc), f"{doc.name} quotes a stale verdict"

    clusters = len(summary["clusters"])
    for doc in (README, RESULTS, SITE / "results.html"):
        text = prose(doc)
        assert f"+{summary['latency_p50_delta']}" in text
        assert f"+{summary['latency_p95_delta']}" in text
        # Either the prose form or the CLI's own `clusters : 6` line.
        assert f"{clusters} clusters" in text or f"clusters : {clusters}" in text


def test_the_docs_cite_the_current_baseline_digest(
    smoke_run: tuple[Path, Path],
) -> None:
    """`ee23bfde…` is quoted in several places; it is the digest of `run.jsonl`."""
    runs, _ = smoke_run
    records = (runs / "smoke-baseline-recorded" / "run.jsonl").read_bytes()
    digest = hashlib.sha256(records).hexdigest()

    meta = json.loads((runs / "smoke-baseline-recorded" / "run_meta.json").read_text())
    assert meta["records_sha256"] == digest

    for doc in (README, RESULTS, SITE / "results.html"):
        assert digest[:8] in read(doc), f"{doc.name} cites a stale digest"


def collect_test_count() -> int:
    """How many tests this suite has, by asking pytest to collect them.

    Collection only — nothing is executed, so this cannot recurse. A subprocess
    rather than ``pytest.main`` because the latter would mutate this session's
    plugin state. No ``-q``: ``addopts`` already supplies one, and a second turns
    the summary line into a per-file listing.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    match = re.search(r"(\d+) tests? collected", completed.stdout)
    assert match is not None, f"could not read a collection count:\n{completed.stdout}"
    return int(match.group(1))


#: The shapes in which a document claims a **suite total**. Deliberately anchored:
#: `155 tests assert exact expected scores` is a per-file count and must not match.
TOTAL_CLAIM_PATTERNS: tuple[str, ...] = (
    r"(\d{3,4}) passed",
    r"(\d{3,4}) tests passing",
    r"all (\d{3,4}) tests",
    r"(\d{3,4}) tests, CPU-only",
    r"(\d{3,4}) CPU-only offline tests",
    r"# (\d{3,4}) tests",
)


def test_the_docs_cite_the_current_test_count() -> None:
    """"N tests passing" is a claim, and it is the claim that rots first.

    Everywhere a document quotes a suite total it quotes the same total, and that
    total is the number of tests this repository actually has.
    """
    total = collect_test_count()

    for doc in (README, RESULTS, SITE / "index.html", SITE / "results.html"):
        assert str(total) in prose(doc), (
            f"{doc.name} does not cite the current test count ({total}); "
            "it must, because it claims one"
        )

    pages = [
        *sorted(SITE.glob("*.html")),
        *sorted((REPO_ROOT / "docs").glob("*.md")),
        README,
    ]
    for doc in pages:
        text = prose(doc)
        for pattern in TOTAL_CLAIM_PATTERNS:
            for stale in re.findall(pattern, text):
                assert int(stale) == total, (
                    f"{doc.name} claims a suite total of {stale}; there are {total}"
                )


# --------------------------------------------------------------------------- #
# No unfinished pages
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path",
    [README, RESULTS, *sorted(SITE.glob("*.html"))],
    ids=lambda path: path.name,
)
def test_no_reader_facing_page_makes_a_placeholder_claim(path: Path) -> None:
    text = read(path)
    for marker in PLACEHOLDER_MARKERS:
        assert marker.lower() not in text.lower(), f"{path.name} contains {marker!r}"


# --------------------------------------------------------------------------- #
# The website is complete, self-contained, and internally consistent
# --------------------------------------------------------------------------- #


def test_every_required_site_page_exists() -> None:
    missing = [name for name in REQUIRED_PAGES if not (SITE / name).is_file()]
    assert not missing, f"missing site pages: {missing}"


@pytest.mark.parametrize(
    "page", sorted(SITE.glob("*.html")), ids=lambda path: path.name
)
def test_every_site_link_resolves(page: Path) -> None:
    for href in re.findall(r'href="([^"]+)"', read(page)):
        assert (SITE / href).is_file(), f"{page.name} links to missing {href}"


@pytest.mark.parametrize(
    "page", sorted(SITE.glob("*.html")), ids=lambda path: path.name
)
def test_no_site_page_requests_an_external_asset(page: Path) -> None:
    """No build step, no CDN, no web font, no tracker. One local stylesheet."""
    text = read(page)
    assert "http://" not in text and "https://" not in text
    assert "<script" not in text
    assert re.search(r'<link[^>]+href="style\.css"', text), "missing local stylesheet"


@pytest.mark.parametrize(
    "page", sorted(SITE.glob("*.html")), ids=lambda path: path.name
)
def test_every_site_page_carries_the_same_navigation(page: Path) -> None:
    """A page reachable from one nav but absent from another is a dead end."""
    nav = re.search(r'<nav class="site">(.*?)</nav>', read(page), re.DOTALL)
    assert nav is not None, f"{page.name} has no site navigation"
    links = re.findall(r'href="([^"]+)"', nav.group(1))
    expected = re.search(
        r'<nav class="site">(.*?)</nav>', read(SITE / "index.html"), re.DOTALL
    )
    assert expected is not None
    assert links == re.findall(r'href="([^"]+)"', expected.group(1))
