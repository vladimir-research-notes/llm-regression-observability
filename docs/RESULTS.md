# Results

> **Status: pending implementation.** This file is populated once the run
> executor (Milestone 4), evaluators (Milestone 5), and comparison engine
> (Milestone 6) exist and the smoke suite has been run end to end.

## What will be reported here

After the smoke suite runs offline with a baseline (recorded adapter) and a
candidate (scripted-regression adapter), this document will summarize:

- **Headline** — number of cases, number regressed, flags by category.
- **Detection of injected regressions** — the scripted adapter injects known
  defects; this table will show detected vs injected per category (the
  detector's recall on ground truth).
- **Structured-output validity** — baseline vs candidate validity rate.
- **Tool-call drift** — allowed-tool ratio, sequence similarity, missing/extra
  tools.
- **Evidence grounding** — coverage, citation correctness, unsupported claims.
- **Latency / cost** — p50/p95 latency deltas and total cost delta.
- **Refusal behavior** — expected vs observed refusal shifts.
- **Application vs. harness noise** — how many degradations were attributed to
  infrastructure and excluded from flags.

## Reproduction

The exact commands that generate these numbers are in
[`REPRODUCIBILITY.md`](REPRODUCIBILITY.md). All numbers are produced offline,
CPU-only, and are byte-reproducible on the same fixtures.

_No results are reported yet; nothing below this line is claimed until the smoke
run exists._
