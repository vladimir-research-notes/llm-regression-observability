# Limitations and non-goals

This project is deliberately scoped. Being explicit about what it does **not** do
is part of the quality bar.

## Synthetic fixtures

All cases are synthetic and small. They are designed to exercise each scorer and
to contain **known, injected** regressions (via the scripted adapter) so the
detector can be tested against ground truth. They are **not** a representative
sample of any real workload, and results on them do not generalize to production
traffic.

## No universal model evaluation

This is not a benchmark or a leaderboard. It measures *relative behavioral
change* between a baseline and a candidate on a fixed suite. It does not rank
models, score absolute quality, or certify that a model is good, safe, or
correct.

## Deterministic mocks, not live models

The default path uses mock / recorded / scripted adapters. That is a feature —
it makes the pipeline reproducible and free — but it means the framework is
tested against *recorded* behavior, not live model stochasticity. Live-model
variance handling (multiple samples, statistical significance) is a documented
future direction, not a current claim.

## Estimated, not measured, cost

Token counts use a transparent char-based estimator (~4 characters per token, no
tokenizer) and a **synthetic price list** — `$0.003` per 1k input tokens and
`$0.015` per 1k output tokens, in `pricing.CostModel`. Those prices are
placeholders in a plausible order of magnitude; they are not any vendor's real
pricing, and nothing in the default path calls a paid API. Costs are **estimates
for relative comparison**, not bills. Real tokenization and real pricing differ.

Where an adapter supplies its own token counts or cost (the recorded fixtures
do), the executor treats them as recorded inputs and does not overwrite them.
`run_meta.json` reports `tokens_estimated_cases` and `cost_estimated_cases` so
an artifact never hides which numbers were derived rather than recorded.

## A cost ceiling is only as good as its price list

The max-cost guard (`adapters/api_guard.py`, Milestone 11) refuses a call whose
estimated cost would breach a configured USD ceiling. It enforces that ceiling
against `chars / 4` tokens times prices an operator supplied. If the price list is
wrong, the ceiling is wrong by the same factor, and nothing in this repository can
detect that: it has no network and cannot look up a price.

