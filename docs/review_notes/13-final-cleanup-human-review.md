Human review: Milestone 13 — Final cleanup

TL;DR

`docs/PLAN.md` §11 lists the ten conditions this repository must meet to be called
complete. All ten were already true. Each was checked by a different command, in a
different output format, by a human reading the result — which means the answer to
"is this repository finished?" lived in someone's head. It is now
`python3 scripts/10_run_all_gates.py`, which runs the ten in dependency order and
exits non-zero if any required one fails. 10/10 green.

The deliverable that matters is `tests/test_gates.py` (43 tests). Its load-bearing
test parses §11 out of the plan and asserts the ten required gates reproduce its
ten bullets **verbatim, in order**. Edit the plan without editing the gates, or the
reverse, and it fails. A gate cannot drift away from the promise it keeps.

Drafting the runner turned up three gates that would have been green while proving
nothing, and one that was checking English. All four were rebuilt. 956 tests pass,
all offline; working tree clean; nothing pushed.

What changed

- **New `scripts/10_run_all_gates.py`.** Eleven gates: the plan's ten, plus `lint`
  as the sole optional one (`ruff` is a dev extra; a host without it is not a broken
  host). `--only` pulls in a gate's prerequisites, so `--only report` runs the whole
  pipeline that produces one. `--skip` is applied last and wins: a skipped
  prerequisite makes its dependants report *not run*, never *pass*. `--json` is
  deterministic.
- **New `tests/test_gates.py`** — 43 tests, described below.
- **`scripts/00_check_environment.py`** — one real `E501` fixed;
  `python3 -m ruff check .` is now clean.
- **Docs and website.** The suite total moved 913 → 956, which `test_docs.py` caught
  in eleven places (README, `docs/RESULTS.md`, `docs/SAFETY.md`, and six site
  pages). `docs/RESULTS.md` and `site/results.html` gained a quality-gates table;
  `docs/REPRODUCIBILITY.md` gained an "all of it at once" section; `index.html`,
  `milestones.html` and `roadmap.html` now say the build is finished rather than
  naming Milestone 13 as next.

The three gates that would have proved nothing

Each of these is green in its weak form, which is the whole problem:

1. **"The smoke suite runs fully offline."** Asserting this proves nothing — the
   suite runs offline on a machine with no network *and* on one with a network,
   because it simply never dials. `smoke_offline` now executes the two run scripts
   in a child interpreter whose `socket.connect`, `connect_ex` and
   `create_connection` raise. A run that reached for the network fails the gate even
   on a connected host. Three tests fire a real connection attempt through that
   child and require it to die; a fourth runs an innocent script through it and
   requires the exit status and `argv` to survive.
2. **"Redaction works."** A scanner that matches nothing reports zero findings on
   every tree, including one with a key in it — and passes. `redaction` plants a
   synthetic credential in a scratch file and requires the scanner to find *that*
   before it accepts an empty finding list for the repository. Its test asserts the
   evidence line says `-> 1 finding`, so the probe cannot be silently dropped.
3. **"A report is generated from comparison records."** A file existing at a path is
   not a report. `report` requires the rendered Markdown to equal the committed
   `docs/examples/regression_report.md` **byte for byte** — possible only because
   the report carries no generation timestamp — and requires the HTML to contain no
   `http://`, `https://`, `<script>`, `<link>`, `<img>` or `@import`.

And one bug the runner found in itself

Its first full run failed the `tests` gate on a suite that passes. `pyproject.toml`
sets `addopts = "-q"`; the gate added another `-q`; at `-qq` pytest prints no
`N passed` summary line at all, so `_first_int(r"(\d+) passed", ...)` returned `None`
and a green suite scored as a broken one. `-o addopts=` fixes it and makes the gate
run exactly the command §11 names regardless of config; `-p no:cacheprovider` was
added alongside, because a gate that asserts the tree is clean should not write
`.pytest_cache/` into it on the way.

Both directions are pinned. The tests force config discovery with `-c pyproject.toml`
over a single throwaway test file: without that flag pytest takes `/tmp` as its
rootdir, never loads this project's config, and the bug does not reproduce — the
first version of that test passed for exactly that wrong reason.

This is the strongest evidence the milestone produced. The gate runner's first act
was to fail on itself.

And one gate that was checking English

`docs`'s first draft asserted that `docs/REPRODUCIBILITY.md` contains the word
"reproduction". It does not — it says "reproducibility" and "reproduce" — and the
gate failed on a document that is, in fact, fine. A gate cannot read English. It now
checks that each required document exists, is not a stub (≥ 500 bytes), and is
**linked from the README**, which is a structural property worth having: a document
no reader can reach explains nothing. Whether the prose is good is what these review
notes are for.

Two design decisions worth a look

- **The gate run writes nothing into the repository.** The pipeline gates run into a
  scratch directory *outside* the tree; `--workdir` is refused if it points inside;
  and the script only ever removes a directory it created (a marker file proves it).
  This is what lets `clean_tree` inspect the repository rather than the leavings of
  the gate that ran before it. It also keeps the `redaction` gate's planted
  credential far away from the tree that same gate scans.
