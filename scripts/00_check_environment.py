#!/usr/bin/env python3
"""Environment preflight for llm-regression-observability.

Standalone (imports nothing from the package, so it works before Milestone 0 has
created the package). Verifies the host can run this CPU-only, offline project:

  * Python >= 3.12
  * required standard-library modules (the core has no third-party deps)
  * tooling availability (reported, not required, except pytest for the suite)
  * enough free disk
  * no network is needed

Exits non-zero only on a hard failure (wrong Python, missing stdlib module).
Optional tooling and low disk are reported as warnings, not failures.

    python3 scripts/00_check_environment.py [--json]
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

MIN_PYTHON = (3, 12)
REQUIRED_STDLIB = [
    "dataclasses", "json", "argparse", "sqlite3", "difflib",
    "hashlib", "pathlib", "statistics", "csv", "re", "http.server",
]
# Reported for convenience; only pytest matters for running the suite.
OPTIONAL_TOOLS = ["pytest", "ruff", "git", "claude"]
MIN_FREE_MB = 200


def _import_ok(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def collect() -> dict:
    checks: list[dict] = []
    ok = True

    # Python version (hard requirement).
    py_ok = sys.version_info[:2] >= MIN_PYTHON
    ok = ok and py_ok
    checks.append({
        "name": "python_version",
        "level": "required",
        "ok": py_ok,
        "detail": f"{sys.version_info.major}.{sys.version_info.minor} "
                  f"(need >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]})",
    })

    # Required stdlib modules (hard requirement).
    for mod in REQUIRED_STDLIB:
        mod_ok = _import_ok(mod)
        ok = ok and mod_ok
        checks.append({
            "name": f"stdlib:{mod}", "level": "required",
            "ok": mod_ok, "detail": "importable" if mod_ok else "MISSING",
        })

    # Optional tooling (reported only).
    for tool in OPTIONAL_TOOLS:
        if tool == "pytest":
            present = _import_ok("pytest")
        else:
            present = shutil.which(tool) is not None
        checks.append({
            "name": f"tool:{tool}", "level": "optional",
            "ok": present,
            "detail": "available" if present else "not found (optional)",
        })

    # Free disk (warning only).
    free_mb = shutil.disk_usage(Path(__file__).resolve().parent).free // (1024 * 1024)
    checks.append({
        "name": "free_disk_mb", "level": "warn",
        "ok": free_mb >= MIN_FREE_MB,
        "detail": f"{free_mb} MB free (recommended >= {MIN_FREE_MB} MB)",
    })

    # Third-party (optional) — informational.
    for pkg in ["pydantic", "pandas"]:
        present = _import_ok(pkg)
        detail = "installed" if present else "absent (optional; core needs neither)"
        checks.append({
            "name": f"optional_pkg:{pkg}", "level": "info",
            "ok": present, "detail": detail,
        })

    return {"ok": ok, "network_required": False, "checks": checks}


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    report = collect()

    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["ok"] else 1

    print("llm-regression-observability — environment preflight")
    print("-" * 56)
    for c in report["checks"]:
        mark = "ok " if c["ok"] else ("!! " if c["level"] == "required" else "-- ")
        print(f"  [{mark}] {c['name']:<24} {c['detail']}")
    print("-" * 56)
    print("network required : no (offline, CPU-only)")
    if report["ok"]:
        print("result           : PASS")
        if not _import_ok("pytest"):
            print("note             : install pytest to run the suite "
                  "(`pip install --user pytest`).")
        return 0
    print("result           : FAIL (a required check did not pass)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
