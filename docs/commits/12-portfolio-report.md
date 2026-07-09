# Milestone 12 — Final portfolio report

**Commit:** `Milestone 12: portfolio report (the docs are now under test)`

## What changed

The system has run end to end since Milestone 7. What had not kept pace was the
prose describing it: five documents still claimed `849 tests`, one page still
claimed `441 passed`, `docs/RESULTS.md` announced 16 failure clusters and listed
8, and `site/results.html` closed with a section explaining what Milestone 10
*would* deliver — three milestones after it delivered it.

So this milestone did two things. It rewrote the top-level documents against the
artifacts a committed run produces. And it made that state enforceable.

### New

| File | Contents |
| --- | --- |
| `tests/test_docs.py` | 64 tests: the README, `docs/RESULTS.md` and all 13 site pages cite live numbers |
| `docs/review_notes/12-portfolio-report-human-review.md` | review placeholder |
| `docs/commits/12-portfolio-report.md` | this note |

### Rewritten

- **`README.md`** — problem, architecture, a real trace, the categories, the actual
  smoke result, reproduction, what is *not* claimed, and the research/product path.
- **`docs/RESULTS.md`** — restructured; gained per-dimension tables for all 18
  defects, a table of all 8 controls, the complete 16-cluster listing, the
  imported-trace result, and a per-file test table; lost its placeholder section.
- **`site/results.html`** — mirrors the new `RESULTS.md`.
- **`site/example-comparison.html`** — the report excerpt is now *actually* verbatim.

### Corrected

`site/index.html`, `site/milestones.html`, `site/roadmap.html`,
`site/architecture.html`, `site/cost-guard.html`,
`site/integration-contracts.html`, `site/safety.html`, `docs/SAFETY.md`,
`docs/milestones/12-portfolio-report.md`. Four stale test totals, one stale
report-test count (66 → 67), one stale milestone status, one obsolete
"what will be reported next".

## Why it is shaped this way

**A document is the only artifact here where a number can be wrong and nothing
fails.** Every other claim in this repository is guarded: `run_meta.json` carries
`records_sha256` and the writer re-hashes; `tests/test_evaluators.py` pins every
fixture score to an exact literal; `test_the_committed_example_report_is_not_stale`
regenerates the sample report and diffs it byte for byte. The README was the last
place where "18 of 18" was a sentence rather than a measurement. Writing prose
was therefore not the deliverable — `tests/test_docs.py` is. It found six real
defects that reading had not.

**Structured claims are parsed, not matched.** The `ComparisonRecord` the README
prints for `tu_wrong_tool` is extracted from its fenced `json` block, parsed, and
compared field by field against the record the engine writes during the test —
`explanation` string included. If a threshold moves or a rule is reworded, the
README fails rather than quietly describing a system that no longer exists. The
website's report excerpt is checked line by line, in order, against
`docs/examples/regression_report.md`. That check is what revealed the excerpt was
never verbatim: it had prettified the report's table columns, so a page claiming
"verbatim from" was showing text the code has never emitted.

**Prose is compared with the typography removed.** Tags, emphasis markers, table
pipes and line wrapping are stripped before matching, because matching raw bytes
fails on a reflow and passes on a lie — exactly backwards. The first version of
that helper used `<[^>]+>` to strip HTML, which in a Markdown file ate every word
between `+20% < 25%` and the next `>` character, silently disabling two
assertions. It is now tag-shaped and newline-bounded. A helper whose bugs make
tests pass is the one to distrust; both directions were then checked by hand —
rewriting `18 of 18` to `17 of 18` fails a test, reflowing the same claim onto one
line and swapping bold for italic does not.

**The suite's own test total is a claim too, so it is derived.** The count comes
from `pytest --collect-only` in a subprocess (collection only; nothing executes,
so it cannot recurse), and every document that quotes a total must quote that one.
Per-file counts like "155 tests assert exact expected scores" are deliberately not
matched — `TOTAL_CLAIM_PATTERNS` is anchored on the shapes a *suite* total takes.
This couples documentation to the test count, which is a real tax and is called
out in the review note. It is also the only thing that would have caught five
copies of `849`.

**The README leads with the case that carries the thesis.** `tu_wrong_tool`'s
`final_output` is byte-identical across baseline and candidate, and a tool call
disappeared. A string-diff test sees nothing; the engine raises
`tool_call_regression`, critical. Its mirror, `sj_control_reorder`, changes the
string and regresses nothing. Stating the thesis and then showing two traces that
demonstrate it in both directions is worth more than a paragraph asserting it.

## What did not change

No library code. No fixture. No scored path. `sha256(run.jsonl)` for the smoke
baseline is still `ee23bfde…`; the comparison still detects the same six clusters;
the committed example report still regenerates byte-identically. The core package
remains dependency-free and stdlib-only.

## Validation

```bash
python3 -m pytest -q                                  # 913 passed (64 new), offline
python3 scripts/01_validate_fixtures.py evals/fixtures # PASS — 26 cases, 18 defects, 8 controls
python3 scripts/08_check_repo_hygiene.py               # PASS — 0 findings
python3 scripts/02_run_baseline.py                     # sha256 ee23bfde… (unchanged)
python3 scripts/05_compare_runs.py                     # 6 flags (4 critical, 2 warn), 6 clusters
python3 scripts/06_generate_report.py --format both    # report renders, redaction clean
python3 scripts/09_import_external_trace.py            # 2/2 detected, 0 false positives
```

Website pages updated: `index.html`, `results.html`, `milestones.html`,
`example-comparison.html`, `roadmap.html`, `architecture.html`, `cost-guard.html`,
`integration-contracts.html`, `safety.html`.

Next: Milestone 13 — final cleanup (full gate run, hygiene check, clean tree).
