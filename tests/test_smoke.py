"""Smoke tests for the Milestone 0 scaffold.

CPU-only and offline: they import the package and exercise the ``llmreg`` CLI
entry points (``version``, ``check-env``, ``--help``) without any network,
clock, or randomness.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import llm_regression_observability as lro
from llm_regression_observability.cli import build_parser, main

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def test_package_exposes_version() -> None:
    assert isinstance(lro.__version__, str)
    assert _SEMVER.match(lro.__version__), lro.__version__


def test_parser_builds_with_subcommands() -> None:
    parser = build_parser()
    assert parser.prog == "llmreg"
    # argparse stores subparser choices on the subparsers action.
    choices: set[str] = set()
    for action in parser._actions:  # noqa: SLF001 - inspecting parser config
        if hasattr(action, "choices") and action.choices:
            choices.update(action.choices)
    assert {"version", "check-env"} <= choices


def test_version_command(capsys) -> None:
    rc = main(["version"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == lro.__version__


def test_check_env_passes_on_this_host(capsys) -> None:
    # We are running under pytest on a supported interpreter, so this must pass.
    rc = main(["check-env"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "result           : PASS" in out
    assert "network required : no" in out


def test_no_args_prints_help_and_succeeds(capsys) -> None:
    rc = main([])
    assert rc == 0
    assert "llmreg" in capsys.readouterr().out


def test_help_exits_zero() -> None:
    import pytest

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_module_entry_point_help() -> None:
    """`python3 -m llm_regression_observability --help` works via src shim."""
    result = subprocess.run(
        [sys.executable, "-m", "llm_regression_observability", "--help"],
        cwd=REPO_ROOT,
        env={"PYTHONPATH": str(SRC_DIR)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "llmreg" in result.stdout


def test_module_entry_point_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "llm_regression_observability", "version"],
        cwd=REPO_ROOT,
        env={"PYTHONPATH": str(SRC_DIR)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == lro.__version__
