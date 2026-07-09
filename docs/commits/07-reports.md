# Milestone 7 — Reports

## What changed
The comparison artifacts now render into something a human can act on in seconds:
a Markdown document and one self-contained HTML file. This is the product's
surface, and the last stage of the `cases → traces → comparison → report`
pipeline `docs/PLAN.md` §4 describes.

- **`src/llm_regression_observability/reports/`** — new package, four modules:
  - `base.py` — `ReportError`; the `Redactor` seam and `identity_redactor`;
    `secret_findings()` / `assert_redaction_clean()` (the fail-closed gate);
    taxonomy ordering (`CATEGORY_ORDER`, `severity_rank`, `flag_sort_key`); and the
    number formatters both renderers share.
  - `model.py` — `load_comparison()` (the three artifacts, schema-validated),
    `build_report()` → `ReportModel`, `load_report_model()`. Every selection
    decision lives here: verdict, per-category rows, worst regressions,
    representative examples, latency/cost movers, structured-output rows,
    disclosures, `LIMITATIONS`.
  - `markdown.py` — `render_markdown()`.
  - `html.py` — `render_html()`, one self-contained file with inlined CSS.
  - `__init__.py` — `render()`, `report_path()`, `write_report()` (render → gate →
    write), `generate_report()`, `ReportResult`.
- **`cli.py`** — `llmreg report --comparison [--out] [--format markdown|html|both]
  [--top N] [--dry-run]`.
- **`scripts/06_generate_report.py`** — the smoke report, no editable install
  required. (`05` was taken by the Milestone 6 comparison script, which had itself
  been pushed off `04` by the Milestone 5 scorer.)
- **`docs/examples/regression_report.md`** — a committed sample, regenerated and
  compared byte for byte by a test.
- **`tests/test_reports.py`** — 66 CPU-only tests.
- Docs/site: milestone doc → Complete; `RESULTS.md` and `REPRODUCIBILITY.md` gained
  the report step and its guarantees; `SAFETY.md` rewritten to separate the gate
  (M7) from the mask (M9); `LIMITATIONS.md` gained two entries; `README.md`'s
  end-to-end block no longer says "not yet built"; `site/` index, architecture,
  example-comparison, results, safety, limitations and milestones pages updated.

## Why / key decisions
- **The report reads the artifacts and nothing else.** `comparison.jsonl`,
  `summary.json`, `comparison_meta.json` — not the runs, not the fixtures, not the
  engine. A report is therefore reproducible from a directory a colleague hands you,
  and it cannot quietly enrich itself from a source the reader does not have. The
  cost is visible in the output: the structured-output table shows
  `json_validity_delta = -1` ("was valid, is not") rather than `1.0 → 0.0`, because
  `ComparisonRecord` records the delta and the flag, not the two absolute values.
  Recorded in `LIMITATIONS.md`.
- **Selection happens once.** `ReportModel` decides which regressions are worst,
  which case represents the tool-call dimension, and whether a cost row may be shown
  at all. The renderers lay out tables; they do not choose rows. Two formats of one
  report cannot disagree, and the ordering is testable without rendering anything.
- **Redaction is a gate now and a mask later.** Milestone 9 owns
  `traces/redaction.py`. Milestone 7 owns the seam it plugs into (`Redactor`, applied
  to every free-text field `build_report()` copies) and the enforcement point:
  `assert_redaction_clean()` scans the *fully rendered document* immediately before
  the bytes hit the disk and **refuses** rather than emits. The default redactor
  masks nothing, which is exactly why the gate is fail-closed rather than advisory.
  `--dry-run` runs the same gate, so it cannot pass where a real write would refuse.
  Tested by splicing a fake key into a copy of the smoke comparison — and separately
  by putting one in a `run_id`, a field the seam deliberately does not touch, to
  prove the gate is independent of the seam.
- **The gate never echoes what it found.** It reports the pattern name and the line
  number. An error message, a CI log, and a stack trace are all places a secret
  leaks.
