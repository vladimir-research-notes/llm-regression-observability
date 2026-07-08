#!/usr/bin/env python3
"""Validate the synthetic fixture suites (Milestone 2).

Thin wrapper over ``llm_regression_observability.datasets.validate_fixtures`` so
the check is runnable without an editable install (it prepends ``src/`` to the
path, like ``conftest.py``). Offline, CPU-only, no third-party dependencies.

    python3 scripts/01_validate_fixtures.py [PATH] [--json]

PATH defaults to ``evals/fixtures`` at the repo root. Exits non-zero if any
fixture fails to parse, referential integrity breaks, an injected defect is not
actually present, a manifest reference dangles, or a credential-like string is
found.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from llm_regression_observability.datasets.validation import (  # noqa: E402
    validate_fixtures,
)


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--json"]
    as_json = "--json" in argv
    path = args[0] if args else None

    report = validate_fixtures(path)

    if as_json:
        print(
            json.dumps(
                {
                    "ok": report.ok,
                    "suites_checked": report.suites_checked,
                    "case_count": report.case_count,
                    "defect_count": report.defect_count,
                    "control_count": report.control_count,
                    "issues": [
                        {
                            "suite": i.suite,
                            "case_id": i.case_id,
                            "kind": i.kind,
                            "message": i.message,
                        }
                        for i in report.issues
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if report.ok else 1

    print("llm-regression-observability — fixture validation")
    print("-" * 56)
    for suite in report.suites_checked:
        print(f"  [ok ] {suite}")
    print("-" * 56)
    print(
        f"cases: {report.case_count}  "
        f"defects: {report.defect_count}  controls: {report.control_count}"
    )
    if report.issues:
        for issue in report.issues:
            print(f"  [!!] {issue.render()}")
        print(f"result: FAIL ({len(report.issues)} issue(s))")
        return 1
    print("secrets scan: clean")
    print("result: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
