"""Command-line interface for llm-regression-observability (``llmreg``).

Milestone 0 exposes two subcommands so the packaging and entry points can be
exercised end to end:

* ``version``   — print the package version.
* ``check-env`` — a lightweight, offline check that the interpreter and the
  required standard-library modules are present. This is intentionally smaller
  than ``scripts/00_check_environment.py`` (the full host preflight); it exists
  so the CLI itself is testable and self-contained.

Milestone 2 adds ``validate-fixtures``. Later milestones add ``run-suite``,
``compare``, and ``report`` (see ``docs/PLAN.md``). This module stays
dependency-free and calls no network, clock, or randomness in any scored path.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from . import __summary__, __version__

# Standard-library modules the dependency-free core relies on. Kept in sync with
# ``docs/PLAN.md`` §3 and ``scripts/00_check_environment.py``.
REQUIRED_STDLIB: tuple[str, ...] = (
    "dataclasses",
    "json",
    "argparse",
    "sqlite3",
    "difflib",
    "hashlib",
    "pathlib",
    "statistics",
)
MIN_PYTHON: tuple[int, int] = (3, 12)


def _import_ok(name: str) -> bool:
    """Return True if ``name`` imports; never raises."""
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _cmd_version(_args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def _cmd_check_env(_args: argparse.Namespace) -> int:
    import sys

    py = sys.version_info
    py_ok = py[:2] >= MIN_PYTHON
    print(
        f"python           : {py.major}.{py.minor}.{py.micro} "
        f"({'ok' if py_ok else f'need >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}'})"
    )

    missing = [m for m in REQUIRED_STDLIB if not _import_ok(m)]
    if missing:
        print(f"stdlib modules   : MISSING {', '.join(missing)}")
    else:
        print(f"stdlib modules   : all {len(REQUIRED_STDLIB)} present")

    print("network required : no (offline, CPU-only)")
    print(f"package version  : {__version__}")

    ok = py_ok and not missing
    print(f"result           : {'PASS' if ok else 'FAIL'}")
    if ok:
        print(
            "note             : full host preflight -> "
            "python3 scripts/00_check_environment.py"
        )
    return 0 if ok else 1


def _cmd_validate_fixtures(args: argparse.Namespace) -> int:
    # Imported lazily so ``version`` / ``check-env`` do not pay for it.
    from .datasets.validation import validate_fixtures

    report = validate_fixtures(args.path)
    for suite in report.suites_checked:
        print(f"suite            : {suite} (ok)")
    if not report.suites_checked:
        print("suite            : none found")
    print(
        f"cases            : {report.case_count} "
        f"({report.defect_count} with injected defects, "
        f"{report.control_count} controls)"
    )
    if report.issues:
        print(f"issues           : {len(report.issues)}")
        for issue in report.issues:
            print(f"  - {issue.render()}")
        print("result           : FAIL")
        return 1
    print("secrets scan     : clean (no credential-like patterns)")
    print("result           : PASS")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the ``llmreg`` argument parser."""
    parser = argparse.ArgumentParser(prog="llmreg", description=__summary__)
    parser.add_argument(
        "--version",
        action="version",
        version=f"llmreg {__version__}",
        help="print the package version and exit",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_version = sub.add_parser("version", help="print the package version")
    p_version.set_defaults(func=_cmd_version)

    p_env = sub.add_parser(
        "check-env",
        help="offline check of the interpreter and required stdlib modules",
    )
    p_env.set_defaults(func=_cmd_check_env)

    p_fixtures = sub.add_parser(
        "validate-fixtures",
        help="validate the synthetic fixture suites (schemas, ground truth, safety)",
    )
    p_fixtures.add_argument(
        "path",
        nargs="?",
        default=None,
        help="path to evals/fixtures (or the repo root); default: repo fixtures",
    )
    p_fixtures.set_defaults(func=_cmd_validate_fixtures)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code (0 on success)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())