- **The silences get their own section.** "What the engine declined to say" prints
  unpaired cases, infra-attributed cases, withheld flags, comparability notes, and
  the thresholds actually applied. A comparison that paired 3 of 8 cases and
  suppressed 2 more has a legitimate headline of "1 regression" and is nearly
  worthless; `summary.json` alone cannot show that. This is what Milestone 6 added
  `comparison_meta.json` for, and it answers that milestone's "Milestone 7 should
  read it. Confirm the split."
- **Sub-threshold movement is a row, not a silence.** The latency and cost tables
  list every case that moved, flagged or not, labelling the ones that did not cross
  both floors. Otherwise a run with a working tolerance is indistinguishable from a
  run nobody measured.
- **Flags are not regressed cases.** The headline reads "N flags across M of K paired
  cases" and the per-category table has separate `Flags` and `Cases` columns, because
  one case can fail in two ways (`at_failed_tool` does). Milestone 6's review note
  asked for exactly this.
- **Every taxonomy category gets a row, silent ones included.** A `0` row means no
  flag fired; it cannot distinguish "evaluated and clean" from "no case declared this
  dimension", and the artifacts cannot either. The row is printed anyway (a missing
  row reads as an oversight) and the limitations note states the ambiguity.
- **No timestamp.** A generation timestamp would make two reports of one comparison
  differ byte for byte. Provenance comes from `records_sha256` and the `llmreg`
  version stamped into `comparison_meta.json` at comparison time, both printed in the
  header table. An AST scan fails if any module under `reports/` imports `time`,
  `datetime`, `random`, `secrets`, `socket`, `urllib`, `http`, or `requests`.
- **The HTML requests nothing.** No `<link>`, `<script>`, `<img>`, web font, or
  `url()`. A report gets attached to a pull request or opened from a CI artifact
  bucket; a stylesheet request there either fails or succeeds and tells a third party
  who is reading it. Everything is escaped on the way in — "the input is trusted" is
  the sentence that precedes an injection bug.
- **Cross-artifact disagreement is an error.** `summary.json` and
  `comparison_meta.json` restate several counts. If they disagree, one is stale, and
  a report that averages over the disagreement is worse than no report.
  `build_report()` checks comparison id, case count, flag count and regressed-case
  count, and raises.
- **A metric value is never truncated below the engine's precision.** `%g` renders an
  `observed` of `1.666667` as `1.66667`, silently disagreeing with the artifact the
  reader is about to open. A zero delta prints unsigned: `+$0.000000` reads like a
  rounded-down increase, which is precisely the ambiguity a cost report must not have.

## Validation run
- `python3 -m pytest -q` → **507 passed** (6 scaffold + 8 smoke + 54 schema +
  25 fixtures + 46 adapters + 51 executor + 155 evaluators + 96 comparison +
  66 reports).
- `python3 -m pytest -q tests/test_reports.py` → 66 passed.
- `ruff check src tests scripts` → one pre-existing E501 in
  `scripts/00_check_environment.py` (Milestone 0); nothing new.
- `python3 scripts/06_generate_report.py` → verdict `REGRESSED`, 6 flags over 6 of 8
  paired cases (4 critical, 2 warn), 6 clusters; `lc_latency_spike` at `+250.0 ms` in
  the latency table; `at_infra_noise` in the disclosures as surfaced-and-excluded and
  nowhere in the worst-regressions table; `cost_regression` and
  `final_answer_regression` present as `0` rows.
- `--dry-run` renders and gates without writing; `--top 1` prints
  "Showing 1 of 6 flags; 5 more are in `comparison.jsonl`"; `--top 0` shows all.
- Cross-process determinism: rendering into a second output root in a fresh process
  produces byte-identical `regression_report.md` and `regression_report.html`.
- Redaction: a fake `sk-…` key spliced into a flag explanation makes both
  `llmreg report` and `--dry-run` refuse, with the pattern name and line number in
  the message and the key itself absent from it; nothing is written. A masking
  redactor passed into the seam lets the same report through clean.
- The committed `docs/examples/regression_report.md` is regenerated from the fixtures
  by `test_the_committed_example_report_is_not_stale` and compared byte for byte.

Next: Milestone 8 — observability (spans + aggregate metrics).
