# Integration contracts

> **Status: implemented (Milestone 10).** The envelope, the six importers, the
> `llmreg import-trace` / `llmreg export-run` commands, and the end-to-end
> demonstration all exist and are exercised by
> `tests/test_integration_contracts.py` (100 tests).
>
> **No sibling repository is required, present, or read.** The five external trace
> shapes below are *declared by this repository* and pinned against committed
> synthetic examples under `data/external/`. That is a contract, not a
> reverse-engineered dump: if a real emitter differs, the importer is the single
> place the difference is absorbed, and its example fixture is where the
> disagreement becomes a test failure instead of a silent mis-score.

## Why this exists

Everything upstream of Milestone 10 assumes a trace was produced *here*, by an
adapter this repository owns. That assumption is what the framework has to give
up to be useful to anything else. So: an external runtime emits its own trace
shape, an **importer** maps it onto `RunRecord`, and the evaluators, the
comparison engine, the span projection and the report generator run over the
result **unchanged**.

Adding a source is: write a `TraceImporter`, register it. No evaluator, no
comparison rule, no report template, no schema change. That property is the
deliverable; the five importers are the evidence for it.

## Contract `1.0`

### The envelope

A bundle this repository exports (`llmreg export-run`) is a
`TraceBundle` — `src/llm_regression_observability/datasets/versioning.py`:

| Field | Meaning |
| --- | --- |
| `contract_version` | version of *this* envelope. Currently `1.0`. An unknown version is refused, not guessed at. |
| `source` | which system emitted `payload`. Only that system's importer may read it. |
| `source_schema` / `source_version` | that system's own schema id and version. |
| `exported_at` | a **recorded input**. `export_bundle()` requires one and reads no clock. |
| `suite_id` / `app_version` | which evaluation suite the records answer, and which side they are. |
| `record_schema` | a fingerprint of `RunRecord` at export time (below). |
| `payload_sha256` | digest of the payload's canonical JSON. A bundle edited after export does not verify. |
| `payload` | source-shaped, opaque to the envelope. |
| `notes` | free text, redaction-scanned like everything else. |

### The record fingerprint

`record_schema` is **derived from the `RunRecord` dataclass**, not declared:

```
record_schema_fingerprint() == "7c781c9c2fad"
```

A hand-maintained version constant is the one nobody remembers to bump. This one
changes the moment a field is added, removed or retyped, whether or not anybody
noticed — and `tests/test_integration_contracts.py` pins the current value, so a
trace-model change fails the suite until somebody decides what it means for a
consumer already holding a bundle. A bundle whose fingerprint differs from the
reading build's is **refused** in strict mode: every downstream score would
otherwise be computed against a shape the exporter never wrote.

### Bare documents

A sibling system will not wrap its traces in our envelope, and demanding that it
does would make the contract useless for its purpose. A bare source document
imports exactly as well, minus the four claims above, provided the caller names
the source (`--source`). Sniffing a shape to identify it is guessing precisely
where guessing costs the most, so it is not done.

### Spans are not part of the payload

`observability/spans.py` derives a span tree from `(EvalCase, RunRecord, metrics)`
as a pure projection. Shipping spans across the boundary would ship a *derivation*
the receiver can recompute and cannot verify. So the contract carries records; an
imported record projects into a valid span tree for free, and
`test_an_imported_record_projects_into_a_valid_span_tree` proves it without any
importer knowing what a span is.

## The four rules an importer obeys

Mapping two vocabularies onto each other is where evaluation harnesses quietly
start lying. The contract (`datasets/importers/base.py`) is defined by what an
importer may **not** do.

### 1. It may not drop a field in silence

Every key in a source document is accounted for exactly once: **mapped**, listed
in the importer's `ignored_fields` (a deliberate, documented drop — a chat
transcript, a workflow id), or **unknown**. An unknown key is a field the source
grew after the importer was written. In strict mode (the default) it raises; with
`--lenient` it lands in `ImportResult.unknown_fields` and in a warning. It never
simply disappears.

Strict-by-default is the right trade for an evaluation framework: you want to
know a field appeared *before* you score against it. The escape hatch exists for
when you don't.

### 2. It may not invent a measurement

Each importer publishes `absent_fields`: the `RunRecord` fields its source
structurally cannot fill. Latency is the one that bites. A missing `latency_ms`
imported as `0.0` is not missing data — it is a *number*, and the comparison
engine believes numbers:

```python
relative(0.0, 300.0) == -1.0        # zeroed candidate: "100% faster". No flag.
relative(300.0, 0.0) == float("inf") # zeroed baseline: unbounded latency_regression.
```

So a record with no recorded duration is **refused** in strict mode with that
sentence, rather than scored. `claude_api` is the source whose natural shape
tempts an importer into inventing one: the API response carries tokens and a stop
reason, and the *client* is what held a stopwatch.

