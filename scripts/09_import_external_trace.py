#!/usr/bin/env python3
"""Import two external traces, compare them, and report (Milestone 10).

The end-to-end demonstration of ``docs/INTEGRATION_CONTRACTS.md``: a trace produced
*outside* this repository flows through the **unchanged** executor, evaluators,
comparison engine and report generator.

    python3 scripts/09_import_external_trace.py

Four stages, all offline and deterministic:

1. ``import-trace`` the synthetic ``code_agent_runtime`` **baseline** session
   (``data/external/code_agent_runtime/baseline.json``) against
   ``evals/suites/external.yaml``.
2. ``import-trace`` the **candidate** session, which carries two injected defects:
   a disallowed ``shell_exec`` call, and a retried ``search``.
3. ``compare`` the two imported runs. Expected: ``tool_call_regression`` on
   ``tu_wrong_tool``, ``error_rate_regression`` on ``at_more_retries``, and nothing
   on the two control cases.
4. ``report`` the comparison as Markdown.

Then, as a coda, ``export-run`` wraps the imported baseline back into a sealed,
versioned ``TraceBundle`` — the other half of the contract, and the shape a sibling
system would consume.

No sibling repository is required, present, or read: the trace documents are
committed synthetic examples. Two invocations produce byte-identical artifacts.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from llm_regression_observability import cli  # noqa: E402

SOURCE = "code_agent_runtime"
SUITE = "evals/suites/external.yaml"
DOCUMENTS = Path("data/external") / SOURCE

BASELINE_RUN = "ext-baseline"
CANDIDATE_RUN = "ext-candidate"
RUNS = "results/runs"
COMPARISON_ID = "external-external-baseline-vs-external-candidate"
REPORT = "results/reports/external_regression_report.md"
BUNDLE = f"results/external/{BASELINE_RUN}.json"


def _step(title: str) -> None:
    print()
    print(f"=== {title}")
    print()


def main(argv: list[str]) -> int:
    _step(f"1/5  import the baseline session ({SOURCE})")
    code = cli.main([
        "import-trace",
        "--source", SOURCE,
        "--input", str(DOCUMENTS / "baseline.json"),
        "--suite", SUITE,
        "--app-version", "external-baseline",
        "--run-id", BASELINE_RUN,
        "--out", RUNS,
        *argv,
    ])
    if code:
        return code

    _step(f"2/5  import the candidate session ({SOURCE})")
    code = cli.main([
        "import-trace",
        "--source", SOURCE,
        "--input", str(DOCUMENTS / "candidate.json"),
        "--suite", SUITE,
        "--app-version", "external-candidate",
        "--run-id", CANDIDATE_RUN,
        "--out", RUNS,
        *argv,
    ])
    if code:
        return code

    _step("3/5  compare the two imported runs (unchanged comparison engine)")
    code = cli.main([
        "compare",
        "--baseline", f"{RUNS}/{BASELINE_RUN}",
        "--candidate", f"{RUNS}/{CANDIDATE_RUN}",
        "--out", "results/comparisons",
    ])
    if code:
        return code

    _step("4/5  report the comparison (unchanged report generator)")
    code = cli.main([
        "report",
        "--comparison", f"results/comparisons/{COMPARISON_ID}",
        "--out", REPORT,
    ])
    if code:
        return code

    _step("5/5  export the imported baseline as a sealed, versioned bundle")
    return cli.main([
        "export-run", "--run", f"{RUNS}/{BASELINE_RUN}", "--out", BUNDLE
    ])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
