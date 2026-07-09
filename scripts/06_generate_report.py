#!/usr/bin/env python3
"""Render the smoke comparison as a Markdown + HTML regression report (Milestone 7).

Thin wrapper over ``llmreg report`` so the report is reproducible without an
editable install (it prepends ``src/`` to the path, like ``conftest.py``).

    python3 scripts/06_generate_report.py [-- EXTRA llmreg report ARGS]

Reads ``results/comparisons/smoke-baseline-vs-candidate/`` (written by
``scripts/05_compare_runs.py``) and writes
``results/reports/regression_report.md`` and ``regression_report.html``.

Any extra arguments are appended verbatim, so the defaults can be overridden:

    python3 scripts/06_generate_report.py --dry-run
    python3 scripts/06_generate_report.py --format markdown --top 0

Numbered ``06`` rather than ``05``: ``scripts/05_compare_runs.py`` (Milestone 6)
already holds that slot, which ``docs/milestones/07-reports.md`` did not know when
it named this file ``05_generate_report.py``. The milestone note records the
deviation.

Offline, CPU-only, deterministic: the report carries no timestamp, so two
invocations on the same comparison artifacts produce byte-identical files.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from llm_regression_observability import cli  # noqa: E402

COMPARISON = "results/comparisons/smoke-baseline-vs-candidate"
OUT = "results/reports/regression_report.md"


def main(argv: list[str]) -> int:
    args = [
        "report",
        "--comparison", COMPARISON,
        "--out", OUT,
        "--format", "both",
        *argv,
    ]
    return cli.main(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
