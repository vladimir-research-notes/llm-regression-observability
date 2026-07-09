# Milestone 9 — Redaction and safety hygiene

**Status: Complete.** `traces/redaction.py` holds the one pattern table and the
masking redactor; it runs before the executor hashes a run, before the report
generator renders, and before the span writer writes. `redaction_status` is derived
per `RunRecord`, and `llmreg scan-secrets` / `scripts/08_check_repo_hygiene.py` scan
the tree. 73 tests.

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
python3 scripts/08_check_repo_hygiene.py
llmreg scan-secrets
```

Tests assert every known secret pattern is masked, that redaction runs prior to
report writing, that non-secret text is left untouched, and that the report
generator refuses to emit unredacted content.

## Decisions this milestone had to make

The spec named one module. Four things it left open were settled here.

**Refuse on evidence, mask on suspicion.** The rules split into two tiers.
*Certain* rules (`sk-…`, `AKIA…`, `gh[pousr]_…`, `AIza…`, `xox…`, a JWT, a private-key
header, `Bearer`, `Authorization`) cannot plausibly fire on prose, so they are what
the fail-closed gate **refuses** on and what decides `redaction_status`. *Heuristic*
rules (a credential-named assignment, an email address, a generic high-entropy token)
can fire on legitimate text, so they **mask but never refuse**. A false-positive
refusal is a denial of service on your own report; a false-positive mask costs one
mangled string, and the status records that it happened.

**Identity fields are scanned, never rewritten.** `run_id`, `case_id`, `model_id`, a
`tool_calls[].name`, a `retrieval_sources[].source_id`, an `errors[].kind` — these are
join keys. The comparison engine pairs by `case_id`; the tool evaluator diffs by tool
name. Masking one would destroy the artifact's meaning while claiming to protect it.
So a certain-tier match in an identity field yields `redaction_status="flagged"` and
the executor **refuses the run**. That is what `flagged` means, and it is why the
value can never appear in a file this project wrote — it is reachable only on a trace
*imported* from elsewhere (Milestone 10).

**The status is derived, and it only moves one way.** `clean < redacted < flagged`;
`redact_record()` takes the maximum of what this pass found and what the record
already carried. A record claiming `clean` over a live secret is overruled, because
the scan is the authority and that is the dangerous lie. A record claiming `redacted`
keeps it even though a second pass finds nothing left to mask — its text is full of
placeholders, and downgrading it would erase the one fact the field exists to record.
The monotone rule is what makes redaction idempotent in bytes *and* status.

**Redaction happens inside `execute_suite`, not `write_run`.** `run_meta.json`
publishes `records_sha256` over `run.jsonl`, so a redactor running at write time would
hash text that no longer exists. Running it before accounting also means
`output_tokens_estimate` describes the text actually persisted, rather than quietly
encoding the length of the secret.

## Where it runs

| Artifact | Mechanism | Why |
| --- | --- | --- |
| `run.jsonl` | **mask**, then derive status; refuse if `flagged` | content fields are model output |
| `spans.jsonl` | **gate** only | every string in a span is a join key |
| `*.md` / `*.html` report | **mask** free text, then **gate** the rendered document | explanations are prose; ids are keys |
| the source tree | **scan** (certain tier only) | a repository is scanned for evidence |

## Results

- 52/52 committed fixture records pass through redaction **unchanged** and report
  `clean`, so no scored text was rewritten.
- The smoke pipeline is byte-identical before and after this milestone;
  `docs/examples/regression_report.md` regenerates exactly.
- `llmreg scan-secrets` reports 0 findings on this repository.
- All three `redaction_status` values are reachable and tested; a vocabulary with a
  dead member would be a lie.
- 73 tests in `tests/test_redaction.py` (74 new overall; 651 pass).

## Rationale

"Redaction works before reports are generated" is an explicit quality gate.
Proving it with tests is the point of a dedicated milestone.

## Definition of done

Redaction tests pass; report path is redaction-gated; docs/website updated. Next:
Milestone 10 — integration contracts.
