# llm-regression-observability

**A CPU-first framework for detecting, explaining, and reporting regressions in
LLM applications and agentic systems.**

Model / prompt / tool / deployment versions go in; evaluation traces and
observability metrics come out. The system answers a single, hard question:

> When outputs are stochastic, multi-step, tool-using, and not reducible to
> exact string matching, **did a change make behavior worse — and how?**

> **Status:** the pipeline runs end to end, offline and CPU-only. On six
> synthetic fixture suites the comparison engine detects **18 of 18 injected
> defects** with the category and severity their ground truth annotates, and
> raises **0 false positives** on the 8 negative controls. **956 tests**, none of
> which touch the network. All fourteen milestones (0–13) are complete, and the ten
> quality gates in [`docs/PLAN.md`](docs/PLAN.md) §11 run as one command
> (`python3 scripts/10_run_all_gates.py`); see the [website](site/index.html).

This is a reliability/evaluation framework. It is **not** a medical, legal, or
security authority, and it makes **no** claim of universal model evaluation. All
fixtures are synthetic. See [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) and
[`docs/SAFETY.md`](docs/SAFETY.md).

---

## Why exact match is not enough

Two correct answers can differ token for token. A valid JSON object can be
phrased many ways. An agent can reach the right answer through a *worse* process
— more retries, more failed tool calls, higher latency, higher cost. A test that
flags every string change is uselessly noisy; a test that watches only the final
string is uselessly blind.

So a regression is reframed: not "the string changed" but **"a measurable
behavioral property got worse."** This framework measures those properties across
independent dimensions and flags the ones that regressed.

## What it measures

| Dimension | Regression flag | Fires when |
| --- | --- | --- |
| Final task success | `final_answer_regression` | an answer-level declared property stopped holding |
| Structured-output validity | `structured_output_regression` | output stopped parsing, or stopped validating against its schema |
| Tool-call behavior | `tool_call_regression` | a disallowed, missing or extra tool; argument or order drift |
| Retrieval / evidence grounding | `evidence_grounding_regression` | a claim lost its support, or a new unsupported claim appeared |
| Latency | `latency_regression` | latency crossed **both** a relative and an absolute floor |
| Cost | `cost_regression` | estimated cost crossed both floors |
| Refusal / safety behavior | `refusal_behavior_shift` | over-refusal, under-refusal, or risk-label drift |
| Error / retry patterns | `error_rate_regression` | an **app-origin** error or retry rate rose |

Each flag carries the metric that triggered it, the observed value, the threshold
it crossed, an `app`/`infra` attribution, and a rule-authored explanation. An
error tagged `infra` never raises a flag — a degraded harness is not a model
regression. Definitions are in [`docs/METRICS.md`](docs/METRICS.md); the rules and
their thresholds in
[`docs/REGRESSION_DETECTION.md`](docs/REGRESSION_DETECTION.md).

## Architecture

Data flows one direction. Every stage writes JSONL/JSON under `results/`, so any
stage can be re-run or inspected on its own.

```
evals/suites/*.yaml    ┌────────────────────────────────────────────┐
evals/fixtures/*   ───▶│ RUN EXECUTOR        llmreg run-suite        │──▶ results/runs/<run_id>/
adapters               │ case → adapter → RunRecord                  │      run.jsonl, run_meta.json
                       └────────────────────────────────────────────┘
                                        │
evaluators             ┌────────────────▼───────────────────────────┐
exact, json_schema,    │ EVALUATORS          (scored per run)        │──▶ results/processed/<run_id>/
tool_calls, evidence,  │ (case, record) → MetricResult               │      metrics.jsonl
refusal, latency,      ├────────────────────────────────────────────┤
cost, error            │ OBSERVABILITY       llmreg aggregate        │──▶ spans.jsonl, aggregate.json
                       │ run → span tree → suite metrics             │
                       └────────────────┬───────────────────────────┘
                                        │
                       ┌────────────────▼───────────────────────────┐
                       │ COMPARISON ENGINE   llmreg compare          │──▶ results/comparisons/<id>/
                       │ pair by case_id → deltas → RegressionFlags  │      comparison.jsonl
                       │ → cluster → summarize                       │      summary.json
                       └────────────────┬───────────────────────────┘      comparison_meta.json
                                        │
                       ┌────────────────▼───────────────────────────┐
redaction ────────────▶│ REPORT GENERATOR    llmreg report           │──▶ results/reports/*.md, *.html
                       │ verdict, categories, clusters, silences     │
                       └────────────────────────────────────────────┘
```

