# Milestone 12 — Final portfolio report

**Status: Complete.** README, `docs/RESULTS.md` and all 13 website pages now cite
only numbers a committed run produces, and `tests/test_docs.py` (64 tests)
recomputes those numbers from the fixtures and fails when a document quotes a stale
one. 913 tests pass.

## Goal

Bring the README, docs, and website to portfolio quality now that the system
actually runs end to end.

## Deliverables

- README updated to show: problem statement; architecture; a real trace example;
  the regression categories; the **actual smoke-suite result** (numbers from a
  real run); how to reproduce; what is *not* claimed; and the future
  research/product path.
- `docs/RESULTS.md` populated with the real smoke-run numbers (headline,
  injected-vs-detected table, validity/tool/evidence/latency/cost/refusal
  tables).
- Website refreshed across all pages to match the implemented system and current
  results; the example-comparison page shows a real rendered report excerpt.

## Validation

```bash
python3 -m pytest -q
python3 scripts/01_validate_fixtures.py evals/fixtures
# regenerate the smoke result the README/RESULTS cite
```

Docs must cite only numbers produced by a committed, reproducible run; no
placeholder claims remain.

## Rationale

The artifact's value as a portfolio/reliability tool depends on the top-level
docs being accurate, concrete, and honest.

## Definition of done

README + RESULTS + website reflect the real, reproducible smoke result; no
overclaims; tests pass. Next: Milestone 13 — final cleanup.
