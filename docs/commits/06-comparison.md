# Milestone 6 — Baseline vs candidate comparison

## What changed
The heart of the system: two runs of the same suite now produce per-case deltas,
regression flags with severity and app/infra attribution, failure clusters, and a
summary. This is where the research question is operationalized.

- **`src/llm_regression_observability/comparison/`** — new package, seven modules:
  - `base.py` — `ComparisonError`; `RunSide` (a loaded, scored side); `delta()` /
    `relative()`; `metric_value()` / `metric_detail()`; and the split between
    `BEHAVIORAL_FIELDS` and `IDENTITY_FIELDS` / `PROVENANCE_FIELDS`.
  - `baseline.py` — `load_run_side()` / `load_baseline()`, `resolve_suite_path()`.
  - `candidate.py` — `load_candidate()`, `check_comparable()`, `Comparability`.
  - `diff.py` — `pair_cases()`, `changed_fields()`, `CaseDiff`, `diff_case()`.
  - `regression.py` — `Thresholds`, the eight rule functions, `PROPERTY_CATEGORY`,
    `TRIGGER_METRICS`, `evaluate_flags()`.
  - `clustering.py` — `cluster_failures()`, `SIGNATURE_LABELS`, `cluster_id_for()`.
  - `aggregate.py` — `compare()`, `write_comparison()`, `compare_runs()`,
    `ComparisonMeta`, `ComparisonResult`.
- **`cli.py`** — `llmreg compare --baseline --candidate [--out] [--comparison-id]
  [--dry-run] [--fail-on none|warn|critical]`.
- **`scripts/05_compare_runs.py`** — the smoke comparison, no editable install
  required. (`04` was taken by the Milestone 5 scorer, as that milestone's review
  note predicted.)
- **`evals/suites/smoke.yaml`** — adds `cost_abs_usd: 0.0005`.
- **`tests/test_comparison.py`** — 96 CPU-only tests, built around an
  `EXPECTED_FLAGS` table of the complete flag set per fixture case.
- Docs/site: milestone doc → Complete; `REGRESSION_DETECTION.md` rewritten against
  the implementation; `RESULTS.md` gained the detection, cluster and control
  tables; `TRACE_FORMAT.md` documents `ComparisonMeta`, `FailureCluster` and the
  seven delta fields; `LIMITATIONS.md` gained three entries;
  `REPRODUCIBILITY.md` adds the compare step; eight `site/` pages updated.

## Why / key decisions
- **One defect, one signal.** `task_success` is the *union* of a case's declared
  `expected_properties`, and most of those properties belong to another dimension
  (`json_schema_valid` to the structured scorer, `expected_tools` to the tool
  scorer, `should_refuse` to the refusal scorer). Flagging every `task_success`
  drop as a `final_answer_regression` would have reported all 18 fixture defects
  twice and made `flags_by_category` a histogram of itself. So the drop is
  *attributed*: `PROPERTY_CATEGORY` maps each property to its owning category, and
  `final_answer_regression` fires only when a newly-failed property is answer-level.
  Visible consequence: `at_more_retries` has `score_delta = -1.0` (it violated
  `max_tool_errors: 0`) and raises `error_rate_regression` alone.
- **One flag per (case, category).** A wrong tool is also a missing tool, an extra
  tool, and a sequence drift. Four triggers, one finding: they collapse into the
  strongest severity, which names the rest in its `explanation`.
- **A *new* unsupported claim is critical; a claim that *lost* support is a
  warning.** Both raise `unsupported_claim_count` 0 → 1, so the count cannot
  separate them. The rule compares claim **texts**: `re_unsupported_claim`'s
  candidate asserts something the baseline never said (the model invented a fact —
  critical); `re_wrong_source`'s makes the same claim but cites the wrong document
  (the model degraded its grounding — warn). This is what lets both match their
  ground truth without a special case.
- **Under-refusal is critical; over-refusal is a warning.** Answering something the
  baseline declined loses correctness. Declining something benign loses quality.
- **Latency and cost each need two floors.** A relative move *and* an absolute one,
  so 2 ms → 3 ms never fires and a slow case that got proportionally no worse never
  fires. The fixture controls sit deliberately between them.
- **A cost delta across two price lists measures the price list.** If the two runs
  used different `CostModel`s, the cost rule is disabled and the reason is written
  to `comparison_meta.json` — the false positive `evaluators/cost.py` warned about.
