# PLAN — llm-regression-observability

A CPU-first framework for detecting, explaining, and reporting regressions in
LLM applications and agentic systems.

This document is the authoritative build plan. It defines the architecture, the
data contracts (evaluation case, trace, comparison), the regression taxonomy,
the milestone breakdown, and the quality gates. It is written to be executed
milestone by milestone by an autonomous agent (`claude -p`) driven by
`scripts/run_all_milestones_with_claude.sh`, but it also stands alone as a design
document.

---

## 1. Research/engineering question

> How can we detect and explain regressions in LLM applications when outputs are
> stochastic, multi-step, tool-using, and not reducible to exact string
> matching?

Exact-match evaluation fails for LLM apps: two correct answers can differ token
for token, a structurally valid JSON object can be phrased many ways, and an
agent can reach the right answer through a worse process (more retries, more
failed tool calls, higher latency, higher cost). A regression is therefore not
"the string changed" but "a measurable behavioral property got worse."

## 2. Core idea

A model / prompt / tooling / deployment change is evaluated across multiple
independent dimensions, each with its own scorer and its own regression flag:

1. Did final task success change?
2. Did structured-output validity change?
3. Did tool-call behavior change?
4. Did retrieval / evidence quality change?
5. Did latency or cost change?
6. Did refusal / safety behavior change?
7. Did error patterns shift?
8. Can we cluster failures and produce actionable reports?
9. Can we distinguish application regressions from harness / infrastructure noise?

Two runs — a **baseline** (known-good version) and a **candidate** (the change) —
are compared case by case. Per-case deltas are computed, regression flags are
raised against configurable thresholds, failures are clustered, and a
human-readable report is generated.

## 3. Constraints (hard)

- **CPU-only**, target AWS `m5.2xlarge`. No GPU.
- **Offline by default.** No test requires the internet or a paid API.
- **Dependency-free core.** Python 3.12 standard library only (`dataclasses`,
  `json`, `argparse`, `sqlite3`, `difflib`, `hashlib`, `pathlib`, `statistics`).
  `pydantic`/`pandas` are optional and never required for tests. (The build host
  has minimal free disk and no guaranteed network — this constraint is load
  bearing, not stylistic.)
- **Deterministic.** No ambient `time`/`random`/`datetime.now()` in scored paths.
  Same inputs → byte-identical outputs (modulo explicit run id).
- **No secrets, no real user data, no model weights, no large logs** committed.
- Any optional paid-API mode is disabled by default and gated by dry-run mode, a
  token/cost estimator, a hard max-cost guard, a response cache, and explicit
  opt-in.

## 4. Architecture

```
                         ┌─────────────────────────────────────────┐
   evals/suites/*.yaml   │  RUN EXECUTOR (Milestone 4)              │
   evals/fixtures/*  ───▶│  case -> adapter -> RunRecord + spans    │──▶ results/runs/<run_id>/
   adapters (M3/M11)     │  estimate tokens/cost, record latency    │      run.jsonl, spans.jsonl
                         └─────────────────────────────────────────┘
                                          │
                         ┌────────────────▼────────────────────────┐
   evaluators (M5)       │  COMPARISON ENGINE (Milestone 6)         │
   json_schema, exact,   │  pair baseline vs candidate per case     │──▶ results/comparisons/<id>/
   tool_calls, evidence, │  compute deltas -> RegressionFlag(s)     │      comparison.jsonl
   latency, cost,        │  aggregate by suite + category           │      summary.json
   refusal, error        │  cluster failures (M6)                   │
                         └────────────────┬────────────────────────┘
                                          │
                         ┌────────────────▼────────────────────────┐
   reports (M7)          │  REPORT GENERATOR                        │──▶ results/reports/*.md, *.html
   redaction (M9)        │  headline, per-category, worst cases,    │
   observability (M8)    │  latency/cost tables, examples           │
                         └─────────────────────────────────────────┘
```

Data flows one direction: **cases + adapter → RunRecords → ComparisonRecords →
Report**. Every stage writes JSONL/JSON artifacts to `results/` so any stage can
be re-run or inspected independently. Observability spans (request / model call /
tool call / retrieval / evaluator) are emitted alongside run records.