The mitigation is provenance rather than verification. A `PriceList` carries
`verified` (has a human compared these to the provider's published prices?) and
`priced_at` (when?), the spend path **refuses an unverified list** unless the
operator explicitly sets `allow_unverified_pricing`, and a `verified: true` list
with no `priced_at` is rejected outright. That converts a silent failure into an
operator decision. It does not make the estimate correct.

Two further gaps: the ceiling bounds *estimated* spend before each call, so a
provider that bills differently than its published prices is not caught; and
`allow_unverified_pricing` is a real escape hatch that permits spending against
numbers nobody checked.

## The paid-API transport is untested by construction

`adapters/api_transport.py` is a reference implementation of the seam a real
provider client plugs into. It has **no test coverage and never runs in CI**,
because running it would cost money. Its request/response mapping is written from
the SDK's documented shape and has not been executed against a live response.
Nothing else in the package imports it, `anthropic` is not a dependency, and the
guarded adapter refuses a live call when no transport is supplied — so the absence
of coverage is contained rather than load-bearing. But it is an absence.

Relatedly, the repository-wide network block in `conftest.py` patches the `socket`
module. A library that bypassed it (via `ctypes`, say) would not be caught. The
guarantee is "no test opens a TCP connection through the standard library", not
"no test can possibly emit a packet".

## Recorded, not measured, latency

`latency_ms` is a value the adapter reports, read from a fixture. The executor
does **not** time the adapter. Wall-clock timing would put an ambient value into
a scored field, so two runs on the same inputs would stop being byte-identical
and every latency assertion would become flaky. The cost of this choice is real:
the framework cannot currently detect a latency regression that exists only in
wall-clock time on live infrastructure. It detects latency regressions *as
reported by the trace source* — which is what an imported production trace or the
guarded API adapter's transport supplies. That transport is the one place in the
package permitted to read a clock, because there the ambient value *is* the
measurement being taken; a response reporting `latency_ms = 0.0` is refused rather
than recorded, for the reason `docs/INTEGRATION_CONTRACTS.md` gives.

## Placeholder scorers

Some scorers are explicit placeholders — `explanation_quality_placeholder` in the
refusal suite, and `semantic_similarity_stub`, a lexical token-overlap ratio and
**not** a learned semantic judge (it rates a paraphrase low and a fluent
contradiction high). Both are listed in `evaluators.NEVER_FLAG_METRICS`, so the
comparison engine cannot raise a regression from either. They are labeled as
placeholders in code, in `MetricResult.detail`, and in the docs.

## Evidence support is recorded, not judged

`evidence_coverage` and `unsupported_claim_count` read the `supported` field of
`RetrievalTrace`. Nothing in this framework reads a snippet and decides whether
it entails a claim — deterministic entailment is out of scope, and a learned
entailment model is a dependency this project does not take. An imported trace
that never sets `supported` scores every claim as unsupported, which is the
conservative direction. `citation_correctness` checks only that a cited
`source_id` exists, so a real document cited for the wrong claim is caught by
`unsupported_claim_count`, not by `citation_correctness`.

## Not every declared property is checked

`task_success` scores a case against a **closed vocabulary** of structural
property checks (`evaluators.exact.PROPERTY_CHECKS`). Anything else a fixture
declares is *advisory*: reported in the metric's `detail`, not scored. Today one
fixture property is advisory (`expected_source_for_color`, in
`retrieval_evidence/re_wrong_source`) — it is prose, and the defect it describes
is caught by the evidence metrics instead. One more is *pairwise*
(`final_output_stable`), which no single record can answer. A case whose
properties are all advisory or pairwise scores `task_success = 1.0` with
`passed = None` and `detail["vacuous"] = true`: nothing failed, and nothing was
tested. `tests/test_evaluators.py` pins the exact advisory set, so a new
unchecked property cannot appear silently.

## The JSON Schema validator is a subset

The core is dependency-free, so `evaluators/json_schema.py` implements the
keyword subset in `SUPPORTED_KEYWORDS` rather than depending on `jsonschema`.
Keywords outside it (`$ref`, `allOf`, `format`, …) are **surfaced** in
`detail["unsupported_keywords"]`, never silently ignored — ignoring one would let
an invalid document score as valid. A test asserts every committed fixture schema
uses only supported keywords.

## Attribution is heuristic, and infra attribution can mask a real regression

The application-vs-infrastructure attribution is rule-based and conservative:
`errors.attribution()` returns `infra` only when a record errored and *every*
error was infra-origin, so one app error makes the whole record `app`. It reduces
false positives from harness noise but can misattribute an unusual failure. It
reports its confidence rather than claiming certainty.

The comparison engine acts on that verdict: when a candidate record is
`infra`-attributed, **every** flag it would have raised is withheld, not just the
error-rate one. That is the right call when the harness timed out — a timeout
inflates latency, fails tool calls, and truncates output, and blaming the
application for any of it would be wrong. But it means **a genuine application
regression that co-occurs with an infra error on the same case is masked**. The
engine does not hide this: the case appears in `comparison_meta.json` under
`infra_attributed_case_ids`, and the withheld categories under
`infra_suppressed_categories`, so a reader can see exactly what was not said.
Re-running the suite is the remedy — an infra error should not reproduce, and an
application regression will.

## A regression flag is a threshold crossing, not a judgment of importance

Severity is assigned by rule (`critical` for correctness lost, `warn` for quality
or efficiency degraded), not by impact. A `critical` flag on a synthetic fixture
is not a claim that the change matters to any user. Likewise, `flags_by_category`
counts findings after collapsing one flag per (case, category); it is not a count
of everything that moved. The per-case `ComparisonRecord` deltas and the flag
explanations carry the detail.

## The report can only say what the comparison artifacts say

`llmreg report` reads `comparison.jsonl`, `summary.json` and
`comparison_meta.json`, and nothing else — not the runs, not the fixtures, not the
engine. That makes a report reproducible from a directory alone, at two costs.

**Deltas, not absolutes.** `ComparisonRecord` records `json_validity_delta`, not
the baseline and candidate validity values, so the structured-output table shows
`-1` ("was valid, is not") rather than `1.0 → 0.0`. The same holds for the tool and
evidence dimensions.

**A `0` row is ambiguous.** In the per-category table, `0 flags` means no flag
fired. It does not distinguish a dimension that was evaluated and came back clean
from one that no case in the suite declared. The report prints all eight categories
anyway — a missing row would read as an oversight — and says this in its own
limitations note.

**A collapsed flag is one row.** Milestone 6 collapses a category's triggers into a
single flag whose `explanation` names the runners-up after `also:`. The report
renders that string; it cannot split "wrong tool *and* missing tool *and* sequence
drift" into three rows, because `RegressionFlag` has no structured `detail` field.

## Redaction can rewrite text that a scorer then reads

`traces/redaction.py` masks a `RunRecord`'s content fields *before* the record is
hashed, persisted, or scored. That is the correct order for safety and the wrong
order for nothing — except that a **false-positive mask changes a scored string**. The
heuristic tier (credential-named assignments, emails, generic high-entropy tokens)
fires on suspicion by design, so a synthetic fixture containing a long mixed-class
token would be scored on `[REDACTED:high_entropy_token]` rather than on its own text.

Three things bound the risk rather than eliminate it. The generic-token rule requires
24+ characters *and* mixed character classes *and* entropy above a prose floor, which
clears SHA-256 digests, UUIDs and hyphenated run ids.
`redaction_status` records that masking happened, so a surprising score has a visible
cause. And `test_the_committed_fixtures_pass_through_redaction_unchanged` asserts all
52 committed fixture records are byte-identical after redaction — a future rule that
starts rewriting scored text fails that test rather than producing a mysterious
evaluator drift.

Identity fields are never rewritten, so pairing and tool-call diffing cannot be
corrupted this way; a secret in one of those refuses the run instead. See
[`SAFETY.md`](SAFETY.md).

## Span timings assert ordering, not duration

A trace records exactly one duration per case, `RunRecord.latency_ms`. The span
tree therefore gives the `request` span that recorded interval and hands its
application children an **equal partition** of it, in trace order. A child span's
width is `latency_ms / step_count`; it is a slot, not a measurement, and reading it
as "the tool call took 63 ms" is wrong. Every span carries
`attributes["timing"]` (`recorded` / `derived` / `none`) to say which it is, but a
consumer that ignores that field will be misled. Cross-category interleaving is not
recorded either, so the child order — model call, then tool calls, then retrievals
— is **canonical, not observed**: a RAG trace and an agent trace get the same
layout. See [`OBSERVABILITY.md`](OBSERVABILITY.md).

## `tool_error_rate_mean` mixes app and infra origins

`tool_error_rate` is descriptive: an infra timeout fails a tool call just as an
application bug does. Its suite mean therefore sits in `application_metrics` while
counting both — on the smoke candidate it is `0.125`, raised equally by
`at_more_retries` (app) and `at_infra_noise` (infra). This cannot cause a false
regression, because the `error_rate_regression` rule reads `app_error_count` and
never this metric. But the suite number does blur the distinction the observability
layer otherwise enforces. It is pinned by
`tests/test_observability.py::test_tool_error_rate_mean_mixes_origins_and_is_pinned_here`
rather than quietly corrected, so a future fix has to be deliberate.

## The observability layer has no consumer yet

`spans.jsonl` and `aggregate.json` are written, validated, and read by tests and by
humans. Nothing downstream reads them: the comparison engine (Milestone 6) and the
report generator (Milestone 7) predate the layer and still compute their own
latency and cost statistics. A run's harness metrics therefore do not appear in the
report's "what the engine declined to say" section, even though that is exactly
where they belong. Recorded in
[`review_notes/08-observability-human-review.md`](review_notes/08-observability-human-review.md).

## Detection recall is measured on defects built to be detected

The 18/18 recall in [`RESULTS.md`](RESULTS.md) is measured on synthetic fixtures
whose defects were written alongside the rules that catch them. It demonstrates
that the rules fire as specified, that the negative controls do not fire, and that
the pipeline is byte-reproducible. It is **not** evidence of recall on real
regressions in real systems, and no such claim is made.

## No production deployment

There is no server, no persistence layer beyond local files (SQLite index is
optional and regenerable), no auth, and no multi-tenant story. This is a
local-first research/engineering tool.

## Not an authority

This framework is not a medical, legal, or security authority. See
[`SAFETY.md`](SAFETY.md).

## Current status

Implementation status is tracked per milestone in [`PLAN.md`](PLAN.md),
[`docs/milestones/`](milestones/), on the [website](../site/index.html), and in
[`RESULTS.md`](RESULTS.md). Anything not yet implemented is marked as such rather
than implied to exist.
