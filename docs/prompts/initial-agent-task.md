# Initial build prompt for Claude Code

You are working in the `llm-regression-observability` repository: a CPU-first
framework for detecting, explaining, and reporting regressions in LLM
applications and agentic systems.

Read `CLAUDE.md` and `docs/PLAN.md`, then execute the plan **milestone by
milestone** using the per-milestone specs in `docs/milestones/`.

## Important instructions

- Do not push. Commit locally after each coherent milestone.
- Do not wait for human review; create a review placeholder and continue.
- Keep the core package dependency-free (Python 3.12 stdlib only). `pydantic` /
  `pandas` / `ruff` are optional and must never be required for tests.
- Keep all default tests CPU-only, offline, and free. No paid API calls.
- Everything deterministic and reproducible (no ambient time/random in scored
  paths).
- Do not commit secrets, credentials, real API responses, private data, large
  logs, caches, `node_modules`, model weights, virtualenvs, or
  `.claude/settings*.json`.
- Update the multi-page website at every milestone.
- Keep documentation professional and precise; avoid "learning project" tone; be
  explicit about limitations and non-goals; label anything approximate or
  synthetic.

Start with Milestone 0 (`docs/milestones/00-scaffold.md`). After each milestone
print: milestone title, commit hash, tests run, website pages updated, review
placeholder path, next milestone.

Continue automatically unless a blocker would risk secrets, paid API spend, data
loss, or repository corruption.

## Preferred driver

Use `./scripts/run_all_milestones_with_claude.sh`, which runs exactly one
milestone per `claude -p` invocation and stops on agent failure, no-new-commit,
dirty tree, or a `pytest` failure.
