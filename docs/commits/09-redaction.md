# Milestone 9 — Redaction and safety hygiene

## What changed

- **`traces/redaction.py` (new)** — the one definition of "credential-like" in this
  repository, and everything built on it:
  - `RedactionRule` (name, tier, pattern, capture group, optional predicate, a `why`)
    and `RULES`, twelve rules across two tiers. `CERTAIN_RULES` and the
    back-compatible `SECRET_PATTERNS` view are derived from the table, never restated.
  - `redact_text()` / `redact_value()` — the masking redactor. Replaces a match with
    `[REDACTED:<rule>]`, a placeholder that matches no rule, so redaction is
    idempotent.
  - `scan_text()`, `secret_findings()`, `scan_for_secrets()`, `assert_clean()` — the
    fail-closed gate, sharing one refusal message that names the pattern and the line
    and never the value.
  - `redact_record()` → `RecordRedaction` — masks content fields, scans identity
    fields, derives `redaction_status`.
  - `shannon_entropy()` and the mixed-character-class filter behind
    `high_entropy_token`.
  - `scan_tree()`, `TreeFinding`, `ALLOW_MARKER` — repository hygiene.
- **`executor.py`** — `execute_suite` redacts each record after re-stamping and
  **before** `_account` and `_summarize`; a `flagged` record raises `ExecutorError`.
  `RunMeta` gains `redacted_case_count`.
- **`observability/spans.py`** — `write_spans` gates the rendered `spans.jsonl`.
- **`reports/base.py`** — imports the table rather than owning it; `secret_findings`
  and `Redactor` are re-exports; `assert_redaction_clean` delegates to `assert_clean`
  and keeps raising `ReportError`; `masking_redactor` added, `identity_redactor` kept.
- **`reports/model.py`, `reports/__init__.py`** — `build_report`, `load_report_model`
  and `generate_report` default to the masking redactor.
- **`datasets/validation.py`** — `SECRET_PATTERNS` and `scan_for_secrets` now come
  from `traces/redaction.py`. One table, four callers.
- **`cli.py`** — `llmreg scan-secrets [PATH]`; `run-suite` prints a redaction line;
  `aggregate` catches `RedactionError`.
- **`scripts/08_check_repo_hygiene.py` (new)** — the tree scan, `--json` supported,
  exit 1 on any finding.
- **`tests/test_redaction.py` (new)** — 73 tests. `tests/test_reports.py` and
  `tests/test_fixtures.py` updated (see below).
- Docs/site: `SAFETY.md` rewritten; milestone doc → Complete; `RESULTS.md`,
  `TRACE_FORMAT.md`, `LIMITATIONS.md`, `OBSERVABILITY.md`, `REGRESSION_DETECTION.md`,
  `REPRODUCIBILITY.md` updated; `site/` index, safety, trace-format, results,
  limitations, architecture and milestones pages updated.

## Why

**Refuse on evidence, mask on suspicion.** Splitting the rules into a *certain* tier
(provider key prefixes, JWT, `Bearer`, `Authorization`, private-key header) and a
*heuristic* tier (credential-named assignment, email, generic high-entropy token) is
the whole design. A certain-tier match is evidence, so it can block an artifact. A
heuristic match is a suspicion, and a suspicion that can block an artifact is a denial
of service on your own report — so the heuristic tier masks and never refuses. The
same asymmetry keeps the repository scan quiet: it applies the certain tier only,
because a hygiene check that flags every SHA-256 digest in `docs/` and every author
email in `pyproject.toml` is a hygiene check that gets skipped.

**Identity fields are scanned, never rewritten.** `run_id`, `case_id`, `model_id`, a
tool name, a source id, an error kind — the pipeline joins on all of them. Masking one
would destroy the artifact's meaning while claiming to protect it. So a certain-tier
match in an identity field yields `redaction_status="flagged"` and the executor
refuses the run. That gives `flagged` a precise meaning and explains why it can never
appear in a file this project wrote: it exists for traces imported from elsewhere
(Milestone 10).

**The status is derived, and only escalates.** `clean < redacted < flagged`;
`redact_record()` takes the maximum of what this pass found and what the record
carried. An adapter claiming `clean` over a live secret is overruled — the scan is the
authority, and that is the dangerous lie. An adapter claiming `redacted` keeps it,
because a second pass over placeholder-filled text finds nothing to mask and would
otherwise conclude the record was never redacted. The monotone rule is what makes
`redact_record(redact_record(r))` equal `redact_record(r)` in bytes *and* status.

**Redaction runs inside `execute_suite`, not `write_run`.** `run_meta.json` publishes
`records_sha256` over `run.jsonl`; a redactor at write time would hash text that no
longer exists, and `write_run`'s own digest check would fail. Running it before
`_account` also means `output_tokens_estimate` describes the text actually persisted,
instead of quietly encoding the length of the secret.

**Spans are a gate target, not a mask target.** This answers the question Milestone 8's
review note left open. Every string in a span is a symbol the trace, the comparison and
the report all index by; masking a `tool_name` would break tool-call comparison in
exchange for protecting a string that by construction holds no free text. The record it
was projected from was masked upstream.

## Tests changed, and why

Two Milestone 7 tests began failing, both because the pipeline started doing the right
thing. `leaky_comparison` spliced a fake key into `under_refusal`, which occurs only
inside rule-authored flag *explanations* — free text, which the new default redactor
masks. The gate no longer had anything to refuse.

- The fixture now splices into a **run id** (an identifier the redactor will not
  rewrite), so the gate tests assert what they were always about: a document that
  cannot be cleaned is not written.
- A new `maskable_comparison` fixture keeps the free-text splice, and a new test
  asserts the report *is* written and holds `[REDACTED:openai_key]`.
- `test_a_report_carrying_a_secret_is_refused_and_nothing_is_written` and
  `test_both_renderers_are_gated` now pass `redactor=identity_redactor` explicitly.
  That is the property under test — the gate holding on its own — stated rather than
  relied on as a default.
- `tests/test_fixtures.py` gained `redaction:allow` markers on the three lines that
  must name a credential shape in order to test the scanner. The alternative, excluding
  `tests/` from the scan, would exclude the files most likely to grow a real key by
  accident. The marker was found by running the scanner, which reported this file and
  `tests/test_redaction.py`'s own JWT literal.

## Results

- 651 tests pass (was 577; 74 new: 73 in `tests/test_redaction.py`, 1 in
  `tests/test_reports.py`). `ruff check src tests scripts/08_check_repo_hygiene.py`
  is clean.
- 52/52 committed fixture records pass through redaction **unchanged** and report
  `clean` — the assertion that guards against a future rule rewriting scored text.
- `run.jsonl`, `run_meta.json` and `docs/examples/regression_report.md` are byte-identical
  to before the milestone. The smoke comparison still detects 6 flags (4 critical, 2 warn).
- `llmreg scan-secrets` reports 0 findings on this repository.
- All three `redaction_status` values are reachable and tested.

## Constraints held

Stdlib only (`re`, `math`, `collections.Counter`, `dataclasses`, `pathlib`). No clock,
no RNG, no network — asserted by an AST scan over `redaction.py`. Redaction of the same
text is byte-identical across calls. Nothing pushed.
