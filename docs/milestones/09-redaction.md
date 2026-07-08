# Milestone 9 — Redaction and safety hygiene

**Status: planned.**

## Goal

Ensure no secret-like or PII-like string is ever written to a trace or report.
See [`../SAFETY.md`](../SAFETY.md).

## Deliverables

In `src/llm_regression_observability/traces/redaction.py`, a redactor that masks:

- fake API keys (provider-style prefixes, e.g. `sk-...`);
- generic secrets / high-entropy tokens;
- bearer tokens and `Authorization` headers;
- email addresses;
- other obvious credential-like strings.

The redactor is wired so it runs **before** persistence and report generation,
and it sets `redaction_status` (`clean` | `redacted` | `flagged`) on each
`RunRecord`. A repository-hygiene helper scans the tree for accidentally
committed secrets.

## Validation

```bash
python3 -m pytest -q tests/test_redaction.py
```

Tests assert every known secret pattern is masked, that redaction runs prior to
report writing, that non-secret text is left untouched, and that the report
generator refuses to emit unredacted content.

## Rationale

"Redaction works before reports are generated" is an explicit quality gate.
Proving it with tests is the point of a dedicated milestone.

## Definition of done

Redaction tests pass; report path is redaction-gated; docs/website updated. Next:
Milestone 10 — integration contracts.
