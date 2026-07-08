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
`$0.015` per 1k output tokens, in `executor.CostModel`. Those prices are
placeholders in a plausible order of magnitude; they are not any vendor's real
pricing, and nothing in the default path calls a paid API. Costs are **estimates
for relative comparison**, not bills. Real tokenization and real pricing differ.

Where an adapter supplies its own token counts or cost (the recorded fixtures
do), the executor treats them as recorded inputs and does not overwrite them.
`run_meta.json` reports `tokens_estimated_cases` and `cost_estimated_cases` so
an artifact never hides which numbers were derived rather than recorded.

## Recorded, not measured, latency

`latency_ms` is a value the adapter reports, read from a fixture. The executor
does **not** time the adapter. Wall-clock timing would put an ambient value into
a scored field, so two runs on the same inputs would stop being byte-identical
and every latency assertion would become flaky. The cost of this choice is real:
the framework cannot currently detect a latency regression that exists only in
wall-clock time on live infrastructure. It detects latency regressions *as
reported by the trace source* — which is what an imported production trace or a
live adapter (Milestone 11) would supply.

## Placeholder scorers

Some scorers are explicit placeholders — notably explanation-quality in the
refusal suite and any semantic-similarity scoring (`semantic_stub.py`), which is
a deterministic stand-in and **not** a learned semantic judge. These are labeled
as placeholders in code and docs.

## Attribution is heuristic

The application-vs-infrastructure attribution is rule-based and conservative. It
reduces false positives from harness noise but can misattribute an unusual
failure. It reports its confidence rather than claiming certainty.

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