Cost is different and is not refused: `cost_estimate_usd` left at `0.0` is priced
by the executor's `CostModel`, and `comparison/candidate.py` already disables the
cost rule across two different price lists.

### 3. It may not guess an attribution

`app` versus `infra` is this repository's distinction (research question 9), not a
field any external system emits. Each importer publishes `error_origins`, a table
from *that source's* error kinds to an origin. An unmapped kind resolves to `app`
**and warns**.

That direction is deliberate. `infra` withholds regression flags
(`comparison/regression.py`), so defaulting there would let an unrecognized error
silently suppress a real regression. A false flag gets investigated; a silent
suppression does not.

### 4. It may not import a secret

Milestone 9 observed that `redaction_status="flagged"` is unreachable on a trace
this project wrote — the executor refuses to persist one — and named the import
boundary as where it becomes reachable. It does, here. `finalize()` runs
`redact_record` over every imported record before returning it:

* **content** (`final_output`, `parsed_output`, tool arguments, error messages,
  snippets) is masked in place and the record is marked `redacted`;
* a certain-tier match in an **identity field** — a `case_id`, a `model_id`, a
  tool *name* — is `flagged`, and the import is refused naming the field and the
  pattern, **never the value**. There is no safe repair: masking a join key
  corrupts every downstream comparison, and keeping it writes the secret to disk.

The executor redacts again downstream. Redaction is idempotent, so the second
pass is a no-op.

## The six importers

| Source | Schema | What it is | Loses |
| --- | --- | --- | --- |
| `llmreg` | `llmreg/run-records` | this repo's own export; the identity map | **nothing** (round-trip tested) |
| `code_agent_runtime` | `code-agent-runtime/trace` | a coding agent's session, as a step list | the message transcript, `session_id` |
| `bioagentbench` | `BioAgentBench` | benchmark episodes: a task definition *and* an answer | the split label |
| `bioscientist_os` | `BioScientistOS` | a multi-node workflow, flattened | per-model attribution, per-node timing |
| `agent_control_bench` | `agent-control-bench` | refuse-or-answer trials and policy violations | — |
| `claude_api` | `anthropic/messages` | **recorded** Messages-API responses | thinking blocks, message/tool ids |

`llmreg` is the reference implementation. It is the only importer that *can* be
lossless, so it is the only one whose round trip is a meaningful test —
`import(export(records)) == records`, byte for byte. The five foreign importers
each lose something, and each says what.

### `code_agent_runtime`

```json
{"schema": "code-agent-runtime/trace", "schema_version": "1",
 "session_id": "...", "agent_version": "ca-1.4.0", "model": "synthetic-coder-1",
 "recorded_at": "2026-06-01T12:00:00Z",
 "tasks": [{"task_id": "tu_wrong_tool", "elapsed_ms": 200.0, "cost_usd": 0.002,
            "usage": {"input_tokens": 150, "output_tokens": 60},
            "steps": [{"kind": "tool", "tool": "search", "input": {"query": "synthetic-001"},
                       "outcome": "ok"},
                      {"kind": "message", "text": "..."}],
            "result": {"text": "...", "json": null},
            "failures": [{"type": "tool_retry", "detail": "..."}]}]}
```

| Source | `RunRecord` | Note |
| --- | --- | --- |
| `tasks[].task_id` | `case_id` | |
| `model` | `model_id` | |
| `agent_version` | `tool_version` | the agent binary versions the tool surface |
| — | `prompt_version` | absent; from `ImportContext`, else `"unknown"` |
| `steps[kind=tool]` | `tool_calls[]` | `order_index` is the position among **tool** steps |
| `steps[kind=message]` | *dropped* | transcript: free model text no scorer reads |
| `outcome` | `status` | `ok` → `ok`, `error` → `failed`, `skipped` → `skipped` |
| `elapsed_ms` | `latency_ms` | |
| `failures[].type` | `errors[].kind` | origin via the table below |

Error origins: `tool_retry`, `assertion_failed`, `invalid_tool_arguments` → `app`;
`tool_timeout`, `sandbox_error`, `rate_limited` → `infra`.

A message between two tool calls is **not a gap** in the tool sequence.
`evaluators/tool_calls.py` diffs sequences of tools, so `order_index` counts tools,
not steps.

### `bioagentbench`

The only source that ships its own **task definition**, so the only one that yields
`EvalCase`s as well as records. `task.expected` becomes `expected_properties`,
`task.documents` become `retrieval_docs`, and the evaluators score the episode with
no suite manifest anywhere in sight.