- **`field_diff_count` is not a trigger.** It counts extra fields regardless of
  `additionalProperties`, so a rise while validity holds means the schema *permits*
  the field. The Milestone 5 review note raised this; the answer is a documented
  non-trigger plus a test.
- **Infra attribution withholds the whole record's flags, and records them.** A
  harness timeout inflates latency, fails tool calls, and truncates output; blaming
  the application for any of it would be wrong. `comparison_meta.json` carries
  `infra_attributed_case_ids` and `infra_suppressed_categories`, so nothing is
  silently dropped. The masking risk is documented in `LIMITATIONS.md`.
- **Pairing is total.** A case on one side only is `unpaired` — never scored as a
  success, never as a failure. An absent candidate is not a passing candidate.
- **A third artifact, `comparison_meta.json`.** `summary.json` answers "what
  regressed"; the sidecar answers "what did the engine decline to say, and why".
  An unpaired case and an infra-suppressed flag are both invisible in the summary's
  counts, and both are exactly the kind of silence that makes an evaluation harness
  lie. No Milestone 1 schema was changed to add it.
- **Clustering is grouping, not learning.** Two flags cluster when they share
  `(category, failure signature)`. The signature carries the offending symbol
  (`disallowed_tool:shell_exec`) because "the model started calling `shell_exec`" is
  a root cause and "a tool was wrong" is not. A fitted distance metric could not be
  reproduced from the artifact — the same objection this project raises to learned
  judges. Cluster ids are content-derived, so adding a case never renumbers them.
- **No non-finite number reaches an artifact.** `relative()` returns `inf` from a
  zero baseline; `_finite()` rejects it, and the latency/cost rules fall back to
  their absolute floors. `json.dumps(float("inf"))` emits `Infinity`, which no
  strict JSON reader accepts.
- **Summaries and cluster labels never echo model text.** They are built from
  metric names, numbers, tool names and property names, so they are safe to print
  before the Milestone 9 redactor exists.

## Validation run
- `python3 -m pytest -q` → **441 passed** (6 scaffold + 8 smoke + 54 schema +
  25 fixtures + 46 adapters + 51 executor + 155 evaluators + 96 comparison).
- `python3 -m pytest -q tests/test_comparison.py` → 96 passed.
- `ruff check src tests scripts/05_compare_runs.py` → clean (optional dev extra).
- **Detection on ground truth.** Across the six fixture suites: 18 injected
  defects, **18 detected** with the category *and* severity `expected.json`
  annotates, 0 missed; 8 negative controls, **0 false positives**. Asserted twice —
  once against the fixtures' own oracle, once against an `EXPECTED_FLAGS` table
  that pins the *complete* flag set per case, so a co-flag cannot appear unnoticed.
- **`at_failed_tool` is the only case with two flags**, and correctly so: its
  candidate both errors (`error_rate_regression`, warn) and degrades its answer
  from "done" to "partial" (`final_answer_regression`, critical). The fixture note
  says exactly that.
- **The scripted candidate reproduces all 18 categories**; four severities are
  harsher than the recorded candidate's, because `ScriptedRegressionAdapter` injects
  a *representative* defect per category rather than the fixture's own. Pinned in
  `SCRIPTED_SEVERITY_DIVERGENCE` rather than described.
- `python3 scripts/05_compare_runs.py` → 8 paired, 0 unpaired, 6 regressed, 6 flags
  (4 critical, 2 warn), 1 infra-attributed case surfaced and excluded, 6 clusters;
  latency p50 `+20.0 ms`, p95 `+65.0 ms`; cost delta `$0.000000`.
- `--fail-on critical` → exit 1; `--fail-on none` (default) → exit 0.
- Cross-process determinism: comparing into a second output root in a fresh process
  produces byte-identical `comparison.jsonl`, `summary.json`, and
  `comparison_meta.json`; `write_comparison()` re-hashes the file it wrote against
  `ComparisonMeta.records_sha256`.
- Answers three questions the Milestone 4 and 5 review notes left open:
  `score_delta == 0` is not "no regression"; `field_diff_count` is not a trigger;
  the `--continue-on-error` record needs no special case (its `infra` error
  attributes the whole record).

Next: Milestone 7 — reports (Markdown + optional HTML from these comparison
records).
