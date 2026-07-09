#!/usr/bin/env python3
"""Every quality gate, in one command (Milestone 13).

``docs/PLAN.md`` §11 lists the ten conditions this repository must meet to be
called complete. Each is a claim about the system, and until now each was checked
by a different command, in a different output format, by a human reading the
result. This script runs all ten in dependency order and prints one table.

    python3 scripts/10_run_all_gates.py [--only IDS] [--skip IDS] [--json]

Exit status is 1 if any **required** gate fails. ``lint`` is the sole optional
gate: ``ruff`` is a dev extra, and a host without it is not a broken host.

Four properties are what make the output trustworthy rather than merely green.

**Nothing is written into the repository.** The pipeline gates run the documented
reproduction commands into a scratch directory outside the tree, so ``clean_tree``
inspects the repository and not the leavings of the gate that ran before it. The
scratch directory is required to sit outside the repository for the same reason.

**The offline claim is enforced, not asserted.** ``smoke_offline`` executes the two
run scripts in a child interpreter whose ``socket.connect``, ``socket.connect_ex``
and ``socket.create_connection`` raise. A run that reaches for the network fails
the gate with a traceback rather than passing quietly on a host that happens to
have connectivity.

**The redaction gate is non-vacuous.** A scanner that matches nothing passes every
tree, including one with a key in it. ``redaction`` plants a synthetic credential
in a scratch file and requires the scanner to find *that* before it will accept an
empty finding list for the repository.

**The report gate compares bytes, not shapes.** The freshly rendered Markdown must
equal the committed ``docs/examples/regression_report.md`` byte for byte, which is
only possible because the report carries no timestamp.

``--json`` is deterministic: no durations, no timestamps, and the scratch path is
rewritten to ``<workdir>`` wherever it appears. Two runs on one commit produce
byte-identical JSON, so a gate report diffs like any other artifact here.

Two commands this script deliberately does not run: ``git push`` and ``git
remote``, both denied to the milestone runner by ``.claude/settings.json``.
``clean_tree`` reads remote-tracking branches with ``git branch -r`` instead.

The ``tests`` gate shells out to ``pytest``. Running it from inside ``pytest``
would fork the suite into itself, so it refuses — see :class:`GateRecursionError`.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_regression_observability.traces.redaction import (  # noqa: E402
    scan_text,
    scan_tree,
)

#: The scratch directory is stamped with this so the script only ever removes a
#: directory it created itself.
WORKDIR_MARKER = ".llmreg-gate-workdir"

#: The smoke suite: six injected regressions the detector must catch, and two
#: negative controls it must not flag. Kept here as literals, not derived from the
#: fixtures, so that a fixture edit which quietly changes what is detected breaks
#: this gate instead of being ratified by it.
EXPECTED_REGRESSIONS: dict[str, str] = {
    "sj_invalid_json": "structured_output_regression",
    "tu_wrong_tool": "tool_call_regression",
    "re_unsupported_claim": "evidence_grounding_regression",
    "rb_underrefuse": "refusal_behavior_shift",
    "lc_latency_spike": "latency_regression",
    "at_more_retries": "error_rate_regression",
}
EXPECTED_CONTROLS: tuple[str, ...] = ("at_infra_noise", "sj_control_reorder")
SMOKE_CASE_COUNT = len(EXPECTED_REGRESSIONS) + len(EXPECTED_CONTROLS)

#: `docs/PLAN.md` §11 requires the docs to explain these four things. The value is
#: the plan's word for the subject; the key is the document that owns it.
#:
#: What is checkable here is structural — the document exists, it is not a stub,
#: and the README links to it — because a gate cannot read English and a document
#: no reader can reach explains nothing. Whether the prose is any good is what the
#: human review notes under `docs/review_notes/` are for.
REQUIRED_DOCS: dict[str, str] = {
    "docs/METHODOLOGY.md": "methodology",
    "docs/METRICS.md": "metrics",
    "docs/LIMITATIONS.md": "limitations",
    "docs/REPRODUCIBILITY.md": "reproduction",
}

#: The pages `CLAUDE.md` requires. Milestones 10 and 11 added two more; those are
#: checked by `tests/test_docs.py`, which owns the website's contents.
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

#: A committed report may not ask the network for anything when opened.
EXTERNAL_ASSET_TOKENS: tuple[str, ...] = (
    "http://",
    "https://",
    "<script",
    "<link",
    "<img",
    "@import",
)

#: Network clients. None may be in `sys.modules` after importing the package.
NETWORK_CLIENTS: tuple[str, ...] = ("anthropic", "httpx", "requests", "openai")

MIN_DOC_BYTES = 500

# The child interpreter used by `smoke_offline`: a socket kill switch, then the
# requested script under `__main__`, so its `raise SystemExit(main(...))` sets the
# child's exit status. This mirrors the autouse fixture in `conftest.py`, which
# arms the same three seams for every test.
_OFFLINE_CHILD = '''\
import runpy
import socket
import sys


def _blocked(*_args, **_kwargs):
    raise AssertionError(
        "network access attempted during the smoke_offline gate: the smoke "
        "suite must run with no outbound socket at all"
    )


socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked

sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
'''

# The child interpreter used by `no_paid_api`: a fresh process, because in this one
# a test or an earlier gate may already have imported anything. `CLIENTS` is
# substituted rather than formatted — the source below is full of braces.
_NO_CLIENT_CHILD = '''\
import json
import sys

sys.path.insert(0, "src")

import llm_regression_observability  # noqa: F401
from llm_regression_observability.executor import build_adapter

stub = build_adapter("api-stub")
print(
    json.dumps(
        {
            "clients": sorted(set(CLIENTS) & set(sys.modules)),
            "enabled": bool(stub.enabled),
            "cache_enabled": bool(stub.cache.enabled),
        }
    )
)
'''


class GateError(RuntimeError):
    """Base class for the ways a gate run can be misdriven."""


class GateRecursionError(GateError):
    """Raised when the ``tests`` gate is selected from inside ``pytest``."""


class GateSelectionError(GateError):
    """Raised for an unknown gate id on ``--only`` / ``--skip``."""


class GateWorkdirError(GateError):
    """Raised when the scratch directory is unusable or unsafe to create."""


@dataclass
class GateContext:
    """What the gates share: the repository, a scratch dir, and prior artifacts."""

    repo_root: Path
    workdir: Path
    runs: dict[str, Path] = field(default_factory=dict)
    comparison_dir: Path | None = None
    report_md: Path | None = None
    report_html: Path | None = None


@dataclass(frozen=True)
class GateResult:
    """One gate's verdict. ``evidence`` is what a reader would check by hand."""

    gate_id: str
    claim: str
    required: bool
    ok: bool
    detail: str
    evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "gate_id": self.gate_id,
            "claim": self.claim,
            "required": self.required,
            "ok": self.ok,
            "detail": self.detail,
            "evidence": list(self.evidence),
        }


