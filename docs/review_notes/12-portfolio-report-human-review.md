Human review: Milestone 12 — Final portfolio report

TL;DR

The README, `docs/RESULTS.md` and the thirteen-page website were brought to the
standard the code already meets, and then *held* there. The deliverable that
matters is not the prose — it is `tests/test_docs.py` (64 tests), because a
document is the one artifact in this repository where a number can be wrong and
nothing fails. It recomputes the detection counts, every flag and cluster id, the
smoke verdict, the baseline digest and the suite's own test total from the
fixtures, and fails when a document quotes a stale one. Writing those tests found
four stale test counts across five files (one page still claimed `441 passed`),
a cluster table that said "16 clusters" and listed 8, a report-test count off by
one, a website section still describing Milestone 10 as future work, and an
"excerpt verbatim from the report" that had quietly reformatted the report's
tables. 913 tests pass, all offline.

What changed

- **`README.md` — rewritten.** It now opens on the problem, then shows a *real*
  trace: `tu_wrong_tool`'s two `RunRecord`s, whose `final_output` is
  **byte-identical** while a tool call silently disappeared. A string diff sees
  nothing; the engine raises `tool_call_regression`, critical. Its mirror,
  `sj_control_reorder`, changes the string and regresses nothing. The full
  `ComparisonRecord` is printed, the smoke verdict is quoted, reproduction is one
  block of commands, and "what is not claimed" is six explicit bullets. The
  research/product path closes it.
- **`docs/RESULTS.md` — restructured and completed.** New: a per-dimension table
  for every one of the 18 injected defects (structured output, tool calls,
  evidence, refusal, latency/cost, errors) carrying the triggering metric, the
  observed value, the threshold and the delta; a table of all 8 negative controls;
  the complete 16-cluster listing; the imported-trace result (2/2 detected, 0 false
  positives); a per-file test table. Removed: the "what will be reported here next"
  placeholder. The redaction table lost a self-contradictory row (`73 in
  tests/test_redaction.py (74 new; 651 total)`).
- **New `tests/test_docs.py`** — 64 tests. See below.
- **Website.** `results.html` rewritten to mirror `RESULTS.md`; `index.html` gains
  the Milestone 12 entry and the smoke verdict; `milestones.html` marks 12 complete
  and 13 next; `example-comparison.html`'s report excerpt is now genuinely verbatim;
  `roadmap.html` gains a product-path section and drops "the current scaffold";
  `architecture.html`, `cost-guard.html`, `integration-contracts.html`,
  `safety.html` had stale test counts corrected.
- **`docs/SAFETY.md`** — test count corrected.

How the doc tests work

Two mechanisms, and neither one matches English.

1. **Structured claims are parsed, not matched.** The `ComparisonRecord` the README
   prints is extracted from its fenced `json` block, `json.loads`-ed, and compared
   field by field — `explanation` string included — against the record the engine
   writes during the test. A changed threshold or a reworded rule breaks the README
   rather than being silently contradicted by it. The website's report excerpt is
   checked line by line, in order, against `docs/examples/regression_report.md`.
2. **Counts are derived, then required to appear.** 18 defects, 8 controls, 19
   flags, 16 clusters, the smoke verdict, `sha256(run.jsonl)`, and the suite's test
   total are all computed in-process (the total by asking pytest to collect, in a
   subprocess) and asserted present in the documents that quote them. Nothing
   asserts a number that is not first measured.

Prose is compared after stripping tags, emphasis, table pipes and line wrapping,
because matching raw bytes fails on a reflow and passes on a lie.

Both halves of that sentence were checked adversarially during development rather
than asserted:

- Rewriting the README's `18 of 18 injected defects` to `17 of 18` fails
  `test_the_docs_cite_the_detection_counts_the_oracle_asserts`.
- Reflowing the same claim onto one line and changing `**bold**` to `*italic*`
  leaves all 64 tests green.

What should be checked later

- **Is `test_the_docs_cite_the_current_test_count` worth its coupling?** Adding any
  test anywhere now requires editing four documents. That is the point — a stale
  "849 tests passing" is exactly what this milestone was cleaning up, and there were
  five of them — but it is a real tax, and a reviewer may prefer the docs say
  "900+ tests" and drop the assertion. The failure message names the new total.
- **The count test shells out to `pytest --collect-only`.** It is offline and
  collection-only (nothing executes, so it cannot recurse), but it is the only
  subprocess in the suite and it adds ~0.3 s. It also depends on `addopts = "-q"`
  staying a single `-q`: a second one changes the summary line to a per-file
  listing, which the parser would not match. The assertion message is explicit if
  that happens.
- **`prose()` is a heuristic, and its first version was wrong.** `<[^>]+>` ate every
  word between `+20% < 25%` and the next `>` in the file, silently disabling two
  assertions. It is now `</?[a-zA-Z][^>\n]*>` — tag-shaped, and never across a
  newline. Worth a second look: it is the kind of helper whose bugs make tests pass.
- **The excerpt test allows elisions.** A line beginning `[...` is skipped, so the
  page could in principle omit a damning section and stay green. Ordering is
  enforced; completeness is not.
- **`REQUIRED_PAGES` lists the eleven pages `CLAUDE.md` names**, not the thirteen
  that exist. `cost-guard.html` and `integration-contracts.html` are covered by the
  nav-consistency and link-resolution tests instead. If `CLAUDE.md`'s list is meant
  to be authoritative and complete, it should gain those two.

Technical risks

- **Low.** No library code changed. The only new import in the package's test tree
  is `subprocess`/`sys` in `tests/test_docs.py`; the core package remains
  stdlib-only and dependency-free, and no scored path was touched. The smoke
  baseline digest is still `ee23bfde…` and the committed example report still
  regenerates byte-identically.
- **The doc tests are now a gate on documentation edits.** A well-meant reflow is
  safe; a reworded number is not. That is intended, and the failure messages say
  which document and which number.
- **The website is unversioned prose.** `tests/test_docs.py` pins its numbers, its
  links, its navigation and its self-containment. It cannot pin whether a sentence
  is honest. The claims-vs-evidence discipline still depends on a reader.

Commands to verify

```bash
python3 -m pytest -q                                  # 913 passed, offline
python3 -m pytest tests/test_docs.py -q                # the 64 doc tests alone
python3 scripts/01_validate_fixtures.py evals/fixtures # PASS (26 cases, 18/8)
python3 scripts/08_check_repo_hygiene.py               # PASS, 0 findings

# regenerate every number the docs cite, from scratch
python3 scripts/02_run_baseline.py     # sha256 ee23bfde…
python3 scripts/03_run_candidate.py    # sha256 b486ff9e…
python3 scripts/04_score_runs.py
python3 scripts/07_aggregate_runs.py
python3 scripts/05_compare_runs.py     # 6 flags, 4 critical, 2 warn, 6 clusters
python3 scripts/06_generate_report.py --format both
python3 scripts/09_import_external_trace.py            # 2/2 detected, 0 FP

# then break something on purpose and watch a doc fail
sed -i 's/18 of 18 injected/17 of 18 injected/' README.md
python3 -m pytest tests/test_docs.py -q                # fails; then git checkout README.md

./scripts/serve_site.sh                                # http://localhost:9001
```

Human feedback
<!-- Human will fill this later. -->
