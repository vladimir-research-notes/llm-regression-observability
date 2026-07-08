# Milestone 7 — Reports

**Status: planned.**

## Goal

Turn comparison records into a clear, actionable Markdown (and optional HTML)
report.

## Deliverables

In `src/llm_regression_observability/reports/`:

- `markdown.py` — renders: headline regression summary; per-category changes;
  worst regressions; representative examples (structured-output, tool-call diff,
  evidence grounding); latency/cost tables; structured-output validity table; and
  a limitations note.
- `html.py` — optional self-contained HTML rendering of the same content.
- CLI `llmreg report` + `scripts/05_generate_report.py`.

Reports are written to `results/reports/`. All text passes through redaction
(Milestone 9) before being written.

## Validation

```bash
python3 -m pytest -q tests/test_reports.py
```

Tests assert the report contains the expected sections and the detected
regressions, renders deterministically from a fixed `ComparisonRecord` set, and
contains no unredacted secret-like strings.

## Rationale

A regression tool is only useful if a human can act on its output in seconds.
The report is the product's surface.

## Definition of done

Report generates from comparison records with all sections; deterministic;
redaction-clean; docs/website updated. Next: Milestone 8 — observability.