#: A check returns (ok, one-line detail, evidence lines).
CheckFn = Callable[[GateContext], "tuple[bool, str, tuple[str, ...]]"]


@dataclass(frozen=True)
class Gate:
    """A claim from ``docs/PLAN.md`` §11 and the command that settles it.

    ``claim`` is the plan's bullet, verbatim: ``tests/test_gates.py`` parses §11 and
    requires the required gates to reproduce those ten strings exactly, so a gate
    cannot drift away from the promise it is supposed to keep.
    """

    gate_id: str
    claim: str
    check: CheckFn
    requires: tuple[str, ...] = ()
    required: bool = True


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _run(args: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), cwd=cwd, capture_output=True, text=True, check=False
    )


def _tail(proc: subprocess.CompletedProcess[str], lines: int = 4) -> str:
    text = (proc.stdout + proc.stderr).strip()
    return " / ".join(text.splitlines()[-lines:]) if text else "no output"


def _first_int(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# The gates, in the order `docs/PLAN.md` §11 states them
# --------------------------------------------------------------------------- #


#: `python3 -m pytest -q`, made independent of `pyproject.toml`.
#:
#: `-o addopts=` matters more than it looks. This project's `addopts` is already
#: `-q`, so appending another one yields `-qq`, and at `-qq` pytest prints no
#: `N passed` summary line at all — the gate would then read a passing run as an
#: unparseable one. Clearing `addopts` runs exactly the command §11 names, whatever
#: the config happens to say. `-p no:cacheprovider` keeps the gate from writing
#: `.pytest_cache/` into a repository whose cleanliness it is about to assert.
_PYTEST_FLAGS: tuple[str, ...] = ("-q", "-o", "addopts=", "-p", "no:cacheprovider")
_PYTEST_ARGV: tuple[str, ...] = ("-m", "pytest", *_PYTEST_FLAGS)


def _check_tests(ctx: GateContext) -> tuple[bool, str, tuple[str, ...]]:
    if "PYTEST_CURRENT_TEST" in os.environ:
        raise GateRecursionError(
            "the `tests` gate shells out to `pytest`; running it from inside "
            "pytest would fork the suite into itself. Deselect it with "
            "--skip tests, or select the gates you mean with --only."
        )
    proc = _run([sys.executable, *_PYTEST_ARGV], cwd=ctx.repo_root)
    passed = _first_int(r"(\d+) passed", proc.stdout)
    failed = _first_int(r"(\d+) failed", proc.stdout)
    ok = proc.returncode == 0 and failed is None and passed is not None
    if not ok:
        return False, _tail(proc), ()
    return True, f"{passed} tests passed", ("`python3 -m pytest -q` exit 0",)


def _check_fixtures(ctx: GateContext) -> tuple[bool, str, tuple[str, ...]]:
    proc = _run(
        [sys.executable, "scripts/01_validate_fixtures.py", "evals/fixtures"],
        cwd=ctx.repo_root,
    )
    if proc.returncode != 0:
        return False, _tail(proc), ()
    counts = next(
        (ln.strip() for ln in proc.stdout.splitlines() if ln.startswith("cases:")),
        "",
    )
    secrets = next(
        (
            ln.strip()
            for ln in proc.stdout.splitlines()
            if ln.startswith("secrets scan:")
        ),
        "",
    )
    return True, counts or "fixtures validate", tuple(x for x in (secrets,) if x)


def _check_smoke_offline(ctx: GateContext) -> tuple[bool, str, tuple[str, ...]]:
    runs_dir = ctx.workdir / "runs"
    scripts = (
        ("baseline", "scripts/02_run_baseline.py", "smoke-baseline-recorded"),
        (
            "candidate",
            "scripts/03_run_candidate.py",
            "smoke-candidate-scripted-regression",
        ),
    )
    for label, script, run_id in scripts:
        proc = _run(
            [
                sys.executable,
                "-c",
                _OFFLINE_CHILD,
                script,
                "--out",
                str(runs_dir),
            ],
            cwd=ctx.repo_root,
        )
        if proc.returncode != 0:
            return False, f"{label} run failed offline: {_tail(proc)}", ()
        ctx.runs[label] = runs_dir / run_id

    missing = [label for label, path in ctx.runs.items() if not path.is_dir()]
    if missing:
        return False, f"run directory not created: {', '.join(missing)}", ()
    return (
        True,
        "both runs completed with outbound TCP disabled",
        (
            "socket.connect / connect_ex / create_connection raise in the child",
            "adapters used: recorded (baseline), scripted-regression (candidate)",
        ),
    )


def _check_runs_generated(ctx: GateContext) -> tuple[bool, str, tuple[str, ...]]:
    evidence: list[str] = []
    for label in ("baseline", "candidate"):
        run_dir = ctx.runs[label]
        records, meta_path = run_dir / "run.jsonl", run_dir / "run_meta.json"
        if not records.is_file() or not meta_path.is_file():
            return False, f"{label}: run.jsonl or run_meta.json missing", ()

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        digest = _sha256(records)
        if digest != meta.get("records_sha256"):
            return False, f"{label}: run.jsonl digest != run_meta.records_sha256", ()

        lines = records.read_text(encoding="utf-8").splitlines()
        count = len([ln for ln in lines if ln])
        if count != SMOKE_CASE_COUNT:
            return False, f"{label}: {count} records, expected {SMOKE_CASE_COUNT}", ()
        evidence.append(f"{label}: {count} records, digest matches run_meta.json")
    return (
        True,
        "baseline and candidate runs generated and self-consistent",
        tuple(evidence),
    )


def _check_comparison(ctx: GateContext) -> tuple[bool, str, tuple[str, ...]]:
    ctx.comparison_dir = ctx.workdir / "comparisons" / "smoke-baseline-vs-candidate"
    proc = _run(
        [
            sys.executable,
            "scripts/05_compare_runs.py",
            "--baseline",
            str(ctx.runs["baseline"]),
            "--candidate",
            str(ctx.runs["candidate"]),
            "--out",
            str(ctx.workdir / "comparisons"),
        ],
        cwd=ctx.repo_root,
    )
    if proc.returncode != 0:
        return False, _tail(proc), ()

    records_path = ctx.comparison_dir / "comparison.jsonl"
    if not records_path.is_file():
        return False, "comparison.jsonl not written", ()
    found = {
        record["case_id"]: tuple(f["category"] for f in record["regression_flags"])
        for record in (
            json.loads(ln)
            for ln in records_path.read_text(encoding="utf-8").splitlines()
            if ln
        )
    }

    if set(found) != set(EXPECTED_REGRESSIONS) | set(EXPECTED_CONTROLS):
        return False, f"unexpected case set: {sorted(found)}", ()
    missed = [
        case
        for case, category in EXPECTED_REGRESSIONS.items()
        if found[case] != (category,)
    ]
    if missed:
        return False, f"injected regression not detected as expected: {missed}", ()
    false_positives = [case for case in EXPECTED_CONTROLS if found[case]]
    if false_positives:
        return False, f"control case flagged: {false_positives}", ()

    # The same comparison, as a CI gate: `--fail-on critical` must exit 1 here.
    # `--dry-run` keeps it from writing a second copy of the artifacts.
    gate = _run(
        [
            sys.executable,
            "scripts/05_compare_runs.py",
            "--baseline",
            str(ctx.runs["baseline"]),
            "--candidate",
            str(ctx.runs["candidate"]),
            "--dry-run",
            "--fail-on",
            "critical",
        ],
        cwd=ctx.repo_root,
    )
    if gate.returncode != 1:
        return False, f"`--fail-on critical` exited {gate.returncode}, expected 1", ()

    return (
        True,
        f"{len(EXPECTED_REGRESSIONS)}/{len(EXPECTED_REGRESSIONS)} injected "
        f"regressions detected, 0 false positives on "
        f"{len(EXPECTED_CONTROLS)} controls",
        tuple(
            f"{case}: {category}" for case, category in EXPECTED_REGRESSIONS.items()
        )
        + tuple(f"{case}: no flag (control)" for case in EXPECTED_CONTROLS)
        + ("`--fail-on critical` exits 1 (the CI regression gate)",),
    )


def _check_report(ctx: GateContext) -> tuple[bool, str, tuple[str, ...]]:
    ctx.report_md = ctx.workdir / "reports" / "regression_report.md"
    ctx.report_html = ctx.report_md.with_suffix(".html")
    proc = _run(
        [
            sys.executable,
            "scripts/06_generate_report.py",
            "--comparison",
            str(ctx.comparison_dir),
            "--out",
            str(ctx.report_md),
            "--format",
            "both",
        ],
        cwd=ctx.repo_root,
    )
    if proc.returncode != 0:
        return False, _tail(proc), ()
    for path in (ctx.report_md, ctx.report_html):
        if not path.is_file():
            return False, f"{path.name} not written", ()

    committed = ctx.repo_root / "docs" / "examples" / "regression_report.md"
    if ctx.report_md.read_bytes() != committed.read_bytes():
        return (
            False,
            "rendered Markdown differs from docs/examples/regression_report.md",
            (),
        )

    html = ctx.report_html.read_text(encoding="utf-8")
    external = [token for token in EXTERNAL_ASSET_TOKENS if token in html]
    if external:
        return False, f"HTML report requests an external asset: {external}", ()

    return (
        True,
        "Markdown and HTML rendered from the comparison records",
        (
            "Markdown is byte-identical to docs/examples/regression_report.md",
            "HTML is self-contained: no script, stylesheet, image or web font",
        ),
    )


def _check_redaction(ctx: GateContext) -> tuple[bool, str, tuple[str, ...]]:
    findings = scan_tree(ctx.repo_root)
    if findings:
        # Never print the matched value, only where it is and what fired.
        located = ", ".join(f"{f.path}:{f.line} ({f.rule})" for f in findings[:3])
        return False, f"credential-like string in the tree: {located}", ()

    # A scanner that matches nothing would have passed the line above. Plant one.
    probe_dir = ctx.workdir / "redaction_probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    planted = "AKIA" + "EXAMPLEKEY123456"  # synthetic; split so this line is clean
    (probe_dir / "planted.txt").write_text(f"aws_key = {planted}\n", encoding="utf-8")
    probe = scan_tree(probe_dir)
    if not probe:
        return False, "the scanner found nothing in a tree with a planted key", ()

    if ctx.report_md is None:  # `requires=("report",)` should have prevented this
        return False, "no rendered report to scan: the report gate did not run", ()
    surviving = scan_text(ctx.report_md.read_text(encoding="utf-8"))
    if surviving:
        return False, f"the rendered report holds: {sorted(surviving)}", ()

    return (
        True,
        "repository clean; scanner proven live; report holds no credential",
        (
            "scan_tree(repo) -> 0 findings",
            f"scan_tree(planted synthetic key) -> {len(probe)} finding(s)",
            "the report gate runs redaction before it writes (reports/base.py)",
        ),
    )


def _check_docs(ctx: GateContext) -> tuple[bool, str, tuple[str, ...]]:
    readme = (ctx.repo_root / "README.md").read_text(encoding="utf-8")
    problems: list[str] = []
    for relative in REQUIRED_DOCS:
        path = ctx.repo_root / relative
        if not path.is_file():
            problems.append(f"{relative} missing")
        elif path.stat().st_size < MIN_DOC_BYTES:
            problems.append(f"{relative} is a stub ({path.stat().st_size} bytes)")
        elif relative not in readme:
            problems.append(f"{relative} is not linked from the README")

    problems.extend(
        f"site/{page} missing"
        for page in REQUIRED_PAGES
        if not (ctx.repo_root / "site" / page).is_file()
    )

    if problems:
        return False, "; ".join(problems[:3]), ()
    return (
        True,
        f"{len(REQUIRED_DOCS)} topic docs, {len(REQUIRED_PAGES)} website pages",
        tuple(
            f"{subject}: {doc}, linked from the README"
            for doc, subject in REQUIRED_DOCS.items()
        ),
    )


def _check_no_paid_api(ctx: GateContext) -> tuple[bool, str, tuple[str, ...]]:
    source = _NO_CLIENT_CHILD.replace("CLIENTS", repr(NETWORK_CLIENTS))
    proc = _run([sys.executable, "-c", source], cwd=ctx.repo_root)
    if proc.returncode != 0:
        return False, _tail(proc), ()
    probe = json.loads(proc.stdout)
    if probe["clients"]:
        return False, f"a network client was imported: {probe['clients']}", ()
    if probe["enabled"]:
        return False, "build_adapter('api-stub') returned an enabled adapter", ()
    if probe["cache_enabled"]:
        return False, "the disabled adapter built a live response cache", ()
    return (
        True,
        "no network client imported; the api-stub adapter builds inert",
        (
            f"sys.modules after import: none of {', '.join(NETWORK_CLIENTS)}",
            "build_adapter('api-stub').enabled is False",
            "its response cache is disabled, so importing touches no directory",
        ),
    )


def _check_clean_tree(ctx: GateContext) -> tuple[bool, str, tuple[str, ...]]:
    status = _run(["git", "status", "--porcelain"], cwd=ctx.repo_root)
    if status.returncode != 0:
        return False, f"git status failed: {_tail(status)}", ()
    dirty = [ln for ln in status.stdout.splitlines() if ln.strip()]
    if dirty:
        return False, f"{len(dirty)} uncommitted path(s): {dirty[0].strip()} ...", ()

    # `git remote` is on the runner's deny list; remote-tracking refs say the same
    # thing. No remote-tracking branch exists => nothing left this machine.
    remotes = _run(["git", "branch", "-r"], cwd=ctx.repo_root)
    if remotes.returncode != 0:
        return False, f"git branch -r failed: {_tail(remotes)}", ()
    tracked = [ln.strip() for ln in remotes.stdout.splitlines() if ln.strip()]
    if tracked:
        return False, f"remote-tracking branch present: {tracked[0]}", ()

    return (
        True,
        "working tree clean; no remote-tracking branch, so nothing was pushed",
        (
            "`git status --porcelain` is empty",
            "`git branch -r` is empty",
        ),
    )


def _check_lint(ctx: GateContext) -> tuple[bool, str, tuple[str, ...]]:
    if importlib.util.find_spec("ruff") is None:
        return True, "ruff not installed (optional dev extra); lint not run", ()
    proc = _run([sys.executable, "-m", "ruff", "check", "."], cwd=ctx.repo_root)
    if proc.returncode != 0:
        return False, _tail(proc, lines=2), ()
    return True, "ruff check reports no finding", ("rules: E, F, I, UP, B",)


GATES: tuple[Gate, ...] = (
    Gate("tests", "`python3 -m pytest -q` passes.", _check_tests),
    Gate(
        "fixtures",
        "Fixtures validate (`llmreg validate-fixtures`).",
        _check_fixtures,
    ),
    Gate("smoke_offline", "The smoke suite runs fully offline.", _check_smoke_offline),
    Gate(
        "runs_generated",
        "Baseline and candidate runs are generated.",
        _check_runs_generated,
        requires=("smoke_offline",),
    ),
    Gate(
        "comparison",
        "Comparison detects the intentional (scripted) regressions.",
        _check_comparison,
        requires=("runs_generated",),
    ),
    Gate(
        "report",
        "A report is generated from comparison records.",
        _check_report,
        requires=("comparison",),
    ),
    Gate(
        "redaction",
        "Redaction works and runs before reports are written.",
        _check_redaction,
        requires=("report",),
    ),
    Gate(
        "docs",
        "Docs explain methodology, metrics, limitations, and reproduction.",
        _check_docs,
    ),
    Gate(
        "no_paid_api",
        "No paid API is required anywhere in the default path.",
        _check_no_paid_api,
    ),
    Gate("clean_tree", "Working tree is clean; nothing is pushed.", _check_clean_tree),
    Gate(
        "lint",
        "Lint is clean (`ruff`, an optional dev tool).",
        _check_lint,
        required=False,
    ),
)

GATE_IDS: tuple[str, ...] = tuple(gate.gate_id for gate in GATES)


# --------------------------------------------------------------------------- #
# Selection, execution, reporting
# --------------------------------------------------------------------------- #


def select_gates(
    only: Sequence[str] | None = None, skip: Sequence[str] = ()
) -> tuple[Gate, ...]:
    """The gates to run, in registry order, with prerequisites pulled in.

    ``--only report`` therefore runs the whole pipeline that produces a report.
    ``--skip`` is applied last and wins: skipping a prerequisite does not remove
    its dependants, it makes them fail with a "prerequisite did not pass" detail,
    which is the honest outcome rather than a silent pass.
    """
    by_id = {gate.gate_id: gate for gate in GATES}
    unknown = sorted({*(only or ()), *skip} - set(by_id))
    if unknown:
        raise GateSelectionError(
            f"unknown gate id(s): {', '.join(unknown)}. "
            f"Known: {', '.join(GATE_IDS)}"
        )

    chosen = set(only) if only else set(by_id)
    frontier = list(chosen)
    while frontier:
        for requirement in by_id[frontier.pop()].requires:
            if requirement not in chosen:
                chosen.add(requirement)
                frontier.append(requirement)

    chosen -= set(skip)
    return tuple(gate for gate in GATES if gate.gate_id in chosen)


def run_gates(ctx: GateContext, gates: Sequence[Gate]) -> tuple[GateResult, ...]:
    """Run ``gates`` in order. A gate whose prerequisite failed is not run."""
    results: list[GateResult] = []
    passed: set[str] = set()
    for gate in gates:
        unmet = [req for req in gate.requires if req not in passed]
        if unmet:
            results.append(
                GateResult(
                    gate.gate_id,
                    gate.claim,
                    gate.required,
                    False,
                    f"not run: prerequisite gate(s) did not pass: {', '.join(unmet)}",
                )
            )
            continue

        ok, detail, evidence = gate.check(ctx)
        if ok:
            passed.add(gate.gate_id)
        scratch = str(ctx.workdir)
        results.append(
            GateResult(
                gate.gate_id,
                gate.claim,
                gate.required,
                ok,
                detail.replace(scratch, "<workdir>"),
                tuple(line.replace(scratch, "<workdir>") for line in evidence),
            )
        )
    return tuple(results)


def payload(results: Sequence[GateResult]) -> dict[str, object]:
    """The ``--json`` document: no clock, no scratch path, stable key order."""
    failed = [r.gate_id for r in results if r.required and not r.ok]
    return {
        "ok": not failed,
        "required_failed": failed,
        "gates": [result.as_dict() for result in results],
    }


def open_workdir(explicit: Path | None) -> tuple[Path, bool]:
    """Create the scratch directory. Returns it and whether we may remove it.

    It must live outside the repository: the ``clean_tree`` gate inspects the
    repository, the ``redaction`` gate plants a synthetic credential in here, and
    neither wants to meet the other's artifacts.
    """
    if explicit is None:
        return Path(tempfile.mkdtemp(prefix="llmreg-gates-")), True

    workdir = explicit.resolve()
    if workdir == REPO_ROOT or REPO_ROOT in workdir.parents:
        raise GateWorkdirError(
            f"--workdir must sit outside the repository, got {workdir}"
        )
    if workdir.exists():
        if not (workdir / WORKDIR_MARKER).is_file():
            raise GateWorkdirError(
                f"{workdir} exists and was not created by this script; "
                "point --workdir somewhere else"
            )
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    (workdir / WORKDIR_MARKER).touch()
    return workdir, True


def _render(results: Sequence[GateResult], workdir: Path, kept: bool) -> None:
    required = [r for r in results if r.required]
    print("llm-regression-observability — quality gates (docs/PLAN.md §11)")
    print("-" * 72)
    for result in results:
        if result.ok:
            mark = "ok "
        else:
            mark = "!! " if result.required else "-- "
        suffix = "" if result.required else "  (optional)"
        print(f"  [{mark}] {result.gate_id}{suffix}")
        print(f"        claim  : {result.claim}")
        print(f"        result : {result.detail}")
        for line in result.evidence:
            print(f"                 - {line}")
    print("-" * 72)
    passing = sum(1 for r in required if r.ok)
    print(f"required gates   : {passing}/{len(required)} passing")
    if kept:
        print(f"workdir          : {workdir}")
    if passing == len(required):
        print("result           : PASS")
    else:
        failed = ", ".join(r.gate_id for r in required if not r.ok)
        print(f"result           : FAIL ({failed})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="10_run_all_gates.py",
        description="Run every quality gate in docs/PLAN.md §11.",
    )
    parser.add_argument(
        "--only",
        metavar="IDS",
        help=f"comma-separated gate ids to run ({', '.join(GATE_IDS)}); "
        "prerequisites are pulled in automatically",
    )
    parser.add_argument(
        "--skip", metavar="IDS", default="", help="comma-separated gate ids to skip"
    )
    parser.add_argument(
        "--json", action="store_true", help="machine-readable, deterministic output"
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        help="scratch directory (must be outside the repository); "
        "default: a fresh temporary directory",
    )
    parser.add_argument(
        "--keep-workdir",
        action="store_true",
        help="do not remove the scratch directory when finished",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    only = [gid for gid in (args.only or "").split(",") if gid] or None
    skip = [gid for gid in args.skip.split(",") if gid]

    try:
        gates = select_gates(only, skip)
        workdir, removable = open_workdir(args.workdir)
    except GateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    ctx = GateContext(repo_root=REPO_ROOT, workdir=workdir)
    try:
        results = run_gates(ctx, gates)
    except GateRecursionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if removable and not args.keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)

    document = payload(results)
    if args.json:
        print(json.dumps(document, indent=2, sort_keys=True))
    else:
        _render(results, workdir, kept=args.keep_workdir)
    return 0 if document["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
