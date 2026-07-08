Read `CLAUDE.md` and `docs/PLAN.md`. Inspect `git status`, `git log --oneline -8`,
`docs/milestones/`, `docs/review_notes/`, and `docs/commits/`. Determine the next
incomplete milestone unless a specific milestone number is provided.

Implement **exactly one** milestone, following its spec in
`docs/milestones/NN-*.md`. Do not implement any later milestone.

Rules (from `CLAUDE.md`):

- Core package stays dependency-free (Python 3.12 stdlib only). `pydantic` /
  `pandas` / `ruff` are optional and must never be required for tests.
- Everything deterministic: no ambient `time`/`random`/`datetime.now()` in scored
  paths. Same inputs → byte-identical outputs (modulo an explicit run id).
- All fixtures synthetic and safe. No secrets, no real data, no large artifacts,
  no `.claude/settings*.json` committed.
- No network and no paid API in any test.

Then:

1. Run the milestone's validation commands (at minimum `python3 -m pytest -q`).
2. Update the multi-page website under `site/` (status badge + the relevant
   page). Keep `site/milestones.html` in sync.
3. Update `docs/RESULTS.md` or the relevant topic/milestone docs if results
   changed. Flip the milestone doc's status to **Complete** with a one-line note.
4. Create/update `docs/review_notes/NN-<name>-human-review.md` (do not claim
   review happened).
5. Create/update `docs/commits/NN-<name>.md` (what changed and why).
6. Commit locally. **Do not push.**
7. Stop after exactly this one milestone and print: milestone title, commit hash,
   tests run, website pages updated, review placeholder path, and the next
   milestone.

If continuing would risk secrets, paid API spend, data loss, or repository
corruption, stop and explain instead.
