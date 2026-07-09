Human review: Milestone 7 — Reports

TL;DR
Added `src/llm_regression_observability/reports/`: four modules that read a
comparison directory — and nothing else — and render it as Markdown and as one
self-contained HTML file. Sections: verdict and provenance, per-category changes,
worst regressions, a worked example each for structured output / tool-call diff /
evidence grounding, structured-output validity, latency and cost tables (including
sub-threshold movers), failure clusters, **what the engine declined to say**,
thresholds applied, limitations. Redaction is a **fail-closed gate**: the fully
rendered document is scanned immediately before it is written and a credential-like
string that survived is refused, not emitted; Milestone 9's masking redactor plugs
into the `Redactor` seam `build_report()` already takes. `llmreg report` (with
`--format`, `--top`, `--dry-run`) and `scripts/06_generate_report.py` drive it. Full
suite is 507 passed (66 new). Ruff clean apart from one pre-existing Milestone 0
line. Offline, dependency-free, deterministic, no generation timestamp. Nothing
pushed.

What changed
- `reports/base.py` — `ReportError`; the `Redactor` seam and `identity_redactor`;
  `secret_findings()` and `assert_redaction_clean()`; `CATEGORY_ORDER`,
  `severity_rank()`, `flag_sort_key()`; the number formatters (`fmt_value`,
  `fmt_delta`, `fmt_ms`, `fmt_ms_delta`, `fmt_usd`, `fmt_usd_delta`, `one_line`).
- `reports/model.py` — `load_comparison()` (schema-validated), `build_report()` →
  `ReportModel`, `load_report_model()`; `FlagRow` / `CategoryRow` / `MoveRow` /
  `Example` / `LatencySection` / `CostSection`; `EXAMPLE_DIMENSIONS`; `LIMITATIONS`;
  `_check_consistent()` (cross-artifact disagreement is an error).
- `reports/markdown.py` — `render_markdown()`, `cell()` (pipe/newline escaping).
- `reports/html.py` — `render_html()`, inlined `STYLE`, everything `html.escape`d.
- `reports/__init__.py` — `render()`, `report_path()`, `write_report()`
  (render → gate → write), `generate_report()`, `ReportResult`, `FORMATS`.
- `cli.py` — `llmreg report --comparison [--out] [--format markdown|html|both]
  [--top N] [--dry-run]`; `REPORT_FORMAT_CHOICES`, `DEFAULT_REPORT_OUT`,
  `DEFAULT_REPORT_TOP` mirrored so `llmreg version` still does not import the
  report stack.
- `scripts/06_generate_report.py`; `docs/examples/regression_report.md` (committed
  sample, pinned by a test).
- `tests/test_reports.py` — 66 CPU-only tests.
- Docs/site: milestone doc → Complete; `RESULTS.md` and `REPRODUCIBILITY.md` gained
  the report step; `SAFETY.md` and `site/safety.html` rewritten to separate the M7
  gate from the M9 mask; `LIMITATIONS.md` / `site/limitations.html` gained two
  entries; `README.md` end-to-end block updated; `site/` index, architecture,
  example-comparison, results and milestones pages updated.

Answers to the questions Milestone 6 left open
- **"`ComparisonMeta` is a new artifact not named in `PLAN.md` §5 … Milestone 7
  should read it. Confirm the split."** It reads it, and the report would be
  materially worse without it. Everything in "What the engine declined to say" —
  unpaired cases, infra-attributed cases, withheld flags, comparability notes — and
  the thresholds table and the records digest come from the sidecar. `summary.json`
  can say what regressed; only `comparison_meta.json` can say what the engine could
  not look at. **Confirm the split stands.**
- **"Reports must not conflate flags and regressed cases."** They do not. The
  headline reads "N regression flag(s) across M of K paired cases", and the
  per-category table carries separate `Flags` and `Cases` columns.
  `test_a_regressed_comparison_leads_with_the_verdict_and_the_counts` pins it.
- **"One flag per (case, category) loses the runners-up as structured data … If
  Milestone 7's report wants to render 'wrong tool *and* missing tool *and* sequence
  drift' as separate rows, the schema needs a field."** No schema change was made.
  The report renders the collapsed flag's `explanation`, which names the runners-up
  after `also:`, as one row. Recorded in `LIMITATIONS.md`.

What should be checked later
- **The redaction gate refuses; it does not mask.** Today a comparison artifact
  carrying a secret produces *no report at all*. That is the right default for a
  milestone that does not own a masking redactor, but it means Milestone 9 changes
  the behavior of an existing, tested path. **Decide whether M9 should mask silently,
  mask and set a `flagged` marker in the report body, or keep refusing on some
  patterns (e.g. a private-key header) regardless.**
- **`SECRET_PATTERNS` is imported from `datasets/validation.py`.** That is where
  Milestone 2 put it, and duplicating it would be worse. Milestone 9's spec moves the
  table to `traces/redaction.py`. **When it does, both callers must follow — a
  second, drifting copy of "credential-like" is the failure mode.**
- **Free text is redacted; identifiers are not.** `build_report()` passes flag
  explanations, per-case summaries, cluster labels and comparability notes through
  the redactor, but not case ids, run ids, metric names, or `suite_path`. The gate
  covers the whole document either way (`test_the_gate_catches_a_secret_the_redactor_
  seam_never_sees` puts a key in a `run_id`), so nothing leaks — but after M9 a
  secret in a run id will *refuse* while a secret in an explanation will *mask*.
  **Confirm that asymmetry is intended.**
