"""Tests for the quality-gate runner (Milestone 13).

``scripts/10_run_all_gates.py`` is the script that decides whether this repository
is finished. It is therefore the one script whose *own* correctness nothing else
checks — a gate runner that silently passes everything looks exactly like a
repository in good order. This file is what stands between those two states.

Three kinds of test live here.

**The registry is the plan.** ``test_required_gates_reproduce_the_plans_quality_gates``
parses §11 of ``docs/PLAN.md`` and requires the ten required gates to reproduce its
ten bullets verbatim, in order. Edit the plan without editing the gates — or the
reverse — and this fails. The gates cannot drift away from the promises they keep.

**Every gate can fail.** A check that cannot fail is not a check. Each gate is
driven against a deliberately broken tree (a missing doc, a planted credential, a
dirty working tree) and required to notice. The redaction gate gets this treatment
twice, because it is itself a non-vacuity argument about a scanner.

**The runner's own guarantees hold.** Prerequisites are pulled in, ``--skip`` beats
that pull, the scratch directory refuses to live inside the repository, the
``--json`` document carries no clock and no scratch path, and the ``tests`` gate
refuses to run under ``pytest`` rather than forking the suite into itself.

The ``tests`` gate is never executed here, only its refusal. Nothing in this file
shells out to ``pytest``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATES_SCRIPT = REPO_ROOT / "scripts" / "10_run_all_gates.py"
PLAN = REPO_ROOT / "docs" / "PLAN.md"


def _load_gates_module() -> ModuleType:
    """Import the script by path: ``10_run_all_gates`` is not an identifier.

    Registered in ``sys.modules`` before execution because ``@dataclass`` resolves
    ``cls.__module__`` through it, and an unregistered module resolves to ``None``.
    """
    spec = importlib.util.spec_from_file_location("_gate_runner", GATES_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gates = _load_gates_module()


def _plan_quality_gates() -> tuple[str, ...]:
    """The bullets under ``## 11. Quality gates`` in ``docs/PLAN.md``."""
    text = PLAN.read_text(encoding="utf-8")
    match = re.search(r"^## 11\. Quality gates$(.*?)^## ", text, re.M | re.S)
    assert match, "docs/PLAN.md no longer has a '## 11. Quality gates' section"
    return tuple(
        line[2:].strip()
        for line in match.group(1).splitlines()
        if line.startswith("- ")
    )


def _context(tmp_path: Path, repo_root: Path | None = None) -> gates.GateContext:
    workdir = tmp_path / "workdir"
    workdir.mkdir(exist_ok=True)
    return gates.GateContext(repo_root=repo_root or REPO_ROOT, workdir=workdir)


# --------------------------------------------------------------------------- #
# The registry is the plan
# --------------------------------------------------------------------------- #


def test_required_gates_reproduce_the_plans_quality_gates() -> None:
    # The load-bearing test in this file. `docs/PLAN.md` §11 is the contract; the
    # required gates are its implementation. They are compared as strings, in
    # order, so neither can be edited quietly.
    claims = tuple(gate.claim for gate in gates.GATES if gate.required)
    assert claims == _plan_quality_gates()


def test_the_plan_states_ten_quality_gates() -> None:
    # Guards the test above: a regex that matched nothing would make it vacuous.
    assert len(_plan_quality_gates()) == 10


def test_the_optional_gate_promises_nothing_the_plan_promised() -> None:
    optional = [gate for gate in gates.GATES if not gate.required]
    assert [gate.gate_id for gate in optional] == ["lint"]
    assert optional[0].claim not in _plan_quality_gates()


def test_gate_ids_are_unique_and_every_prerequisite_exists() -> None:
    ids = [gate.gate_id for gate in gates.GATES]
    assert len(ids) == len(set(ids))
    for gate in gates.GATES:
        for requirement in gate.requires:
            assert requirement in ids, f"{gate.gate_id} requires unknown {requirement}"


def test_prerequisites_are_declared_before_their_dependants() -> None:
    # `run_gates` walks the registry once, so a prerequisite listed later would
    # never have run by the time its dependant is reached.
    seen: set[str] = set()
    for gate in gates.GATES:
        assert set(gate.requires) <= seen, f"{gate.gate_id} requires a later gate"
        seen.add(gate.gate_id)


