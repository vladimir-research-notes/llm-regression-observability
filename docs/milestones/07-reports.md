# Milestone 7 — Reports

**Status: Complete.** `llmreg report` renders a comparison into Markdown and one
self-contained HTML file, deterministically and redaction-gated. `507 tests pass`
(66 new). A committed sample lives at
[`../examples/regression_report.md`](../examples/regression_report.md) and a test
regenerates it, so it cannot go stale.

## Goal

Turn comparison records into a clear, actionable Markdown (and optional HTML)
report.

## Deliverables

In `src/llm_regression_observability/reports/`:

- `base.py` — the redaction seam (`Redactor`) and the fail-closed redaction gate
  (`assert_redaction_clean`), taxonomy ordering, and number formatting shared by
  both renderers.
- `model.py` — the three comparison artifacts in, one `ReportModel` out: verdict,
  per-category rows, worst regressions, representative examples, latency/cost
  sections, structured-output rows, clusters, disclosures, thresholds, limitations.
  Every selection decision lives here, so the two renderers cannot disagree.
- `markdown.py` — headline; per-category changes; worst regressions; representative
  examples (structured output, tool-call diff, evidence grounding); structured-output
  validity table; latency and cost tables; failure clusters; a "what the engine
  declined to say" section; thresholds; a limitations note.
- `html.py` — self-contained HTML rendering of the same content (no `<link>`, no
  `<script>`, no web font, no `url()` — the document requests nothing).
- CLI `llmreg report` + `scripts/06_generate_report.py`.

Reports are written to `results/reports/`. All text passes through the redaction
seam, and the fully rendered document passes the redaction gate, before being
written (Milestone 9 supplies the masking redactor that fits the seam).

## Validation

```bash
python3 -m pytest -q tests/test_reports.py     # 66 passed
python3 scripts/06_generate_report.py
```

Tests assert the report contains the expected sections and the detected
regressions, renders deterministically from a fixed `ComparisonRecord` set, and
contains no unredacted secret-like strings.

## Rationale

A regression tool is only useful if a human can act on its output in seconds. The
report is the product's surface.

## Design decisions worth knowing

**The report can only say what the artifacts say.** It reads
`comparison.jsonl`, `summary.json` and `comparison_meta.json` — never the runs, the
fixtures, or the engine. So it is reproducible from a directory a colleague hands
you, and it cannot quietly enrich itself from a source the reader does not have.
The visible cost: the structured-output table reports the *delta*
(`json_validity_delta = -1`), because `ComparisonRecord` records the delta and the
flag, not the two absolute validity values.

**Redaction today is a gate, not a mask.** Milestone 9 owns the masking redactor.
Milestone 7 owns the seam it plugs into and the enforcement point: the fully
rendered document is scanned immediately before the bytes hit the disk, and a
credential-like string that survived is **refused**, not written. The default
redactor masks nothing, which is exactly why the gate is fail-closed. The gate
never echoes what it found — it names the pattern and the line.

**The silences get their own section.** "What the engine declined to say" prints
unpaired cases, infra-attributed cases, withheld flags, comparability notes, and
the thresholds actually applied. A clean headline over a comparison that paired 3
of 8 cases is the failure mode this project exists to avoid, and it is invisible in
`summary.json` alone. This is what `comparison_meta.json` was added for in
Milestone 6.

**Sub-threshold movement is a row, not a silence.** The latency and cost tables
list every case that moved, flagged or not, labelling the ones that did not cross
both floors. A report that hides them makes a run with a working tolerance
indistinguishable from a run nobody measured.

**Flags are not regressed cases.** The headline says "N flags across M of K paired
cases", and the per-category table has separate `Flags` and `Cases` columns —
because one case can fail in two ways (`at_failed_tool` does).

**Every taxonomy category gets a row, including the silent ones.** A `0` row means
no flag fired. It does *not* distinguish "evaluated and clean" from "no case in
this suite declared this dimension" — the artifacts cannot tell those apart, and
the limitations note says so rather than letting the table imply coverage.

**No timestamp.** A generation timestamp would make two reports of one comparison
differ byte for byte. Provenance comes from the records digest and the `llmreg`
version recorded at comparison time.

## Deviations from this spec

- The CLI script is `scripts/06_generate_report.py`, not `05_generate_report.py`:
  the `05` slot is already `scripts/05_compare_runs.py` from Milestone 6. Recorded
  in `REPRODUCIBILITY.md`. (Milestone 6 took the same shift for the same reason.)
- Two modules beyond the spec's `markdown.py` / `html.py`: `base.py` (the redaction
  gate and formatting) and `model.py` (selection). Both renderers must pick the same
  rows and print the same numbers; giving each its own copy of that logic is how
  two formats of one report end up disagreeing. No Milestone 1 schema was changed.
- A committed sample report, `docs/examples/regression_report.md`, with
  `tests/test_reports.py::test_the_committed_example_report_is_not_stale`
  regenerating it from the fixtures and comparing byte for byte.

## Definition of done

Report generates from comparison records with all sections; deterministic;
redaction-clean; docs/website updated. Next: Milestone 8 — observability.
