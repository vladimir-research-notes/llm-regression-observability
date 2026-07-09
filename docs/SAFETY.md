# Safety and privacy

This project is a **reliability/evaluation framework**. It is not a medical,
legal, or security authority, and it makes no safety certification of any model
or application.

## Data scope

- **All fixtures are synthetic.** Prompts, model outputs, tool traces, and
  retrieval documents are authored for this repository. There is **no real user
  data** anywhere in the repo.
- Fixtures are **small and safe**. Any biomedical- or security-adjacent example
  is kept non-clinical, non-actionable, and non-exploitative — it exists only to
  exercise the refusal/boundary and evidence scorers, never to provide real
  guidance.
- The refusal/boundary suite uses prompts where the *expected* behavior is stable
  and benign; it tests whether a change shifts refusal behavior, not whether the
  model should comply with anything harmful.

## Redaction: the gate (Milestone 7) and the mask (Milestone 9)

Two mechanisms, built in that order on purpose.

**The gate exists now.** `reports/base.py::assert_redaction_clean` scans the fully
rendered report immediately before the bytes hit the disk and is **fail-closed**: a
credential-like string that reached the document means the report is *refused*, not
written. The error names the pattern and the line and never echoes the value —
an error message is a place secrets leak too. `llmreg report --dry-run` runs the
same gate, so a dry run cannot pass where a real write would refuse.

The patterns it recognizes today (`datasets/validation.py::SECRET_PATTERNS`, also
used by `llmreg validate-fixtures`) are provider-style API keys (`sk-…`, `AKIA…`,
`gh[pousr]_…`, `AIza…`, `xox[baprs]-…`), bearer tokens, and private-key headers.

**The mask arrives with Milestone 9.** `traces/redaction.py` will supply a redactor
that *masks* rather than refuses — covering the patterns above plus generic
high-entropy tokens, `Authorization` headers, and email addresses — and will set
`redaction_status` (`clean` | `redacted` | `flagged`) on each `RunRecord` before
persistence. It plugs into the `Redactor` seam that `reports.build_report()`
already takes; the gate stays exactly where it is, as the last assertion.

Milestone 7's default redactor masks nothing, which is precisely why the gate is
fail-closed rather than advisory.

Independently of both: a comparison artifact carries no model-generated prose to
begin with. Flag explanations, per-case summaries and cluster labels are authored
by the rules from metric names, numbers, tool names and property names — never by
copying a model's output. The report renders only those, so the gate is a backstop,
not the only line of defence.

## What is never committed

Secrets, API keys, real API responses, private data, `.env` files, large logs,
response caches, model weights, and `.claude/settings*.json` are all
`.gitignore`d. The repository-hygiene check (part of the environment preflight)
scans for these before commits in CI-style usage.

## Optional paid-API mode

No paid API is used by default or in any test. If an optional API adapter is
enabled (Milestone 11), it is gated by: dry-run mode, a token/cost estimator, a
hard max-cost guard, a response cache, and explicit opt-in. Real API responses
are never committed.

## Honest framing

Where a metric is approximate (token/cost estimation) or a placeholder
(explanation-quality scoring), the documentation and the code say so. This system
measures *relative* behavioral change on *synthetic* fixtures. It does not
validate correctness, safety, or fitness for any real-world use.