def test_the_smoke_expectations_cover_the_whole_suite() -> None:
    overlap = set(gates.EXPECTED_REGRESSIONS) & set(gates.EXPECTED_CONTROLS)
    assert not overlap
    assert gates.SMOKE_CASE_COUNT == 8
    # Six injected regressions, each a distinct taxonomy category.
    assert len(set(gates.EXPECTED_REGRESSIONS.values())) == 6


def test_the_gate_runner_never_invokes_a_denied_git_command() -> None:
    # `.claude/settings.json` denies `git push` and `git remote` to the milestone
    # runner. A script that shells out to them would be an end-run around that.
    source = GATES_SCRIPT.read_text(encoding="utf-8")
    for command in ('"push"', '"remote"'):
        assert command not in source, f"the gate runner shells out to git {command}"


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


def test_selecting_a_gate_pulls_in_its_prerequisites() -> None:
    selected = [gate.gate_id for gate in gates.select_gates(["report"])]
    assert selected == ["smoke_offline", "runs_generated", "comparison", "report"]


def test_selection_preserves_registry_order() -> None:
    selected = [gate.gate_id for gate in gates.select_gates(["clean_tree", "docs"])]
    assert selected == ["docs", "clean_tree"]


def test_no_selection_runs_every_gate() -> None:
    assert len(gates.select_gates()) == len(gates.GATES)


def test_skip_beats_the_prerequisite_pull() -> None:
    selected = gates.select_gates(["report"], skip=["comparison"])
    assert "comparison" not in [gate.gate_id for gate in selected]
    assert "report" in [gate.gate_id for gate in selected]


def test_an_unmet_prerequisite_does_not_run_its_dependant(tmp_path: Path) -> None:
    # Reported as "not run", never as a pass: a prerequisite that was skipped must
    # not silently green-light the gate that depended on it. Driven with stand-in
    # gates, so this asserts the runner's bookkeeping and not the pipeline's.
    ran: list[str] = []

    def _spy(name: str) -> gates.CheckFn:
        def _check(_ctx: gates.GateContext) -> tuple[bool, str, tuple[str, ...]]:
            ran.append(name)
            return True, "ok", ()

        return _check

    dependant = gates.Gate("report", "…", _spy("report"), requires=("comparison",))
    results = gates.run_gates(_context(tmp_path), [dependant])

    assert ran == []  # the check never ran
    assert not results[0].ok
    assert "prerequisite" in results[0].detail
    assert "comparison" in results[0].detail


def test_a_failed_prerequisite_does_not_run_its_dependant(tmp_path: Path) -> None:
    # The same rule when the prerequisite ran and failed, rather than being skipped.
    broken = gates.Gate("comparison", "…", lambda _c: (False, "detector broke", ()))
    dependant = gates.Gate(
        "report", "…", lambda _c: (True, "ok", ()), requires=("comparison",)
    )
    results = gates.run_gates(_context(tmp_path), [broken, dependant])
    assert [r.ok for r in results] == [False, False]
    assert "prerequisite" in results[1].detail


@pytest.mark.parametrize("bad", ["nonsuch", "Tests", "clean-tree"])
def test_unknown_gate_ids_are_rejected(bad: str) -> None:
    with pytest.raises(gates.GateSelectionError, match="unknown gate id"):
        gates.select_gates([bad])


def test_an_unknown_skip_id_is_rejected_too() -> None:
    # A typo in `--skip` must not silently run the gate it meant to remove.
    with pytest.raises(gates.GateSelectionError, match="unknown gate id"):
        gates.select_gates(skip=["clean-tree"])


# --------------------------------------------------------------------------- #
# The runner's own guarantees
# --------------------------------------------------------------------------- #


def test_the_tests_gate_refuses_to_run_inside_pytest(tmp_path: Path) -> None:
    # We are inside pytest right now: PYTEST_CURRENT_TEST is set. Shelling out to
    # `pytest -q` here would fork the suite into itself.
    with pytest.raises(gates.GateRecursionError, match="fork the suite into itself"):
        gates._check_tests(_context(tmp_path))