- **The per-category table prints all eight categories, and a `0` row is ambiguous.**
  It cannot distinguish "evaluated and clean" from "no case in this suite declared
  this dimension"; the artifacts do not record which dimensions were in scope. The
  table says so in prose. **Decide whether `ComparisonMeta` should record the
  dimensions each case declared, so the report can print `n/a` instead of `0`.**
- **The structured-output table shows a delta, not the two absolute values.**
  `ComparisonRecord.json_validity_delta = -1` rather than `1.0 → 0.0`, because that is
  all the artifact holds. Same for the tool and evidence dimensions. Fixing it means
  either widening `ComparisonRecord` or letting the report read the runs — and the
  latter would cost the property that a report is reproducible from a comparison
  directory alone. **Confirm the trade.**
- **`ReportModel` and `reports/base.py` are two modules beyond the spec's
  `markdown.py` / `html.py`.** The reason: both renderers must pick the same rows and
  print the same numbers, and giving each its own copy of that logic is how two
  formats of one report end up disagreeing. **Confirm, or fold `base.py` into
  `model.py`.**
- **`fmt_value` prints six decimals, matching `comparison.base.DELTA_DECIMALS`.** It
  is a duplicated constant in spirit (the formatter hard-codes `.6f`). If the engine's
  rounding ever changes, the report silently disagrees with `comparison.jsonl` again.
  **Consider importing `DELTA_DECIMALS` rather than restating `6`.**
- **The "worst regressions" table defaults to 10 rows.** The smoke suite raises 6, so
  nothing is hidden there — but the six fixture suites together carry 19 flags
  (`RESULTS.md`), and a suite manifest covering them would hide 9 behind a
  "Showing 10 of 19" line. The number is arbitrary. **Confirm 10, or make it a suite
  threshold.**
- **`scripts/06_generate_report.py`, not `05_generate_report.py`.** The `05` slot
  went to the Milestone 6 comparison script, which had itself been pushed off `04` by
  the Milestone 5 scorer. The milestone spec's Deliverables list was updated and the
  deviation recorded there and in `REPRODUCIBILITY.md`.
- **`docs/examples/regression_report.md` is committed and pinned by a test** that
  rebuilds the smoke comparison from the fixtures and compares byte for byte. It is
  the reason a sample report in the docs cannot rot; the price is that any change to
  a flag explanation, a section heading, or a number format now fails a test until the
  file is regenerated. **Confirm the file belongs in `docs/` rather than being
  generated on demand.**

Technical risks
- Low. Pure stdlib (`dataclasses`, `pathlib`, `math`, `html`, `collections.abc`); no
  network, clock, RNG, paid API, or secrets. An AST scan
  (`test_no_ambient_time_randomness_or_network_in_the_report_path`) fails if any
  module under `reports/` imports `time`, `datetime`, `random`, `secrets`, `socket`,
  `urllib`, `http`, or `requests`.
- The report carries **no generation timestamp**, so two renderings of one comparison
  are byte-identical — verified in-process
  (`test_writing_twice_produces_byte_identical_files`) and across processes via
  `python3 scripts/06_generate_report.py --out results/processed/_check/report.md`
  plus `diff`.
- `reports/html.py` is a module named `html` that imports the standard library's
  `html` for `escape`. Absolute imports make this unambiguous in Python 3, and the
  tests exercise it, but it is the kind of shadowing worth knowing about.
- The HTML is self-contained: `test_html_is_self_contained_and_requests_nothing`
  asserts no `<link>`, `<script>`, `<img>`, `url(`, `http://` or `https://` appears.
  `test_html_is_well_formed` parses it with `html.parser` and checks tag nesting.
- Every string a renderer emits is escaped (`\|` in Markdown, `html.escape` in HTML)
  even though comparison artifacts hold only rule-authored prose. The tests inject
  `a | b` and `<script>x</script>` into a flag explanation to prove it.
- Generated `results/reports/` contents stay gitignored. The one committed report is
  `docs/examples/regression_report.md`, which contains only synthetic case ids,
  metric names and thresholds.
- One pre-existing `ruff` E501 remains in `scripts/00_check_environment.py`
  (Milestone 0). Left alone: out of this milestone's scope.

Commands to verify
    python3 -m pytest -q                                    # 507 passed
    python3 -m pytest -q tests/test_reports.py              # 66 passed
    python3 scripts/05_compare_runs.py                      # -> results/comparisons/...
    python3 scripts/06_generate_report.py                   # -> results/reports/*.md,*.html
    python3 scripts/06_generate_report.py --dry-run         # renders + gates, writes nothing
    python3 scripts/06_generate_report.py --top 1           # "Showing 1 of 6 flags"
    python3 scripts/06_generate_report.py --out results/processed/_check/report.md
    diff results/reports/regression_report.md   results/processed/_check/report.md
    diff results/reports/regression_report.html results/processed/_check/report.html
    ruff check src tests scripts/06_generate_report.py      # clean (dev extra)

    # the report a human actually reads:
    cat docs/examples/regression_report.md
    ./scripts/serve_site.sh   # then open example-comparison.html

Human feedback
<!-- Human will fill this later. -->
