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

Token counts use a transparent char-based estimator and a fixture price table.
Costs are **estimates for relative comparison**, not bills. Real tokenization and
real pricing differ.

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