| Source | Target | Note |
| --- | --- | --- |
| `episodes[].wallclock_seconds` | `latency_ms` | **× 1000.** The quietest, most expensive bug an importer can have |
| `citations[].doc_id` / `quote` / `claim` / `supports_claim` | `RetrievalTrace` | `supported` is *recorded*, never judged |
| `task.documents[].doc_id` | `RetrievalDoc.source_id` | |
| `tokens.prompt` / `.completion` | token estimates | |
| `usd` | `cost_estimate_usd` | |

Error origins: `tool_error`, `invalid_answer` → `app`; `grader_error`,
`harness_timeout` → `infra`.

An unmarked `supports_claim` scores as **unsupported**, matching
`evaluators/evidence.py`: nothing here reads a quote and decides whether it entails
a claim, and the conservative reading is the safe one.

### `bioscientist_os`

The lossy one, in the set precisely so the loss has somewhere to be written down.

* **One run, many models.** `RunRecord.model_id` is a single string. A multi-model
  run becomes `multi:<sorted names joined by +>` and a warning says per-model
  attribution was lost. Keeping the first or last model would produce a `model_id`
  true of one node and false of the record.
* **Duration and cost are per node, and summed.** That is a *claim about a serial
  workflow*. A source that parallelizes needs a `duration_ms` on the run, not on
  the nodes.
* **`status: "failed"`** → `ErrorRecord(kind="workflow_failed", origin="app")`.
* The source carries a prompt but no expected properties, so its `EvalCase`
  declares nothing to check and `task_success` comes back **vacuous**
  (`value=1.0`, `passed=None`, `detail["vacuous"]`) rather than a fabricated
  pass. Nothing failed; nothing was looked for.

### `agent_control_bench`

Carries the *expectation* and the *decision* separately, which is the split
`evaluators/refusal.py` wants:

| Source | Target |
| --- | --- |
| `policy.must_refuse` | `expected_properties["should_refuse"]` |
| `policy.risk_label` | `expected_properties["expected_risk_label"]` |
| `response.refused` | `parsed_output["refused"]` |
| `response.risk_label` | `parsed_output["risk_label"]` |
| `policy_version` | `tool_version` (the guard rails are the tools) |
| `violations[].rule` | `errors[].kind` |

Error origins: `policy_bypass`, `unsafe_completion`, `over_refusal` → `app`;
`grader_crashed` → `infra`.

Putting the decision in `parsed_output` is not a convenience. `refusal_decision()`
prefers `parsed_output["refused"]` — the application's own answer — and falls back
to a lexical scan only when the trace does not label one. Every `refusal_match`
result records which it used, so an import that loses the label degrades *visibly*,
into `decision_source="lexical_fallback"`, rather than silently into a guess.

### `claude_api`

**A contract, not a client.** This importer reads a document somebody else
recorded. Nothing in it opens a socket. The paid-API adapter — dry-run mode, token
estimator, hard max-cost guard, response cache, explicit opt-in — is Milestone 11
and lives in `adapters/`. The committed example is hand-written and synthetic:
`CLAUDE.md` forbids committing a real API response, and a recorded response is the
most likely place for a credential to enter this repository.

| Source | Target | Note |
| --- | --- | --- |
| `content[type=text].text` | `final_output` | blocks joined by a blank line |
| `content[type=tool_use]` | `tool_calls[]` | `name` + `input`; block order |
| `content[type=thinking]` | *dropped* | reasoning text; not an answer, not a tool call |
| an unknown block type | **refused** | a dropped block may be a tool call, and a dropped tool call is a `tool_call_regression` that never fires |
| `usage.input_tokens` / `.output_tokens` | token estimates | |
| `stop_reason == "max_tokens"` | `ErrorRecord("truncated_output", "app")` | |
| `latency_ms` (beside the response) | `latency_ms` | **required in strict mode** — see rule 2 |
| — | `cost_estimate_usd` | absent; the executor's `CostModel` prices it |

A `refusal` stop reason is deliberately *not* turned into
`parsed_output["refused"]`: this source carries no risk label, and
`evaluators/refusal.py` reads the text and records
`decision_source="lexical_fallback"`, which is the honest description of what
happened.

## An imported trace, end to end

`evals/suites/external.yaml` names four cases drawn from the committed fixture
suites. That reuse is the point: **an external system that runs your evaluation
cases hands you traces keyed by your case ids**, and comparing them to your
baseline is the reason an import contract exists.

```bash
llmreg import-trace --source code_agent_runtime \
    --input data/external/code_agent_runtime/baseline.json \
    --suite evals/suites/external.yaml \
    --app-version external-baseline --run-id ext-baseline --out results/runs

llmreg import-trace --source code_agent_runtime \
    --input data/external/code_agent_runtime/candidate.json \
    --suite evals/suites/external.yaml \
    --app-version external-candidate --run-id ext-candidate --out results/runs

llmreg compare --baseline results/runs/ext-baseline \
               --candidate results/runs/ext-candidate --out results/comparisons

llmreg report --comparison results/comparisons/external-external-baseline-vs-external-candidate \
              --out results/reports/external_regression_report.md
```

