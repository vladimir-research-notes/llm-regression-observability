# Paper path — honest future research directions

This document sketches where the work could go as *research*, without
overclaiming what the current scaffold demonstrates. Each direction is framed as
an open question plus what would have to be built and shown to answer it.

## 1. Statistical regression detection under stochasticity

**Question.** With live, sampled models, when is an observed behavioral delta a
real regression versus sampling noise?

**Path.** Add a multi-sample adapter; compute per-metric distributions across n
samples; replace threshold flags with significance tests (bootstrap CIs,
sequential testing) that control a stated false-discovery rate. Show detection
power vs sample budget on synthetic ground truth.

## 2. Failure clustering and root-cause attribution

**Question.** Can correlated failures be grouped into a small number of
actionable root causes automatically?

**Path.** Extend Milestone 6 clustering from feature-based grouping to a
labeled-taxonomy evaluation: inject k independent defect types, measure whether
clustering recovers k groups with high homogeneity/completeness, and report
against a known confusion matrix.

## 3. Application vs. infrastructure attribution

**Question.** How reliably can application regressions be separated from harness
noise, and what is the cost of each misattribution?

**Path.** Formalize the attribution rules as a classifier with a labeled set of
app/infra incidents; report precision/recall and the asymmetric cost of the two
error types. This is research question #9 promoted to a measurable claim.

## 4. Metric validity for agentic "process" regressions

**Question.** Do process metrics (retries, failed tool calls, tool-sequence
drift) predict outcomes users care about, or are they proxies that can be gamed?

**Path.** Correlate process-metric deltas with final-success deltas across many
synthetic agents; identify where a "correct answer, worse process" case should or
should not be flagged, and calibrate severity accordingly.

## 5. Grounding/evidence regression without a semantic model

**Question.** How far can deterministic, dependency-free evidence scoring go
before a learned entailment model is required?

**Path.** Benchmark the stdlib evidence scorers against a held-out set with known
unsupported claims; quantify the gap to a semantic entailment baseline and state
precisely where the placeholder `semantic_stub` must be replaced.

## 6. Cross-system trace portability

**Question.** Can one regression-observability contract serve traces from
multiple agent runtimes?

**Path.** Realize the Milestone 10 import contracts against a code-agent runtime,
BioAgentBench, BioScientist OS, and agent-control-bench; show the same detectors
and report generator run unchanged on imported traces.

## Non-claims

This scaffold does **not** yet demonstrate any of the above. It provides the data
contracts, deterministic fixtures with injected ground-truth regressions, and the
detection/report pipeline that make these studies runnable. Each direction above
would need its own experiment, dataset, and write-up before any empirical claim
is made.