def _pytest_summary_for(tmp_path: Path, flags: list[str]) -> str | None:
    """Run pytest with ``flags`` over one trivial test; return its ``N passed``.

    ``-c pyproject.toml`` is what makes this a real reproduction. Pointed at a file
    under ``tmp_path``, pytest would otherwise take ``/tmp`` as its rootdir, never
    load this project's config, and never apply the ``addopts`` that causes the bug.
    A throwaway test file, rather than the suite, keeps this from forking pytest
    into itself — it costs a process start and one assertion.
    """
    trivial = tmp_path / "test_trivial.py"
    trivial.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-c", "pyproject.toml", *flags, str(trivial)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def test_the_tests_gates_pytest_invocation_prints_a_summary_line(
    tmp_path: Path,
) -> None:
    """The `tests` gate reads `N passed` out of pytest's output. It must be there.

    This project's `addopts` is already `-q`, so a gate that appends its own `-q`
    runs at `-qq` — where pytest prints no summary line at all, and the gate scores
    a passing suite as an unparseable one. That shipped, and the first full gate run
    is what caught it. `-o addopts=` is the fix.
    """
    stdout = _pytest_summary_for(tmp_path, list(gates._PYTEST_FLAGS))
    assert gates._first_int(r"(\d+) passed", stdout) == 1, stdout[-400:]


def test_without_neutralizing_addopts_the_summary_line_disappears(
    tmp_path: Path,
) -> None:
    # The other direction, so the test above cannot pass for the wrong reason: `-q`
    # on the command line plus `-q` from the config is `-qq`, and `-qq` says nothing.
    stdout = _pytest_summary_for(tmp_path, ["-q", "-p", "no:cacheprovider"])
    assert gates._first_int(r"(\d+) passed", stdout) is None, stdout[-400:]


def test_the_tests_gate_does_not_write_a_cache_into_the_repository() -> None:
    # The gate run asserts the tree is clean; it must not dirty it on the way.
    assert "no:cacheprovider" in gates._PYTEST_FLAGS


def test_a_workdir_inside_the_repository_is_refused() -> None:
    with pytest.raises(gates.GateWorkdirError, match="outside the repository"):
        gates.open_workdir(REPO_ROOT / "results" / "scratch")
    with pytest.raises(gates.GateWorkdirError, match="outside the repository"):
        gates.open_workdir(REPO_ROOT)


def test_a_workdir_the_script_did_not_create_is_refused(tmp_path: Path) -> None:
    # Otherwise `--workdir ~/important` would be a delete command.
    existing = tmp_path / "not-ours"
    existing.mkdir()
    (existing / "precious.txt").write_text("do not remove me\n", encoding="utf-8")
    with pytest.raises(gates.GateWorkdirError, match="was not created by this script"):
        gates.open_workdir(existing)
    assert (existing / "precious.txt").is_file()


def test_a_workdir_the_script_did_create_is_reused(tmp_path: Path) -> None:
    target = tmp_path / "ours"
    first, removable = gates.open_workdir(target)
    assert removable and (first / gates.WORKDIR_MARKER).is_file()
    (first / "stale.txt").write_text("from a previous run\n", encoding="utf-8")

    second, _ = gates.open_workdir(target)
    assert second == first
    assert not (second / "stale.txt").exists()  # wiped, because we own it