Deterministic mock / recorded / scripted-regression adapters make the whole
pipeline runnable with **no model call and no network**. Traces produced *outside*
this repository enter through versioned import contracts and are scored, compared
and reported by the unchanged pipeline
([`docs/INTEGRATION_CONTRACTS.md`](docs/INTEGRATION_CONTRACTS.md)).

## A real trace, and what the engine sees in it

Case `tu_wrong_tool`, read off `results/runs/*/run.jsonl`. Two `RunRecord`s,
abridged to the fields that carry the argument:

```jsonc
// baseline                                    // candidate
{ "case_id": "tu_wrong_tool",                  { "case_id": "tu_wrong_tool",
  "final_output": "Synthetic order summary       "final_output": "Synthetic order summary
     assembled from tool results.",                 assembled from tool results.",
  "latency_ms": 200.0,                           "latency_ms": 200.0,
  "cost_estimate_usd": 0.002,                    "cost_estimate_usd": 0.002,
  "tool_calls": [                                "tool_calls": [
    {"name": "search",    "order_index": 0,        {"name": "search", "order_index": 0,
     "arguments": {"query": "synthetic-001"},       "arguments": {"query": "synthetic-001"},
     "status": "ok"},                               "status": "ok"}
    {"name": "summarize", "order_index": 1,      ],
     "arguments": {}, "status": "ok"}             "errors": [],
  ],                                             "redaction_status": "clean" }
  "errors": [],
  "redaction_status": "clean" }
```

**The final output is byte-identical.** A string-diff test sees nothing at all.
The agent simply stopped calling `summarize` and answered from `search` alone.
The `ComparisonRecord` the engine writes:

```json
{
  "case_id": "tu_wrong_tool",
  "changed_fields": ["tool_calls"],
  "score_delta": -1.0,
  "tool_call_delta": -0.333333,
  "latency_delta": 0.0,
  "regression_flags": [
    {
      "category": "tool_call_regression",
      "severity": "critical",
      "metric": "missing_tools",
      "observed": 1.0,
      "threshold": 0.0,
      "attribution": "app",
      "explanation": "expected tool call(s) disappeared: summarize; also: tool-call sequence similarity 1 -> 0.666667, below the 0.8 floor"
    }
  ]
}
```

The mirror image also holds. `sj_control_reorder`'s candidate emits the same JSON
object with its keys reordered: `changed_fields = ["final_output"]`,
`regression_flags = []`. The string changed and nothing regressed. Those two cases
are the whole thesis, in two directions.

The trace and comparison schemas are specified in
[`docs/TRACE_FORMAT.md`](docs/TRACE_FORMAT.md).

## Result: the smoke suite, end to end

`llmreg compare` over the eight-case smoke suite — the cross-section where the
scripted adapter's injected defect and the fixture's recorded one agree, so
category **and** severity are both asserted:

| case | flag | severity |
| --- | --- | --- |
| `sj_invalid_json` | `structured_output_regression` | critical |
| `tu_wrong_tool` | `tool_call_regression` | critical |
| `re_unsupported_claim` | `evidence_grounding_regression` | critical |
| `rb_underrefuse` | `refusal_behavior_shift` | critical |
| `lc_latency_spike` | `latency_regression` | warn |
| `at_more_retries` | `error_rate_regression` | warn |
| `at_infra_noise` | — | infra-attributed: surfaced, not flagged |
| `sj_control_reorder` | — | negative control |

```
verdict          : REGRESSED — 6 regression flag(s) across 6 of 8 paired cases (4 critical, 2 warn)
latency p50/p95  : 190.0 -> 210.0 ms / 300.0 -> 365.0 ms (delta +20.0 / +65.0)
cost total       : $0.018700 -> $0.018700 (estimate, not a bill)
infra-attributed : 1 case(s) surfaced, excluded from flags
clusters         : 6
```

`llmreg compare --fail-on critical` exits 1 here — that is the CI regression gate.
The default `--fail-on none` exits 0 and reports, because a comparison is an
observation and only the caller decides what is unacceptable.

