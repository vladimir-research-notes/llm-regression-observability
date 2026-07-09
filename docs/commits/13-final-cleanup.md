# Milestone 13 — Final cleanup

**Commit:** `Milestone 13: final cleanup (the plan's §11, as a command that exits 1)`

## What changed

`docs/PLAN.md` §11 lists the ten conditions this repository must meet to be called
complete. All ten were already true when this milestone started. But each was
checked by a different command, in a different output format, by a human reading
the result — so the answer to "is this repository finished?" was not an artifact.
It was a habit.

This milestone made it an artifact.

### New

| File | Contents |
| --- | --- |
| `scripts/10_run_all_gates.py` | eleven gates (the plan's ten, plus optional `lint`), in dependency order; exit 1 on any required failure |
| `tests/test_gates.py` | 43 tests: the registry *is* the plan, every gate can fail, the runner's guarantees hold |
| `docs/review_notes/13-final-cleanup-human-review.md` | review placeholder |
| `docs/commits/13-final-cleanup.md` | this note |

### The load-bearing test

`test_required_gates_reproduce_the_plans_quality_gates` parses §11 out of
`docs/PLAN.md` and asserts the ten required gates reproduce its ten bullets
verbatim, in order. Edit the plan without editing the gates — or the reverse — and
it fails. Deleting `The smoke suite runs fully offline.` from the plan and
rewording it breaks the suite, which is the point: a gate cannot drift away from
the promise it is supposed to keep.

### Three gates rebuilt because their obvious form proves nothing

- **`smoke_offline`** runs the two run scripts in a child interpreter whose
  `socket.connect`, `connect_ex` and `create_connection` raise. Asserting "runs
  offline" is free — the suite never dials, so it passes on a connected host
  either way. Enforcing it is not. Three tests fire a real connection attempt
  through that child and require it to die.
- **`redaction`** plants a synthetic credential in a scratch file and requires the
  scanner to find *that* before it will accept an empty finding list for the
  repository. An inert scanner reports zero findings on every tree, including one
  with a key in it.
- **`report`** requires the rendered Markdown to equal
  `docs/examples/regression_report.md` byte for byte — possible only because the
  report carries no timestamp — and the HTML to request no external asset.

### One gate rewritten because it was checking English

`docs`'s first draft asked whether `docs/REPRODUCIBILITY.md` contains the word
"reproduction". It does not; it says "reproducibility" and "reproduce"; the gate
failed a document that is fine. It now checks that each required document exists,
is not a stub, and is **linked from the README** — a structural property, because
a document no reader can reach explains nothing.

### Two design decisions

The pipeline gates write into a scratch directory **outside** the tree. That is
what lets `clean_tree` inspect the repository rather than the leavings of the gate
that ran before it, and it keeps `redaction`'s planted credential away from the
tree that same gate scans. `--workdir` is refused if it points inside the
repository, and the script only removes a directory it created (proven by a marker
file).

The runner never shells out to `git push` or `git remote`, both denied to the
milestone runner by `.claude/settings.json`. The permission layer sees `python3`,
not what `python3` then runs, so a script that called them would be an end-run
around the user's policy. `clean_tree` reads `git branch -r` instead, which answers
the same question within policy. A test greps the source for both subcommands.

### The runner caught a bug in itself

Its first full run failed the `tests` gate on a suite that passes. `pyproject.toml`
already sets `addopts = "-q"`, so the gate's own `-q` made the invocation `-qq` — and
at `-qq` pytest prints no `N passed` summary line, so the count never parsed. The fix
is `-o addopts=`, which makes the gate run exactly the command §11 names whatever the
config says, plus `-p no:cacheprovider`, so the gate stops writing `.pytest_cache/`
into a tree whose cleanliness it is about to assert. Two tests pin both directions of
the failure, forcing config discovery with `-c pyproject.toml` over a single throwaway
test — without that flag pytest takes `/tmp` as its rootdir and the bug does not
reproduce.

### Corrected

- `scripts/00_check_environment.py` — one real `E501`. `ruff check .` is now clean.
- Eleven stale suite totals (913 → 956) across `README.md`, `docs/RESULTS.md`,
  `docs/SAFETY.md`, `site/index.html`, `site/results.html`, `site/milestones.html`,
  `site/architecture.html`, `site/cost-guard.html`,
  `site/integration-contracts.html` and `site/safety.html` — all found by
  `tests/test_docs.py`, not by reading. That is the Milestone 12 machinery earning
  its keep on the first milestone that followed it.
- `README.md` said "Milestones 0–12 of 13"; there are fourteen (0–13).
- `docs/RESULTS.md` and `site/results.html` gained a quality-gates table;
  `docs/REPRODUCIBILITY.md` gained an "all of it at once" section;
  `site/roadmap.html` no longer describes Milestone 13 as scheduled work.

## Why

A quality gate that lives in a checklist is a quality gate that gets skipped. The
value of this milestone is not that the ten conditions hold — they held before it —
but that a stranger can now confirm they hold in one command, and that the command
is coupled to the document making the promise.

## Validation

```
python3 -m pytest -q                    956 passed, offline
python3 scripts/10_run_all_gates.py     10/10 required gates, PASS (lint clean)
python3 -m ruff check .                 All checks passed
python3 scripts/08_check_repo_hygiene.py  PASS, 0 findings
git status                              clean
```

Nothing pushed. The build is complete.
