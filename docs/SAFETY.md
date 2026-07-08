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

## Redaction (Milestone 9)

Before any trace or report is persisted, text passes through a redactor
(`traces/redaction.py`) that masks:

- fake API keys (e.g. `sk-...`, provider-style prefixes);
- generic secrets / high-entropy tokens;
- bearer tokens and `Authorization` headers;
- email addresses;
- other obvious credential-like strings.

Redaction is covered by tests that assert known secret patterns are masked, and
it runs **before** report generation. `redaction_status` on each `RunRecord`
records whether a record was `clean`, `redacted`, or `flagged`.

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