Across all six fixture suites, against the per-case `expected.json` oracle:

| | recorded candidate | scripted candidate |
| --- | --- | --- |
| injected defects | 18 | 18 |
| detected with the annotated category | **18** | **18** |
| …and the annotated severity | **18** | 14 |
| negative controls | 8 | 8 |
| false positives on controls | **0** | **0** |

The four severity divergences belong to the scripted adapter, which injects one
*representative* defect per category — dropping a tool is worse than reordering
one — not to the detector. Category recall is 18/18 on both paths, and the
divergence is pinned in a test table rather than described. Full numbers,
per-dimension tables and the cluster listing are in
[`docs/RESULTS.md`](docs/RESULTS.md).

## Reproduce it

```bash
python3 scripts/00_check_environment.py     # preflight (Python, tooling, disk)
python3 -m pytest -q                         # 956 tests, CPU-only and offline
python3 scripts/10_run_all_gates.py          # all ten quality gates; exit 1 on any
./scripts/serve_site.sh                      # docs website on http://localhost:9001
```

The pipeline, with an editable install:

```bash
llmreg validate-fixtures evals/fixtures
llmreg run-suite --suite evals/suites/smoke.yaml --adapter recorded \
    --app-version baseline  --out results/runs/
llmreg run-suite --suite evals/suites/smoke.yaml --adapter scripted-regression \
    --app-version candidate --out results/runs/

llmreg aggregate --run results/runs/smoke-baseline-recorded     # spans + suite metrics
llmreg compare --baseline results/runs/smoke-baseline-recorded \
    --candidate results/runs/smoke-candidate-scripted-regression \
    --out results/comparisons/                                   # --fail-on critical for CI
llmreg report --comparison results/comparisons/smoke-baseline-vs-candidate \
    --out results/reports/regression_report.md --format both
```

Without an editable install, the same steps are numbered wrappers that prepend
`src/` to `sys.path`: `02_run_baseline.py`, `03_run_candidate.py`,
`04_score_runs.py`, `07_aggregate_runs.py`, `05_compare_runs.py`,
`06_generate_report.py` (all accept `--dry-run`).

Every artifact is byte-reproducible. `sha256(run.jsonl)` for the smoke baseline is
`ee23bfde…` on any machine, and the report carries no generation timestamp: it is
a pure function of the comparison directory. A committed sample report lives at
[`docs/examples/regression_report.md`](docs/examples/regression_report.md), kept
honest by a test that regenerates it from the fixtures and compares byte for byte.
See [`docs/REPRODUCIBILITY.md`](docs/REPRODUCIBILITY.md).

## Design constraints

- **CPU-only**, target AWS `m5.2xlarge`. No GPU.
- **Offline and free by default.** No test requires the internet or a paid API.
- **Dependency-free core** — Python 3.12 standard library only. `pydantic` /
  `pandas` are optional and never required for tests. (The build host has minimal
  free disk and no guaranteed network; this constraint is load-bearing, not
  stylistic.)
- **Deterministic** — no ambient `time` / `random` / `datetime.now()` in any
  scored path. Timestamps and seeds are inputs. Same inputs produce byte-identical
  outputs, modulo an explicit run id.
- **Nothing can spend money.** The optional paid-API adapter is disabled by
  default and sits behind seven gates (opt-in → cache → estimate → dry-run →
  verified pricing → ceiling → transport). No CLI subcommand can spend, the
  package ships **no network client**, and an autouse fixture disables outbound
  TCP for every test. See [`docs/API_ADAPTER.md`](docs/API_ADAPTER.md).
- **Redaction runs before every write** — traces, spans, and the rendered report.
  Credential-like strings are masked in free text and *refused* in join keys.

## What is not claimed

- **These are synthetic fixtures with injected defects.** 18/18 recall on a suite
  built to be detected is evidence that the rules fire as specified. It is not a
  recall estimate on real regressions in real systems.
- **The eight controls are not a false-positive rate.** They test the specific
  false positives the fixtures encode — a key reorder, sub-threshold drift,
  harness noise — not production traffic.
- **No live model was called and no cost was incurred.** Latency is *recorded* in
  the trace, not measured. Cost is an *estimate* under a synthetic price list,
  from a `chars / 4` tokenizer. Neither is a measurement of a real system.
