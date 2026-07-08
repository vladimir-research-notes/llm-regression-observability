# PLAN

The authoritative build plan lives in **[`docs/PLAN.md`](docs/PLAN.md)**:
architecture, data contracts, regression taxonomy, milestone breakdown, and
quality gates.

Per-milestone specifications live in **[`docs/milestones/`](docs/milestones/)**
(one file per milestone, `00`–`13`). Each is the reference the milestone runner
implements against.

## Milestones at a glance

| # | Title |
| --- | --- |
| 0 | Scaffold & project contract |
| 1 | Core schemas & trace model |
| 2 | Fixture suites |
| 3 | Mock / recorded / scripted-regression adapters |
| 4 | Run-suite executor |
| 5 | Evaluators |
| 6 | Baseline vs candidate comparison |
| 7 | Reports (Markdown + optional HTML) |
| 8 | Observability layer (spans + metrics) |
| 9 | Redaction & safety hygiene |
| 10 | Integration contracts |
| 11 | Optional API adapter stub & cost guard |
| 12 | Final portfolio report |
| 13 | Final cleanup |

## How milestones are executed

```bash
# implement one milestone at a time via claude -p (see docs/prompts/)
./scripts/run_all_milestones_with_claude.sh          # milestones 0..13
./scripts/run_all_milestones_with_claude.sh 4 6      # a subrange
```

The runner stops on any of: agent failure, no new commit, dirty working tree, or
a `pytest` failure — so a broken milestone halts the chain for inspection.
