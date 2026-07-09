#!/usr/bin/env python3
"""Compare the smoke baseline against the smoke candidate (Milestone 6).

Thin wrapper over ``llmreg compare`` so the comparison is reproducible without an
editable install (it prepends ``src/`` to the path, like ``conftest.py``).

    python3 scripts/05_compare_runs.py [-- EXTRA llmreg compare ARGS]

Reads ``results/runs/{smoke-baseline-recorded,smoke-candidate-scripted-regression}/``
(written by ``scripts/02_run_baseline.py`` and ``scripts/03_run_candidate.py``) and
writes ``results/comparisons/smoke-baseline-vs-candidate/``:
``comparison.jsonl``, ``summary.json``, ``comparison_meta.json``.

Any extra arguments are appended verbatim, so the defaults can be overridden:

    python3 scripts/05_compare_runs.py --dry-run
    python3 scripts/05_compare_runs.py --fail-on critical   # a CI regression gate

Numbered ``05`` rather than ``04``: ``scripts/04_score_runs.py`` (Milestone 5)
already holds that slot. The milestone note records the deviation from
``docs/milestones/06-comparison.md``, which named the file ``04_compare_runs.py``.

Offline, CPU-only, deterministic: two invocations on the same run artifacts
produce byte-identical output files.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from llm_regression_observability import cli  # noqa: E402

BASELINE = "results/runs/smoke-baseline-recorded"
CANDIDATE = "results/runs/smoke-candidate-scripted-regression"
OUT = "results/comparisons"


def main(argv: list[str]) -> int:
    args = [
        "compare",
        "--baseline", BASELINE,
        "--candidate", CANDIDATE,
        "--out", OUT,
        *argv,
    ]
    return cli.main(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