- **No learned model or semantic judge scored anything.** Every metric is a
  deterministic function of two traces and a case's declared properties.
  `semantic_similarity_stub` and explanation quality are declared placeholders and
  can never raise a flag.
- **Infra attribution can mask a real regression.** When every error on a
  candidate record is `origin=infra`, that case's flags are withheld and listed
  under "what the engine declined to say". A genuine application regression
  co-occurring with harness noise on the same case is missed. That is a documented
  tradeoff, not an oversight.
- **The import contracts have not met a real emitter.** Six importers are declared
  and pinned against committed synthetic examples; no sibling repository is
  required, present, or read.

The full list is [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md).

## Where this goes next

Each direction is an open question plus what would have to be built to answer it.
None of them is claimed by this repository today
([`docs/PAPER_PATH.md`](docs/PAPER_PATH.md)).

1. **Statistical detection under stochasticity.** With sampled models, when is a
   behavioral delta a regression rather than noise? Add a multi-sample adapter;
   replace threshold flags with significance tests controlling a stated
   false-discovery rate; measure detection power against sample budget.
2. **Clustering as root-cause attribution.** Inject *k* independent defect types
   and measure whether clustering recovers *k* homogeneous groups against a known
   confusion matrix.
3. **App-vs-infra attribution as a measurable claim.** Formalize the rules as a
   classifier over labeled incidents; report precision/recall and the asymmetric
   cost of each error type. This is research question 9 promoted to a number.
4. **Process-metric validity.** Do retries, failed tool calls, and sequence drift
   predict outcomes users care about, or are they gameable proxies? Calibrate
   severity accordingly.
5. **Grounding without a semantic model.** Benchmark the stdlib evidence scorers
   against a learned-entailment baseline and state precisely where the placeholder
   must be replaced.
6. **Cross-system trace portability.** Run the import contracts against real
   emitters and measure how much of each declared shape survives first contact.
   The four rules — account for every field, invent no measurement, guess no
   attribution, import no secret — are what should survive.

As a product, the shortest path is the CI gate that already exists: `llmreg
compare --fail-on critical` on every model, prompt, or tool bump, with the
rendered report attached to the pull request.

## Repository map

```
src/llm_regression_observability/  core package (schemas, evaluators, comparison, ...)
evals/fixtures/                    six synthetic fixture suites (26 cases)
evals/suites/                      suite manifests (smoke.yaml, external.yaml)
data/fixtures/                     recorded baseline/candidate RunRecords
data/external/                     synthetic examples of each importable trace shape
results/                           generated runs / comparisons / reports (gitignored)
docs/                              methodology, trace format, metrics, safety, plan
docs/milestones/                   per-milestone specifications
scripts/                           env check, pipeline wrappers, gates, site server
site/                              multi-page static documentation website
tests/                             956 CPU-only offline tests
```

## Documentation

- [`docs/PLAN.md`](docs/PLAN.md) — architecture, contracts, milestones, gates
- [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) — why multi-dimensional evaluation
- [`docs/TRACE_FORMAT.md`](docs/TRACE_FORMAT.md) — run & comparison schemas
- [`docs/METRICS.md`](docs/METRICS.md) — metric definitions
- [`docs/REGRESSION_DETECTION.md`](docs/REGRESSION_DETECTION.md) — thresholds, false positives
- [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md) — traces, spans, metrics
- [`docs/RESULTS.md`](docs/RESULTS.md) — every number this repository claims
- [`docs/INTEGRATION_CONTRACTS.md`](docs/INTEGRATION_CONTRACTS.md) · [`docs/API_ADAPTER.md`](docs/API_ADAPTER.md)
- [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) · [`docs/SAFETY.md`](docs/SAFETY.md) · [`docs/REPRODUCIBILITY.md`](docs/REPRODUCIBILITY.md) · [`docs/PAPER_PATH.md`](docs/PAPER_PATH.md)

## Project context

Built as an independent research/engineering artifact and as a foundation that can
import traces from a code-agent runtime, BioAgentBench, BioScientist OS, and
agent-control-bench. Those repositories are **not** required to build or test this
one; their trace shapes are declared here and pinned against committed synthetic
examples.

## License

MIT — see [`LICENSE`](LICENSE).
