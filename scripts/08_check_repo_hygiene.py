#!/usr/bin/env python3
"""Repository hygiene: no credential-like string in a committed file.

Scans the source tree with the certain-tier rules from
``llm_regression_observability.traces.redaction`` — the same table the report gate,
the fixture validator and the span writer use. Heuristic rules (emails, generic
high-entropy tokens) are *not* applied here: they would flag every SHA-256 digest in
``docs/`` and every author email in ``pyproject.toml``, and a hygiene check that
cries wolf is a hygiene check that gets skipped.

A file that must legitimately name a credential shape (a test asserting the scanner
works, a doc showing a pattern) marks the line with ``redaction:allow``.

    python3 scripts/08_check_repo_hygiene.py [PATH] [--json]

Exits 1 if anything is found. Prints the file, the line and the pattern name —
never the matched value.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_regression_observability.traces.redaction import (  # noqa: E402
    ALLOW_MARKER,
    CERTAIN_RULES,
    scan_tree,
)


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    positional = [arg for arg in argv if not arg.startswith("-")]
    root = Path(positional[0]).resolve() if positional else REPO_ROOT

    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1

    findings = scan_tree(root)

    if as_json:
        print(
            json.dumps(
                {"ok": not findings, "root": str(root),
                 "findings": [asdict(f) for f in findings]},
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if not findings else 1

    print("llm-regression-observability — repository hygiene")
    print("-" * 56)
    print(f"  root          : {root}")
    print(f"  rules         : {len(CERTAIN_RULES)} certain-tier credential patterns")
    print(f"  allow marker  : a line containing '{ALLOW_MARKER}' is skipped")
    print("-" * 56)
    if findings:
        for finding in findings:
            print(f"  [!!] {finding.render()}")
        print("-" * 56)
        print(f"result           : FAIL ({len(findings)} finding(s))")
        return 1
    print("  [ok ] no credential-like pattern in a committed file")
    print("-" * 56)
    print("result           : PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
