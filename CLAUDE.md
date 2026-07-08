# Claude Code instructions for this repository

This repository is a research-grade engineering project: a CPU-first framework
for **detecting, explaining, and reporting regressions in LLM applications and
agentic systems**.

## Public framing

`llm-regression-observability` takes model / prompt / tool / deployment versions
in, and produces evaluation traces, observability metrics, and regression
reports out. It answers one question:

> How do we detect and explain regressions in LLM applications when outputs are
> stochastic, multi-step, tool-using, and not reducible to exact string
> matching?

This is **not** a toy dashboard or a learning demo. It is a small but real
production/research system. It is a reliability/evaluation framework — **not a
medical, legal, or security authority**.

## Operating principles

- Build milestone by milestone (see `docs/PLAN.md` and `docs/milestones/`).
- Implement **exactly one milestone per run** when driven by the milestone runner.
- Commit locally after each coherent milestone. **Never push.**
- Do not wait for human review. Create a human-review placeholder per milestone
  and continue automatically.
- Keep default tests **CPU-only, offline, and free**. No test may require the
  internet or a paid API.
- The **core package is dependency-free** (Python 3.12 standard library only:
  `dataclasses`, `json`, `argparse`, `sqlite3`, `difflib`, `hashlib`, `pathlib`).
  This is a hard constraint: the build host has minimal free disk and no
  guaranteed network, so heavy dependencies (`pydantic`, `pandas`) must remain
  **optional** and must never be required to run the suite or the tests.
- Prefer deterministic mock / recorded / scripted adapters first. Any real API
  adapter is interface-only and disabled by default (Milestone 11).
- Any paid-API mode must have: dry-run mode, token/cost estimator, hard
  max-cost guard, response cache, and explicit opt-in.
- Do not commit secrets, API keys, real API responses, private data,
  large logs, caches, model weights, virtualenvs, `node_modules`, or
  `.claude/settings*.json`.
- Use only **synthetic** prompts, outputs, and small safe fixtures. No real user
  data. Keep any biomedical/security-adjacent examples non-clinical and
  non-exploitative.
- Avoid marketing language and "learning journal" tone. Documentation must be
  precise, technical, and honest about what is synthetic or approximate.

## Determinism rule

Everything that ships must be **deterministic and reproducible**. Do not call
`time.time()`, `random.random()`, or `datetime.now()` inside library code paths
that feed traces or scores. Timestamps and seeds are inputs (recorded in
fixtures or passed explicitly), never ambient. Two runs of the same suite on the
same fixtures must produce byte-identical results (modulo an explicit run id).

## Quality bar

Every major claim must be backed by at least one of: a test, a reproducible
example, a trace artifact, a result table, a documented limitation, or an
explicit non-goal.

The repo should be understandable to a strong senior engineer in 5 minutes and
survive deeper technical questioning after 30 minutes.

## Required behavior after each milestone

1. Run that milestone's validation commands (at minimum `python3 -m pytest -q`).
2. Update the multi-page website under `site/` (status + relevant page).
3. Update `docs/RESULTS.md` or the relevant milestone/topic docs if results
   changed.
4. Create/update a review placeholder under `docs/review_notes/`.
5. Create/update the milestone note under `docs/milestones/`.
6. Create/update a commit note under `docs/commits/`.
7. Commit locally (no push).
8. Print: milestone title, commit hash, tests run, website pages updated, review
   placeholder path, and the next milestone.

Do not stop for review unless continuing would risk secrets, paid API spend,
data loss, or repository corruption.

## Human review files

For every milestone create `docs/review_notes/XX-<name>-human-review.md` with:

```
Human review: <milestone title>
TL;DR
What changed
What should be checked later
Technical risks
Commands to verify
Human feedback
<!-- Human will fill this later. -->
```

Do not claim human review happened.

## Website requirements

Maintain a multi-page static site under `site/` (plain HTML + one `style.css`,
no build step, no external assets). Required pages:

- `index.html` (Overview)
- `architecture.html`
- `trace-format.html`
- `metrics.html`
- `regression-categories.html`
- `example-comparison.html`
- `results.html`
- `limitations.html`
- `safety.html`
- `roadmap.html`
- `milestones.html` (per-milestone explanations)

Each topic page: brief explanation, deeper technical explanation, current
implementation status, relevant files, current test/result status, open
questions/limitations. Update the site at every milestone.

## Repository layout (target)

See `docs/PLAN.md` for the authoritative structure and milestone breakdown. Key
package path: `src/llm_regression_observability/`. Console entry point: `llmreg`.

## Local commands

```bash
python3 scripts/00_check_environment.py     # environment preflight
python3 -m pytest -q                         # CPU-only test suite
./scripts/serve_site.sh                      # serve site/ on port 9001
./scripts/run_all_milestones_with_claude.sh  # drive milestones via claude -p
```