### Package layout

```
src/llm_regression_observability/
  __init__.py            version + metadata
  schemas.py             typed data contracts (dataclasses + validation)
  cli.py                 `llmreg` argparse CLI
  executor.py            run-suite executor: cases + adapter -> RunRecords

  datasets/   loader.py fixtures.py versioning.py
  traces/     model_trace.py tool_trace.py redaction.py storage.py
  evaluators/ exact.py json_schema.py semantic_stub.py tool_calls.py
              latency.py cost.py refusal.py evidence.py
  comparison/ baseline.py candidate.py diff.py regression.py clustering.py
  observability/ metrics.py spans.py aggregation.py
  adapters/   mock_model.py scripted_model.py recorded_model.py api_stub.py
  reports/    markdown.py html.py
```

## 5. Core data contracts

### A. Evaluation case (`EvalCase`)

```
case_id            stable identifier
version            case schema/content version
prompt             synthetic prompt text
input_payload      structured inputs (dict)
expected_properties  properties the output must satisfy (not exact string)
optional_json_schema  JSON schema the output must validate against
optional_allowed_tools  tools the app is permitted to call
optional_retrieval_docs  documents the answer must be grounded in
tags               free-form labels
risk_category      e.g. benign, structured, tool_use, retrieval, refusal, agent
```

An `EvalSuite` is an ordered collection of `EvalCase`s plus suite-level metadata
(id, description, default thresholds).

### B. Model / app trace (`RunRecord`)

```
run_id                 unique per (suite, adapter, app_version) run
case_id
app_version            baseline | candidate | <label>
model_id               identifier of the (mock/recorded) model
prompt_version
tool_version
timestamp              recorded, not ambient (from fixture or run config)
input_tokens_estimate
output_tokens_estimate
latency_ms
cost_estimate_usd
final_output           raw output string
parsed_output          parsed structure (e.g. JSON), or null
tool_calls             list[ToolCallTrace]
retrieval_sources      list[RetrievalTrace]
errors                 list of structured error records
redaction_status       clean | redacted | flagged
```

### C. Comparison result (`ComparisonRecord`)

```
case_id
baseline_run_id
candidate_run_id
changed_fields         list of fields that differ
score_delta            final-answer score change
latency_delta          ms
cost_delta             usd
json_validity_delta
tool_call_delta
refusal_delta
evidence_delta
regression_flags       list[RegressionFlag]
human_readable_summary
```

Supporting types: `ToolCallTrace`, `RetrievalTrace`, `MetricResult`,
`RegressionFlag`, `ReportSummary` (see `schemas.py` / `docs/TRACE_FORMAT.md`).

## 6. Regression taxonomy

At least these categories are implemented (see `docs/REGRESSION_DETECTION.md`):

| Category | Fires when |
| --- | --- |
| `structured_output_regression` | JSON parse/schema validity dropped |
| `tool_call_regression` | wrong/missing/extra tool, arg or order drift |
| `latency_regression` | p50/p95 latency exceeded threshold |
| `cost_regression` | estimated cost exceeded threshold |
| `refusal_behavior_shift` | over/under-refusal vs expected |
| `evidence_grounding_regression` | citations missing/wrong/unsupported |
| `final_answer_regression` | task success score dropped |
| `error_rate_regression` | error/retry rate increased |

Each flag carries: category, severity, the metric(s) that triggered it, the
threshold, and a short explanation. A flag distinguishes **application
regression** from **harness/infra noise** where possible (e.g. an error tagged
`infra` does not raise `error_rate_regression`).

## 7. Fixture suites

Small, committed, synthetic fixtures under `evals/fixtures/` and `data/fixtures/`:

1. **structured_json** — output must be valid JSON matching a schema. Candidate
   introduces invalid JSON, missing required fields, wrong types, extra fields,
   plausible-but-invalid output. Scorers: parse success, schema validity,
   field-level diff.
