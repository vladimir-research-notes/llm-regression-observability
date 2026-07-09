#!/usr/bin/env python3
"""Project a run into spans and reduce it to suite-level metrics (Milestone 8).

Reads ``results/runs/<run_id>/run.jsonl`` (written by ``llmreg run-suite``),
scores it with the Milestone 5 evaluators, projects each case into a span tree,
and writes two artifacts:

* ``results/runs/<run_id>/spans.jsonl``       — the span tree, validated
* ``results/processed/<run_id>/aggregate.json`` — the suite summary

    python3 scripts/07_aggregate_runs.py                      # both smoke runs
    python3 scripts/07_aggregate_runs.py --run smoke-baseline-recorded
    python3 scripts/07_aggregate_runs.py --dry-run            # print only

The summary keeps **application** scores, **harness** signals
(``trace_completeness``, ``infra_error_rate``) and declared **placeholders** in
three separate maps, so a degraded harness can never be read as a regression in
the system under test. Offline, CPU-only, deterministic: two invocations on the
same run artifacts produce byte-identical ``spans.jsonl`` and ``aggregate.json``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from llm_regression_observability.cli import main as cli_main  # noqa: E402
from llm_regression_observability.executor import RUN_JSONL  # noqa: E402

DEFAULT_RUNS = ("smoke-baseline-recorded", "smoke-candidate-scripted-regression")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--run",
        action="append",
        default=None,
        help="run id under --runs-root (repeatable; default: both smoke runs)",
    )
    parser.add_argument("--runs-root", default="results/runs")
    args, extra = parser.parse_known_args(argv)

    runs_root = Path(args.runs_root)
    for i, run_id in enumerate(args.run or list(DEFAULT_RUNS)):
        run_dir = runs_root / run_id
        if not (run_dir / RUN_JSONL).is_file():
            print(f"error            : no {RUN_JSONL} under {run_dir}")
            print("hint             : run scripts/02_run_baseline.py first")
            print("result           : FAIL")
            return 1
        if i:
            print()
        code = cli_main(["aggregate", "--run", str(run_dir), *extra])
        if code:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