- **The runner never invokes `git push` or `git remote`.** Both are on the milestone
  runner's deny list in `.claude/settings.json`, and a script that shelled out to
  them would be an end-run around the user's policy — the permission layer sees
  `python3`, not what `python3` then runs. `clean_tree` reads remote-tracking
  branches with `git branch -r` instead, which answers the same question ("has
  anything left this machine?") within policy. A test greps the runner's source for
  those two subcommands.

What should be checked later

- **`clean_tree` treats any remote-tracking branch as a failure.** That is correct
  for *this* repository, whose contract is "never push", but it makes the gate
  runner unusable as-is in a normal repo with an `origin`. If the script is ever
  lifted out, that gate needs to become "HEAD is not an ancestor of any remote ref",
  which requires `git merge-base --is-ancestor` and therefore a policy change.
- **`EXPECTED_REGRESSIONS` is a literal table in the script,** deliberately not
  derived from the fixtures' `expected.json`. Deriving it would make the gate
  self-ratifying: a fixture edit that changed what is detected would be blessed
  rather than caught. The cost is a second place to edit when the smoke suite
  changes, and the failure message names the case. Reviewer may disagree.
- **The `tests` gate refuses to run under `pytest`** (`GateRecursionError`) by
  checking `PYTEST_CURRENT_TEST` in the environment. That is an ambient read, which
  the determinism rule forbids in scored paths — this is not a scored path, and the
  alternative (a fork bomb) is worse. Called out because it is the only ambient read
  in the repository outside `conftest.py`.
- **The pipeline gates are re-run inside the test suite** (`test_gates.py` runs
  `fixtures → smoke_offline → runs_generated → comparison → report → redaction` for
  real, into `tmp_path`). It costs about 2 s of the suite's 9 s. It is also the only
  place the milestone's central claim is *executed* rather than described, so it
  earns the time. If the suite gets slower, that is the first test to mark.
- **`--json` determinism is asserted for a passing repository.** Scratch paths are
  rewritten to `<workdir>` unconditionally, but a failing gate's `detail` can carry
  a subprocess traceback, and a traceback carries absolute paths from outside the
  workdir (e.g. site-packages). Two failing runs on two machines would not diff
  cleanly. Not worth fixing until someone wants to diff failures.
- **The `redaction` gate scans the working tree, not the index.** So it sees
  untracked, gitignored files (`results/`, `logs/`) too. That is strictly more
  cautious, matches `scripts/08_check_repo_hygiene.py`, and is why it is run before
  the probe is planted — but it means a gate run in a dirty workspace can fail on a
  file that would never be committed.

Technical risks

- **Low. No library code changed.** `src/llm_regression_observability/` is untouched
  this milestone; the only source edit outside `scripts/` and `tests/` is a line
  break in the environment preflight. The core package remains stdlib-only and
  dependency-free, no scored path was touched, the smoke baseline digest is still
  `ee23bfde…`, and the committed example report still regenerates byte-identically.
- **`scripts/10_run_all_gates.py` is not importable as a module** (its name starts
  with a digit). `tests/test_gates.py` loads it by path via `importlib`, and must
  register it in `sys.modules` *before* `exec_module`, because `@dataclass` resolves
  `cls.__module__` through it. That is a sharp edge for anyone editing the loader.
- **The gate runner spawns subprocesses that spawn subprocesses.** `10_run_all_gates`
  → `python3 -c <preamble>` → `runpy.run_path(scripts/02_run_baseline.py)`. Failures
  surface as the last four lines of the child's combined output, which is enough to
  diagnose but not to debug. `--keep-workdir` exists for that.
- **Nothing here proves the repository has no secrets** — only that no *certain-tier
  credential pattern* appears in a scannable text file. A base64 blob in a `.png`, or
  a key shaped like nothing in the table, passes. See `docs/SAFETY.md`.

Commands to verify

```bash
python3 scripts/10_run_all_gates.py                    # 10/10 required gates, PASS
python3 scripts/10_run_all_gates.py --json             # deterministic; diff two runs
python3 scripts/10_run_all_gates.py --only report --keep-workdir

python3 -m pytest -q                                   # 956 passed, offline
python3 -m pytest tests/test_gates.py -q               # the 43 gate tests alone
python3 -m ruff check .                                # clean (optional tool)
python3 scripts/08_check_repo_hygiene.py               # PASS, 0 findings
git status                                             # clean

# the gate runner is the plan: break the coupling and watch it fail
sed -i 's/^- The smoke suite runs fully offline\./- The smoke suite runs offline./' docs/PLAN.md
python3 -m pytest tests/test_gates.py -q               # fails; then git checkout docs/PLAN.md

# a gate that cannot fail is not a gate: prove `clean_tree` and `redaction` bite
touch UNTRACKED.md && python3 scripts/10_run_all_gates.py --only clean_tree  # exit 1
rm UNTRACKED.md
```

Human feedback
<!-- Human will fill this later. -->
