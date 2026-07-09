# llm-regression-observability

**A CPU-first framework for detecting, explaining, and reporting regressions in
LLM applications and agentic systems.**

Model / prompt / tool / deployment versions go in; evaluation traces and
observability metrics come out. The system answers a single, hard question:

> When outputs are stochastic, multi-step, tool-using, and not reducible to
> exact string matching, **did a change make behavior worse — and how?**

> **Status:** project contract + build harness in place. Implementation is built
> milestone by milestone (0–13). See [`docs/PLAN.md`](docs/PLAN.md) and the live
> status on the [website](site/index.html).

This is a reliability/evaluation framework. It is **not** a medical, legal, or
security authority, and it makes **no** claim of universal model evaluation. All
fixtures are synthetic. See [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) and
[`docs/SAFETY.md`](docs/SAFETY.md).

---

## Why exact match is not enough

Two correct answers can differ token for token. A valid JSON object can be
phrased many ways. An agent can reach the right answer through a *worse* process
— more retries, more failed tool calls, higher latency, higher cost. So a
regression is not "the string changed"; it is "a measurable behavioral property
got worse." This framework measures those properties across independent
dimensions and flags the ones that regressed.

## What it measures

| Dimension | Regression flag |
| --- | --- |
| Final task success | `final_answer_regression` |
| Structured-output validity | `structured_output_regression` |
| Tool-call behavior | `tool_call_regression` |
| Retrieval / evidence grounding | `evidence_grounding_regression` |
| Latency | `latency_regression` |
| Cost | `cost_regression` |
| Refusal / safety behavior | `refusal_behavior_shift` |
| Error / retry patterns | `error_rate_regression` |

## How it works

```
cases + adapter ──▶ RunRecords (traces) ──▶ ComparisonRecords ──▶ Report
   (baseline)                                    (deltas +
   (candidate)                                    regression flags)
```

A **baseline** run (known-good version) and a **candidate** run (the change) are
executed over the same synthetic suite, compared case by case, scored on every
dimension, flagged against configurable thresholds, clustered, and turned into a
Markdown/HTML report. Deterministic mock / recorded / scripted-regression
adapters make the whole pipeline runnable with **no model call and no network**.

## Quickstart

```bash
python3 scripts/00_check_environment.py     # preflight (Python, tooling, disk)
python3 -m pytest -q                         # CPU-only, offline test suite
./scripts/serve_site.sh                      # docs website on http://localhost:9001
```

The end-to-end flow. Every step runs today (Milestones 2, 4, 5, 6 and 7):

```bash
llmreg validate-fixtures evals/fixtures
llmreg run-suite --suite evals/suites/smoke.yaml --adapter recorded \
    --app-version baseline  --out results/runs/
llmreg run-suite --suite evals/suites/smoke.yaml --adapter scripted-regression \
    --app-version candidate --out results/runs/

llmreg compare --baseline results/runs/<baseline> --candidate results/runs/<candidate> \
    --out results/comparisons/                       # add --fail-on critical for CI
llmreg report --comparison results/comparisons/<id> \
    --out results/reports/regression_report.md --format both
```

Without an editable install, the same steps are available as numbered wrappers:
`02_run_baseline.py`, `03_run_candidate.py`, `04_score_runs.py`,
`05_compare_runs.py`, `06_generate_report.py` (all accept `--dry-run`). Every
artifact they write is byte-identical across runs. A committed sample report lives
at [`docs/examples/regression_report.md`](docs/examples/regression_report.md), kept
honest by a test that regenerates it from the fixtures.

## Design constraints

- **CPU-only**, target AWS `m5.2xlarge`. No GPU.
- **Offline and free by default.** No test requires the internet or a paid API.
- **Dependency-free core** — Python 3.12 standard library only. `pydantic` /
  `pandas` are optional and never required for tests.
- **Deterministic** — same inputs produce byte-identical outputs.
- Any optional paid-API adapter is disabled by default and gated by dry-run
  mode, a token/cost estimator, a hard max-cost guard, a response cache, and
  explicit opt-in.

## Repository map

```
src/llm_regression_observability/  core package (schemas, evaluators, comparison, ...)
evals/fixtures/                    six synthetic fixture suites
evals/suites/                      suite manifests (e.g. smoke.yaml)
data/fixtures/                     prompts, outputs, tool traces, retrieval docs
results/                           generated runs / comparisons / reports (gitignored)
docs/                              methodology, trace format, metrics, safety, plan
docs/milestones/                   per-milestone specifications
scripts/                           env check, site server, milestone runner
site/                              multi-page static documentation website
tests/                             CPU-only offline tests
```

## Documentation

- [`docs/PLAN.md`](docs/PLAN.md) — architecture, contracts, milestones, gates
- [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) — why multi-dimensional evaluation
- [`docs/TRACE_FORMAT.md`](docs/TRACE_FORMAT.md) — run & comparison schemas
- [`docs/METRICS.md`](docs/METRICS.md) — metric definitions
- [`docs/REGRESSION_DETECTION.md`](docs/REGRESSION_DETECTION.md) — thresholds, false positives
- [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md) — traces, spans, metrics
- [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) · [`docs/SAFETY.md`](docs/SAFETY.md) · [`docs/REPRODUCIBILITY.md`](docs/REPRODUCIBILITY.md) · [`docs/PAPER_PATH.md`](docs/PAPER_PATH.md)

## Project context

Built as an independent research/engineering artifact and as a foundation that
can later import traces from a code-agent runtime, BioAgentBench, BioScientist
OS, and agent-control-bench (integration contracts defined in Milestone 10; those
repositories are **not** required to build or test this one).

## License

MIT — see [`LICENSE`](LICENSE).