def test_the_json_payload_carries_no_clock_and_no_scratch_path(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    scratch = str(ctx.workdir)

    def _leaky(_ctx: gates.GateContext) -> tuple[bool, str, tuple[str, ...]]:
        return True, f"wrote {scratch}/run.jsonl", (f"scanned {scratch}",)

    leaky = gates.Gate("docs", "Docs explain …", _leaky)
    document = gates.payload(gates.run_gates(ctx, [leaky]))
    serialized = json.dumps(document)

    assert scratch not in serialized
    assert "<workdir>/run.jsonl" in document["gates"][0]["detail"]
    assert document["gates"][0]["evidence"] == ["scanned <workdir>"]


def test_a_failing_required_gate_fails_the_run(tmp_path: Path) -> None:
    failing = gates.Gate("docs", "Docs explain …", lambda _ctx: (False, "no", ()))
    document = gates.payload(gates.run_gates(_context(tmp_path), [failing]))
    assert document["ok"] is False
    assert document["required_failed"] == ["docs"]


def test_a_failing_optional_gate_does_not_fail_the_run(tmp_path: Path) -> None:
    # `ruff` is a dev extra. A host without it is not a broken host — but the
    # failure is still reported, rather than being rewritten into a pass.
    failing = gates.Gate(
        "lint", "Lint is clean …", lambda _ctx: (False, "E501", ()), required=False
    )
    results = gates.run_gates(_context(tmp_path), [failing])
    document = gates.payload(results)

    assert results[0].ok is False
    assert document["ok"] is True
    assert document["required_failed"] == []


# --------------------------------------------------------------------------- #
# The offline child: the `smoke_offline` gate's enforcement mechanism
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "attempt",
    [
        "socket.create_connection(('example.invalid', 80))",
        "socket.socket().connect(('example.invalid', 80))",
        "socket.socket().connect_ex(('example.invalid', 80))",
    ],
)
def test_the_offline_child_blocks_every_socket_seam(
    tmp_path: Path, attempt: str
) -> None:
    # If this child could reach the network, `smoke_offline` would prove nothing:
    # it would pass on any host that happens to have connectivity.
    reacher = tmp_path / "reacher.py"
    reacher.write_text(
        textwrap.dedent(f"""\
            import socket
            {attempt}
        """),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-c", gates._OFFLINE_CHILD, str(reacher)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert "network access attempted" in proc.stderr


def test_the_offline_child_runs_an_innocent_script_and_forwards_its_argv(
    tmp_path: Path,
) -> None:
    # The other half: a script that wants no socket runs normally, sees its own
    # arguments, and its exit status is the child's.
    innocent = tmp_path / "innocent.py"
    innocent.write_text(
        textwrap.dedent("""\
            import sys
            print(" ".join(sys.argv[1:]))
            raise SystemExit(7)
        """),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-c", gates._OFFLINE_CHILD, str(innocent), "--out", "/x"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 7
    assert proc.stdout.strip() == "--out /x"


# --------------------------------------------------------------------------- #
# Every gate can fail
# --------------------------------------------------------------------------- #


def test_the_docs_gate_fails_when_a_required_doc_is_missing(tmp_path: Path) -> None:
    fake_repo = tmp_path / "repo"
    (fake_repo / "docs").mkdir(parents=True)
    (fake_repo / "site").mkdir()
    (fake_repo / "README.md").write_text("no links here\n", encoding="utf-8")

    ok, detail, _ = gates._check_docs(_context(tmp_path, repo_root=fake_repo))
    assert not ok
    assert "docs/METHODOLOGY.md missing" in detail


def test_the_docs_gate_fails_on_a_doc_the_readme_never_links(tmp_path: Path) -> None:
    fake_repo = tmp_path / "repo"
    (fake_repo / "docs").mkdir(parents=True)
    (fake_repo / "site").mkdir()
    for page in gates.REQUIRED_PAGES:
        (fake_repo / "site" / page).write_text("<html></html>", encoding="utf-8")
    body = "x" * (gates.MIN_DOC_BYTES + 1)
    for relative in gates.REQUIRED_DOCS:
        (fake_repo / relative).write_text(body, encoding="utf-8")

    linked = "\n".join(f"[d]({doc})" for doc in list(gates.REQUIRED_DOCS)[:-1])
    (fake_repo / "README.md").write_text(linked, encoding="utf-8")

    ok, detail, _ = gates._check_docs(_context(tmp_path, repo_root=fake_repo))
    assert not ok
    assert "not linked from the README" in detail


def test_the_docs_gate_fails_on_a_stub(tmp_path: Path) -> None:
    fake_repo = tmp_path / "repo"
    (fake_repo / "docs").mkdir(parents=True)
    (fake_repo / "site").mkdir()
    for relative in gates.REQUIRED_DOCS:
        (fake_repo / relative).write_text("# TODO\n", encoding="utf-8")
    (fake_repo / "README.md").write_text(
        "\n".join(gates.REQUIRED_DOCS), encoding="utf-8"
    )

    ok, detail, _ = gates._check_docs(_context(tmp_path, repo_root=fake_repo))
    assert not ok
    assert "is a stub" in detail


def test_the_redaction_gate_fails_on_a_tree_holding_a_credential(
    tmp_path: Path,
) -> None:
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    planted = "AKIA" + "EXAMPLEKEY123456"  # synthetic, and split so this line is clean
    (fake_repo / "config.py").write_text(f'KEY = "{planted}"\n', encoding="utf-8")

    ok, detail, _ = gates._check_redaction(_context(tmp_path, repo_root=fake_repo))
    assert not ok
    assert "credential-like string in the tree" in detail
    assert "config.py:1 (aws_access_key)" in detail
    assert planted not in detail  # the finding is located, never quoted


def test_the_redaction_gate_proves_its_scanner_is_live(tmp_path: Path) -> None:
    # A clean tree passes only because the same scanner, on the same run, found the
    # planted key. Without that probe an inert scanner would pass this too.
    clean_repo = tmp_path / "repo"
    clean_repo.mkdir()
    (clean_repo / "notes.md").write_text("nothing to see\n", encoding="utf-8")

    ctx = _context(tmp_path, repo_root=clean_repo)
    ctx.report_md = tmp_path / "report.md"
    ctx.report_md.write_text("# Regression report\n", encoding="utf-8")

    ok, _, evidence = gates._check_redaction(ctx)
    assert ok
    assert any("planted synthetic key) -> 1 finding" in line for line in evidence)
    assert (ctx.workdir / "redaction_probe" / "planted.txt").is_file()


def test_the_redaction_gate_fails_on_a_report_that_holds_a_credential(
    tmp_path: Path,
) -> None:
    clean_repo = tmp_path / "repo"
    clean_repo.mkdir()

    ctx = _context(tmp_path, repo_root=clean_repo)
    ctx.report_md = tmp_path / "report.md"
    ctx.report_md.write_text(
        "| case | `" + "sk-" + "A" * 20 + "` |\n", encoding="utf-8"
    )

    ok, detail, _ = gates._check_redaction(ctx)
    assert not ok
    assert "openai_key" in detail


def test_the_clean_tree_gate_fails_outside_a_git_repository(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "repo"
    not_a_repo.mkdir()
    ok, detail, _ = gates._check_clean_tree(_context(tmp_path, repo_root=not_a_repo))
    assert not ok
    assert "git status failed" in detail


def test_the_runs_gate_fails_when_a_digest_does_not_match(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    for label in ("baseline", "candidate"):
        run_dir = ctx.workdir / label
        run_dir.mkdir()
        (run_dir / "run.jsonl").write_text("{}\n" * 8, encoding="utf-8")
        (run_dir / "run_meta.json").write_text(
            json.dumps({"records_sha256": "0" * 64}), encoding="utf-8"
        )
        ctx.runs[label] = run_dir

    ok, detail, _ = gates._check_runs_generated(ctx)
    assert not ok
    assert "digest != run_meta.records_sha256" in detail


def test_the_runs_gate_fails_on_a_short_run(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    run_dir = ctx.workdir / "baseline"
    run_dir.mkdir()
    records = run_dir / "run.jsonl"
    records.write_text("{}\n" * 3, encoding="utf-8")  # three cases, not eight
    digest = hashlib.sha256(records.read_bytes()).hexdigest()
    (run_dir / "run_meta.json").write_text(
        json.dumps({"records_sha256": digest}), encoding="utf-8"
    )
    ctx.runs["baseline"] = run_dir
    ctx.runs["candidate"] = run_dir

    ok, detail, _ = gates._check_runs_generated(ctx)
    assert not ok
    assert f"3 records, expected {gates.SMOKE_CASE_COUNT}" in detail


# --------------------------------------------------------------------------- #
# The real thing: the pipeline gates, on this commit
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("gate_id", ["docs", "no_paid_api"])
def test_the_cheap_gates_pass_on_this_commit(tmp_path: Path, gate_id: str) -> None:
    gate = next(g for g in gates.GATES if g.gate_id == gate_id)
    ok, detail, _ = gate.check(_context(tmp_path))
    assert ok, detail


def test_the_whole_pipeline_passes_on_this_commit(tmp_path: Path) -> None:
    # smoke_offline -> runs_generated -> comparison -> report -> redaction, run for
    # real against the committed fixtures, into a scratch directory outside the
    # repository. This is Milestone 13's claim, executed rather than asserted.
    #
    # `tests` and `clean_tree` are excluded: the first would fork this suite into
    # itself, and the second cannot hold while a milestone is in progress.
    selected = gates.select_gates(["redaction", "fixtures"])
    results = gates.run_gates(_context(tmp_path), selected)

    assert [r.gate_id for r in results] == [
        "fixtures",
        "smoke_offline",
        "runs_generated",
        "comparison",
        "report",
        "redaction",
    ]
    failed = [(r.gate_id, r.detail) for r in results if not r.ok]
    assert not failed, failed
