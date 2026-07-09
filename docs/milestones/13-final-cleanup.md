# Milestone 13 — Final cleanup

**Status: Complete.** The ten quality gates of `docs/PLAN.md` §11 are now one
command — `python3 scripts/10_run_all_gates.py`, 10/10 required gates green — and
`tests/test_gates.py` (43 tests) parses §11 and asserts the required gates
reproduce its ten bullets verbatim. 956 tests pass; working tree clean; nothing
pushed.

## Goal

Run every quality gate end to end and leave the repository clean.

## Checklist

Run and confirm all pass:

```bash
python3 -m pytest -q                                   # tests
python3 scripts/01_validate_fixtures.py evals/fixtures # fixtures validate
# smoke baseline + candidate runs (recorded / scripted-regression)
llmreg run-suite --suite evals/suites/smoke.yaml --adapter recorded \
    --app-version baseline  --out results/runs/
llmreg run-suite --suite evals/suites/smoke.yaml --adapter scripted-regression \
    --app-version candidate --out results/runs/
llmreg compare --baseline results/runs/<baseline> --candidate results/runs/<candidate> \
    --out results/comparisons/
llmreg report --comparison results/comparisons/<id> \
    --out results/reports/regression_report.md
python3 -m ruff check . || true                        # lint (optional tool)
git status
```

All of it, in dependency order, with the artifacts inspected rather than eyeballed:

```bash
python3 scripts/10_run_all_gates.py          # exit 1 if any required gate fails
python3 scripts/10_run_all_gates.py --json   # deterministic, diffable
```

Verify the repository contains: no secrets, no real credentials, no large logs,
no caches, no paid-API outputs; and that the working tree is clean.

## Quality gates (all must hold)

- tests pass;
- fixtures validate;
- smoke suite runs fully offline;
- baseline and candidate runs are generated;
- comparison detects the intentional regressions;
- a report is generated from comparison records;
- redaction works;
- docs explain methodology, metrics, limitations, and reproduction;
- no paid API is required;
- working tree is clean; **nothing is pushed**.

## What was built

Every gate above was already true, and each was checked by a different command,
in a different output format, by a human reading the result. The deliverable is
the script that runs all ten and the tests that keep it honest.

**`scripts/10_run_all_gates.py`.** Eleven gates — the plan's ten, plus `lint` as
the one optional gate, since `ruff` is a dev extra and a host without it is not a
broken host. `--only` pulls in a gate's prerequisites (`--only report` runs the
whole pipeline that produces one); `--skip` is applied last and wins, so a skipped
prerequisite makes its dependants report *not run* rather than pass.

**`tests/test_gates.py`.** 43 tests in three groups: the registry *is* the plan
(§11's bullets are parsed and compared to the required gates' claim strings, in
order); every gate can fail (each is driven against a deliberately broken tree and
required to notice); and the runner's own guarantees hold.

## Three gates that would have passed while proving nothing

Drafting the runner surfaced three checks that are green in their weak form and
therefore worthless. Each was rebuilt:

- **`smoke_offline` enforces offline rather than asserting it.** The two run
  scripts execute in a child interpreter whose `socket.connect`, `connect_ex` and
  `create_connection` raise. A run that reached for the network fails the gate even
  on a host with connectivity. (Three tests fire a real connection attempt through
  that child and require it to die.)
- **`redaction` proves its own scanner is live.** An inert scanner reports zero
  findings on every tree, including one with a key in it. The gate plants a
  synthetic credential in a scratch file and requires the scanner to find *that*
  before it will accept an empty finding list for the repository.
- **`report` compares bytes.** The rendered Markdown must equal the committed
  `docs/examples/regression_report.md` exactly — possible only because the report
  carries no timestamp.

Two supporting decisions: the pipeline gates write into a scratch directory
**outside** the tree (`--workdir` is refused inside it, and the script only removes
a directory it created), which is what lets `clean_tree` inspect the repository
rather than the leavings of the gate before it; and `--json` carries no clock and
no scratch path, so two runs on one commit are byte-identical.

Two things the runner deliberately does not do. It never shells out to `git push`
or `git remote` — both denied to the milestone runner by `.claude/settings.json` —
so `clean_tree` reads remote-tracking branches with `git branch -r` instead. And
the `tests` gate raises `GateRecursionError` when selected from inside `pytest`,
rather than forking the suite into itself.

## Corrections made

- The `docs` gate's first draft asked whether `REPRODUCIBILITY.md` contains the
  word "reproduction". It does not, and it should not have to. A gate cannot read
  English; it now checks that each required document exists, is not a stub, and is
  **linked from the README**. Whether the prose is any good is what
  `docs/review_notes/` is for.
- One real lint finding — `E501` in `scripts/00_check_environment.py` — was fixed.
  `python3 -m ruff check .` is now clean.
- **The runner caught a bug in itself on its first full run.** Its `tests` gate
  shelled out to `python3 -m pytest -q`, but this project's `addopts` is already
  `-q`, so the effective invocation was `-qq` — at which pytest prints no
  `N passed` summary line, and the gate scored a passing suite as an unparseable
  one. `-o addopts=` makes the gate run exactly the command §11 names whatever
  the config happens to say, and `-p no:cacheprovider` stops it from writing
  `.pytest_cache/` into a tree whose cleanliness it is about to assert. Two tests
  pin both directions: one requires the gate's flags to yield a parseable summary,
  the other requires the naive flags not to.
- Adding 43 tests moved the suite total from 913 to 956, which `test_docs.py`
  caught in eleven places across the README, `docs/RESULTS.md`, `docs/SAFETY.md`
  and six website pages. That is the Milestone 12 machinery doing its job.

## Definition of done

All gates green; hygiene check clean; final commit made locally. The build is
complete.
