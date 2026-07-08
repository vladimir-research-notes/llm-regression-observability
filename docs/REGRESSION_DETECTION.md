# Regression detection

A regression is raised when a candidate's measured behavior is **worse than the
baseline by more than a threshold** on a given dimension. Detection is
deterministic and threshold-driven; nothing is learned or fitted.

## Inputs

- Paired `RunRecord`s (baseline, candidate) for the same `case_id`.
- Per-case `MetricResult`s from the evaluators.
- A `thresholds` table (suite-level defaults, overridable per case).

## Regression categories

| Category | Trigger metric(s) | Default threshold |
| --- | --- | --- |
| `structured_output_regression` | `json_schema_validity` drop, new parse failure | any drop to 0 on a case valid at baseline |
| `tool_call_regression` | `allowed_tool_ratio` drop, `tool_sequence_similarity` drop, missing/extra tools, arg diff | seq_sim < 0.8 or any disallowed/missing tool |
| `latency_regression` | `latency_delta_p50`, `latency_delta_p95` | p95 delta > +25% (and > +50 ms) |
| `cost_regression` | `cost_total_delta` | > +20% |
| `refusal_behavior_shift` | `refusal_match` flip, `risk_label_delta` | any flip vs expected |
| `evidence_grounding_regression` | `evidence_coverage` drop, `unsupported_claim_count` rise, `citation_correctness` drop | coverage drop > 0.2 or new unsupported claim |
| `final_answer_regression` | `task_success` drop | success 1 → 0 on a case |
| `error_rate_regression` | `error_rate` rise, `retry_count` rise (app-origin only) | error_rate delta > +0.1 |

Thresholds above are the scaffold's documented defaults; the implementation
reads them from the suite manifest so they can be tuned without code changes.

## Severity

Each flag gets a severity:

- **critical** — correctness lost (success 1→0, schema valid→invalid, disallowed
  tool used, new unsupported claim).
- **warn** — quality/efficiency degraded past threshold (latency, cost, partial
  evidence drop, tool arg drift).
- **info** — a change worth noting that did not cross a threshold.

## Attribution: application vs harness noise

Every candidate degradation is attributed:

- **app** — caused by the model/prompt/tool change. Raises a flag.
- **infra** — caused by the harness (timeout on a flaky mock, transport error,
  malformed fixture, incomplete trace). Recorded and surfaced in the report, but
  **does not** raise a regression flag.

Attribution uses the `origin` field on `ErrorRecord` and a small set of rules
(e.g. a `timeout` with no partial output on an otherwise-stable case is treated
as infra unless it reproduces deterministically). This is the mechanism behind
research question #9 — *distinguish application regressions from harness noise* —
and is intentionally conservative: when in doubt it flags as `app`, because a
missed regression is worse than a noisy one, but it labels its confidence.

## False positives and how they are controlled

- **Deterministic runs.** Mock/recorded/scripted adapters remove sampling noise,
  so a flagged delta reflects the change, not variance.
- **Thresholds with minimum absolute deltas.** A latency flag needs both a
  relative (+25%) and an absolute (+50 ms) move, so a 2 ms → 3 ms jitter never
  fires.
- **Direction-aware.** Only *worse* moves flag; a candidate that is faster,
  cheaper, or more valid never raises a regression.
- **Pair completeness.** A case missing from either run is reported as
  `unpaired`, not silently scored — an absent candidate is not a "success."
- **Clustering.** Correlated failures are grouped so one root cause reads as one
  finding, not N.

## Output

Flags are attached to each `ComparisonRecord` and aggregated into a
`ReportSummary` (`flags_by_category`, per-suite counts, clusters). The report
generator (Milestone 7) renders the headline, per-category breakdown, worst
regressions, and representative examples. The scripted-regression adapter injects
known defects so the detector's recall can be asserted in tests with exact
expected flags.