Or in one step: `python3 scripts/09_import_external_trace.py`.

The candidate session carries two injected defects — a disallowed `shell_exec`
call, and a retried `search` — and two unchanged control cases. The **unchanged**
comparison engine, with the same thresholds the smoke suite uses, finds:

| Case | Flags |
| --- | --- |
| `tu_wrong_tool` | `tool_call_regression` (critical) |
| `at_more_retries` | `error_rate_regression` (warn) |
| `tu_control_stable` | — |
| `at_control_stable` | — |

2 detected, 0 false positives on the 2 controls. No evaluator, comparison rule or
report template was touched to get there.

### Why an import is *executed*, not written

An imported trace could have been written straight to `run.jsonl`. It is not.
`ImportedTraceAdapter` is a `ModelAdapter` whose behavior is a record somebody else
recorded, exactly as `RecordedModelAdapter` replays a committed fixture, and it is
handed to the ordinary `execute_suite()`. That buys, unchanged: identity
re-stamping, redaction before accounting, token estimation and cost pricing where
the source carried none, and `records_sha256` over the bytes actually written. A
second artifact writer would be a second place those five decisions are made, and
the two would drift.

The adapter is deliberately **not** registered in `adapters.ADAPTER_NAMES`: it has
no fixtures to construct itself from and no meaning without a document. It shows up
in `run_meta.adapter` as `imported-<source>`, so a report says where the trace came
from.

### `import_meta.json`

Written next to `run.jsonl`. `run_meta.json` says what the run *is*; this says what
the import **could not say** — `warnings`, `ignored_fields`, `unknown_fields`,
`absent_fields`, and which cases the source supplied a task definition for. A
comparison that reads clean over a trace half of which never arrived is exactly the
lie an evaluation framework must not be able to tell quietly, so the losses are an
artifact rather than somebody's terminal scrollback.

## Export

```bash
llmreg export-run --run results/runs/smoke-baseline-recorded \
                  --out results/external/smoke-baseline.json
```

`exported_at` defaults to the **run's own recorded timestamp**, never the wall
clock: two exports of one run must be byte-identical, or `payload_sha256` is
useless as a change detector. Cases travel with the records by default (`--no-cases`
to omit them), because a trace without its evaluation case is not scorable — the
case is where `expected_properties`, the JSON schema and the allow-list live.

## Limitations and open questions

* **The five external shapes are declared here, not observed.** No sibling
  repository was read. Each importer is a hypothesis about a shape, validated
  against a synthetic example this repository wrote. First contact with a real
  emitter will move some of these tables; the rules in
  `datasets/importers/base.py` are what should survive.
* **`ignored_fields` is documentation.** The mechanical check is per-call-site.
  A key deliberately ignored at one path is still *unknown* at another, which is
  the behavior you want, but it means the published list and the code are kept in
  step by review, not by a compiler.
* **A lenient import can score a zeroed latency.** `--lenient` exists so a source
  with no duration is usable at all; it does not make the resulting latency
  comparison meaningful. `import_meta.json` names `latency_ms` in `absent_fields`,
  and the operator has to read it.
* **Nothing validates a source's own semantics.** If `bioagentbench` reports
  `wallclock_seconds` that are actually milliseconds, the importer multiplies by
  1000 and nothing notices. A contract can pin a shape; it cannot pin a meaning.
* **One suite per import.** An imported document must cover exactly the cases the
  named suite defines — no more, no fewer. A trace answering a superset is refused
  rather than silently subsetted.

## Files

| Path | What |
| --- | --- |
| `src/llm_regression_observability/datasets/versioning.py` | envelope, fingerprint, export/read |
| `src/llm_regression_observability/datasets/importers/base.py` | the contract every importer holds to |
| `src/llm_regression_observability/datasets/importers/registry.py` | dispatch; `SOURCES` |
| `src/llm_regression_observability/datasets/importers/runs.py` | `ImportedTraceAdapter`, `ImportMeta`, `import_run` |
| `src/llm_regression_observability/datasets/importers/<source>.py` | one module per source |
| `data/external/<source>/*.json` | committed synthetic examples |
| `evals/suites/external.yaml` | the four-case suite an imported trace answers |
| `scripts/09_import_external_trace.py` | the end-to-end demonstration |
| `tests/test_integration_contracts.py` | 100 tests; the validation command below |

## Validate

```bash
python3 -m pytest -q tests/test_integration_contracts.py
python3 scripts/09_import_external_trace.py
```
