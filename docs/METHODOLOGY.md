# Methodology

## Why exact-match evaluation is insufficient

Classical regression testing asserts `output == expected`. For LLM applications
this fails on every axis that matters:

- **Stochasticity.** The same prompt yields different strings across runs and
  across model versions, most of them equally correct.
- **Structural equivalence.** `{"a": 1, "b": 2}` and `{"b": 2, "a": 1}` are the
  same object; two correct summaries share no substring.
- **Multi-step process.** An agent can produce the right final answer through a
  worse process — more retries, more failed tool calls, a disallowed tool, twice
  the latency, twice the cost. Exact match on the final answer is blind to all of
  this.
- **Grounding.** A fluent answer can cite the wrong document or make an
  unsupported claim. The string looks fine; the behavior regressed.

A test that flags every string change is uselessly noisy; a test that ignores
everything but the final string is uselessly blind.

## The reframing

> A regression is not "the output changed." It is **"a measurable behavioral
> property got worse."**

We therefore evaluate a change across independent dimensions, each with a
deterministic scorer and its own regression flag, and compare a **baseline**
(known-good version) against a **candidate** (the change) case by case.

## Dimensions measured

1. **Final task success** — did the output satisfy the case's expected
   properties (not exact string)?
2. **Structured-output validity** — does the output parse and validate against
   the declared JSON schema? Which fields drifted?
3. **Tool-call behavior** — were only allowed tools used, with expected
   arguments, in a comparable order?
4. **Retrieval / evidence grounding** — are claims cited, are citations correct,
   is evidence coverage adequate, how many claims are unsupported?
5. **Latency** — p50/p95 shifts against thresholds.
6. **Cost** — estimated token cost shifts against thresholds.
7. **Refusal / safety behavior** — over-refusal, under-refusal, changed risk
   classification on safe synthetic prompts.
8. **Error / retry patterns** — did error rate or retry count increase, and is
   the cause application-level or infrastructure noise?

## Baseline vs candidate

Cases are paired by `case_id`. For each pair the comparison engine computes
per-dimension deltas, raises `RegressionFlag`s where a delta crosses a
configurable threshold, and aggregates flags by suite and by category. Failures
are clustered so a report can say "12 cases regressed, 9 of them for the same
reason" rather than listing 12 unrelated diffs.

## Application regression vs harness noise

Not every degradation is the application's fault. A timeout from a flaky mock, a
transport error, or a fixture problem is **infrastructure noise**. Errors are
tagged (`app` vs `infra`); only application-attributable degradations raise
regression flags, and the report separates the two so a reviewer is not paged for
a harness hiccup. This distinction is a first-class goal, not an afterthought.

## Determinism as a precondition

Because we compare two runs, the runs must be reproducible. All randomness and
all timestamps are **inputs** (recorded in fixtures or passed in run config),
never ambient. The mock/recorded/scripted adapters are fully deterministic, so
the "known regression" injected by the scripted adapter is detected the same way
every time — which is how the detector itself is tested.

## What this methodology does *not* claim

- It does not rank models or produce a leaderboard.
- It does not certify safety, correctness, or production readiness.
- It does not evaluate on real user data — all fixtures are synthetic.

See [`LIMITATIONS.md`](LIMITATIONS.md).