2. **tool_use** — expected tool-call patterns. Candidate introduces wrong tool,
   missing tool, extra tool, changed args, order change. Scorers: allowed-tool
   usage, argument diff, sequence similarity.
3. **retrieval_evidence** — answer must cite provided docs. Candidate introduces
   uncited claims, wrong source ids, unsupported snippets, missing evidence.
   Scorers: citation presence/correctness, evidence coverage, unsupported-claim
   count.
4. **refusal_boundary** — safe synthetic prompts where behavior should be stable.
   Candidate introduces over-refusal, under-refusal, changed classification.
   Scorers: expected refusal/non-refusal, risk-label diff.
5. **latency_cost** — recorded traces with latency/cost differences. Scorers:
   p50/p95 latency deltas, cost deltas, threshold flags.
6. **agent_trace** — small synthetic multi-step traces. Candidate introduces more
   retries, more failed tool calls, policy violations, longer run time, correct
   final answer but worse process. Scorers: final success, process metrics, tool
   error rate, trace completeness.

## 8. Adapters

- `MockModelAdapter` — fixed outputs for tests.
- `RecordedModelAdapter` — replays committed fixture outputs.
- `ScriptedRegressionAdapter` — deterministically injects known regressions
  (used to prove the detector works).
- `ApiAdapterStub` — interface only, disabled by default.

Optional/later: Claude API adapter with cost guard; Claude Code / code-agent
runtime trace importers (contracts defined in Milestone 10).

## 9. CLI (`llmreg`)

```bash
llmreg check-env
llmreg validate-fixtures evals/fixtures
llmreg run-suite --suite evals/suites/smoke.yaml --adapter recorded \
    --app-version baseline  --out results/runs/
llmreg run-suite --suite evals/suites/smoke.yaml --adapter scripted-regression \
    --app-version candidate --out results/runs/
llmreg compare --baseline results/runs/<baseline> --candidate results/runs/<candidate> \
    --out results/comparisons/
llmreg report --comparison results/comparisons/<id> --out results/reports/regression_report.md
```

## 10. Milestones

Fourteen milestones (0–13). Each is one commit. The runner implements exactly one
per `claude -p` invocation and stops on: agent failure, no new commit, dirty
tree, or `pytest` failure. Per-milestone specs live in `docs/milestones/`.

| # | Title | Core deliverable |
| --- | --- | --- |
| 0 | Scaffold & project contract | pyproject, package skeleton, CLI stub, first tests |
| 1 | Core schemas & trace model | typed data contracts + unit tests |
| 2 | Fixture suites | six synthetic suites + fixture validation |
| 3 | Adapters | mock / recorded / scripted-regression + interface |
| 4 | Run-suite executor | run cases → RunRecords + traces |
| 5 | Evaluators | deterministic scorers with exact expected scores |
| 6 | Baseline vs candidate comparison | deltas, flags, aggregation, clustering |
| 7 | Reports | Markdown + optional HTML reports |
| 8 | Observability layer | spans + aggregate metrics |
| 9 | Redaction & safety hygiene | redaction utilities + tests before reports |
| 10 | Integration contracts | trace import/export formats for sibling repos |
| 11 | API adapter stub & cost guard | dry-run, estimator, max-cost guard, cache |
| 12 | Final portfolio report | README + docs to portfolio quality |
| 13 | Final cleanup | full gate run + hygiene check |

## 11. Quality gates

The repo is **not complete** unless all hold:

- `python3 -m pytest -q` passes.
- Fixtures validate (`llmreg validate-fixtures`).
- The smoke suite runs fully offline.
- Baseline and candidate runs are generated.
- Comparison detects the intentional (scripted) regressions.
- A report is generated from comparison records.
- Redaction works and runs before reports are written.
- Docs explain methodology, metrics, limitations, and reproduction.
- No paid API is required anywhere in the default path.
- Working tree is clean; nothing is pushed.

## 12. Commit discipline

- One coherent commit per milestone; no giant formatting-only commits.
- Never push. Never wait for human review; leave a review placeholder instead.
- Public docs read like professional engineering/research documentation, not a
  learning diary. Anything approximate or synthetic is labeled as such.
