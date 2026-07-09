Human review: Milestone 9 — Redaction and safety hygiene

TL;DR
Added `src/llm_regression_observability/traces/redaction.py`: the single definition of
"credential-like" in this repository, a masking redactor built on it, and a fail-closed
gate. Twelve rules in two tiers — `certain` rules refuse an artifact, `heuristic` rules
only mask. The redactor runs inside `execute_suite` (before tokens are accounted and
before `run.jsonl` is hashed), inside `build_report` (free text only), and as a gate in
`write_spans` and `write_report`. `redaction_status` is now *derived* per `RunRecord`
and forms a lattice (`clean < redacted < flagged`) that only escalates; a `flagged`
record — a credential in a join key — refuses the run. `SECRET_PATTERNS` moved here from
`datasets/validation.py` and all callers follow it, as Milestone 7's note promised.
`llmreg scan-secrets` and `scripts/08_check_repo_hygiene.py` scan the committed tree.
Full suite is 651 passed (74 new). Ruff clean apart from the same pre-existing
Milestone 0 line. Offline, dependency-free, deterministic, no clock anywhere. Nothing
pushed.

What changed
- `traces/redaction.py` — `RedactionRule` / `RULES` / `CERTAIN_RULES` / `SECRET_PATTERNS`;
  `redact_text()`, `redact_value()`, `redact_record()` → `RecordRedaction`;
  `scan_text()`, `scan_for_secrets()`, `secret_findings()`, `assert_clean()`,
  `refusal_message()`; `identity_findings()`; `escalate()` / `STATUS_ORDER`;
  `shannon_entropy()`; `scan_tree()` / `TreeFinding` / `ALLOW_MARKER`; `RedactionError`.
- `traces/__init__.py` — re-exports the redaction surface alongside storage.
- `executor.py` — `_redact()` in the case loop; `RunMeta.redacted_case_count`.
- `observability/spans.py` — `write_spans` gates the rendered file.
- `reports/base.py` — table imported, not owned; `masking_redactor` added;
  `assert_redaction_clean` delegates and still raises `ReportError`.
- `reports/model.py`, `reports/__init__.py` — masking redactor is the default.
- `datasets/validation.py` — imports the table; its local copy deleted.
- `cli.py` — `llmreg scan-secrets`; `run-suite` prints a redaction line.
- `scripts/08_check_repo_hygiene.py`.
- `tests/test_redaction.py` (73 tests); `tests/test_reports.py` and
  `tests/test_fixtures.py` updated — see "Tests changed" below.
- Docs/site: `SAFETY.md` rewritten; milestone doc → Complete; `RESULTS.md`,
  `TRACE_FORMAT.md`, `LIMITATIONS.md`, `OBSERVABILITY.md`, `REGRESSION_DETECTION.md`,
  `REPRODUCIBILITY.md`; `site/` index, safety, trace-format, results, limitations,
  architecture, milestones.

Decisions taken, each of which a reviewer may want to reverse
- **Two tiers: refuse on evidence, mask on suspicion.** `certain` = provider key
  prefixes, JWT, private-key header, `Bearer`, `Authorization`. `heuristic` =
  credential-named assignment, email, generic high-entropy token. Only the certain tier
  blocks an artifact or decides `redaction_status`; only the certain tier is used by the
  repository scan. **Confirm the split, and in particular that `secret_assignment`
  (`api_key=…`) belongs in `heuristic` rather than `certain`** — it is name-based and
  could plausibly fire on prose, which is why it does not block.
- **Identity fields are scanned, never rewritten; a match refuses the run.** Masking a
  `case_id` or a `tool_calls[].name` would corrupt pairing and tool-call diffing. The
  alternative — persist a `flagged` record with the secret intact — writes a secret.
  **Confirm the refusal, and confirm that refusing the *whole run* (rather than dropping
  the one case) is right.**
- **`redaction_status` is a monotone lattice, not a pure function of the current text.**
  A record arriving `redacted` keeps that status even though a second pass finds nothing
  to mask. This is the only way redaction is idempotent in status as well as bytes, and
  it means a lying adapter can over-report redaction (conservative) but never
  under-report it (the scan overrules `clean`). **The alternative — recompute purely
  from the text — makes `redact(redact(r)) != redact(r)` and erases the field's only
  fact. Confirm.**
- **Redaction lives in `execute_suite`, not `write_run`.** Required, not stylistic:
  `RunMeta.records_sha256` is computed in `_summarize` and `write_run` verifies it against
  the file. Redacting later would make the digest describe text that was never written.
  A side effect is that `output_tokens_estimate`, when derived, counts the *masked* text.
  **Confirm that is preferred over counting the original.**
- **Spans are gated, not masked.** Answers Milestone 8's open question. Every span
  attribute is a symbol the pipeline joins on. **Confirm; if a future span carries free
  text, this decision must be revisited, not the gate.**
- **`build_report`'s default redactor changed from `identity_redactor` to masking.** This
  is a public-API behavior change: a library caller who previously got a `ReportError` for
  a secret in a flag explanation now gets a masked report. **Confirm that fail-safe beats
  fail-loud here.** `identity_redactor` remains for callers who want the old behavior.

What should be checked later
- **A false-positive mask silently changes a scored string.** `final_output` is redacted
  before any evaluator reads it. Today all 52 committed fixture records pass through
  byte-identical (`test_the_committed_fixtures_pass_through_redaction_unchanged`), which
  is the guard — but the guard only covers *this* corpus. **A new fixture containing a
  long mixed-class token would be scored on `[REDACTED:high_entropy_token]`.** Recorded in
  `LIMITATIONS.md` rather than quietly fixed. Decide whether the executor should refuse to
  mask a *scored* field, or warn, or keep masking.
- **The entropy rule's thresholds are tuned, not derived.** `MIN_TOKEN_LENGTH = 24`,
  `MIN_ENTROPY_BITS = 3.2`, plus a mandatory lowercase+uppercase+digit filter. The filter,
  not the entropy floor, is what clears a SHA-256 digest (entropy 3.79 — above the floor).
  **There is no false-positive/false-negative measurement behind these numbers; they were
  chosen against the strings this repository writes.** Any real corpus should re-tune them.
- **`email` masks PII but is heuristic, so it never refuses.** An email in a *report* is
  therefore masked, but an email in a *run id* would be written. Judged acceptable
  (identity fields are synthetic and machine-generated). **Confirm.**
- **The `redaction:allow` marker is honored by the tree scan, and nothing audits its use.**
  Three lines in `tests/test_fixtures.py` and one construction in `tests/test_redaction.py`
  need it. A real secret could be hidden behind the marker. **Consider whether the marker
  should require a reason, or whether the scan should report suppressed lines in a summary.**
- **`scan_tree` approximates "committed" with a skip list**, not with `git ls-files` (no
  subprocess, so the scan works without git and inside tests). It skips `.git`, caches,
  virtualenvs, `build`/`dist`, `results/`, and `settings.local.json` by name; it reads only
  an allowlist of text suffixes under 1 MiB. **A secret in a file type outside the allowlist
  is not scanned.** Confirm the allowlist, or spend the subprocess.
- **`RunMeta` gained a field.** `run_meta.json` written before this milestone lacks
  `redacted_case_count`; it has a default, so old artifacts still load. `results/` is
  gitignored and regenerable, so nothing committed is stale. **Confirm no external consumer
  pins the field set.**
- **Nothing masks `EvalCase` inputs.** A prompt or a retrieval doc is a fixture, validated
  secret-free by `llmreg validate-fixtures`. If Milestone 10 imports cases from a sibling
  repository, that assumption ends. **The import path will need `redact_value` over the
  case, or an explicit refusal.**
- **`llmreg validate-fixtures` still prints "secrets scan : clean"** using the certain tier.
  It now reads the same table as everything else, so the phrase means exactly what the gate
  means. No change needed; noted so the two are not later allowed to drift apart again.

Technical risks
- Low. Pure stdlib (`re`, `math`, `collections.Counter`, `dataclasses`, `pathlib`,
  `collections.abc`, `typing`); no network, clock, RNG, paid API, or secrets.
  `test_the_redactor_reaches_for_no_clock_rng_or_socket` is an AST scan over the module's
  imports.
- Redaction is idempotent and deterministic:
  `test_masked_text_never_matches_a_certain_rule_again` (per rule),
  `test_redacting_a_record_twice_changes_nothing`,
  `test_redaction_of_the_same_text_is_byte_identical_across_calls`.
- The placeholder `[REDACTED:<rule>]` contains `[` and `:`, which appear in no rule's
  character class, so no rule can re-match another rule's output. A property test over the
  whole table asserts it rather than the comment.
- Every rule has a sample and every sample is assembled at runtime, so no line of the test
  file is itself a credential for the scanner. `test_every_rule_has_a_sample` fails if a
  rule is added without one.
- No error message, `TreeFinding`, or scan result ever contains a matched value; asserted
  in five places.
- Post-condition: `redact_record` re-scans what it masked and raises `RedactionError` if a
  certain-tier pattern survived. Currently unreachable by construction; kept as a defensive
  assertion at the point of change rather than three stages downstream at the report gate.

Commands to verify
    python3 -m pytest -q                                    # 651 passed
    python3 -m pytest -q tests/test_redaction.py            # 73 passed
    python3 scripts/08_check_repo_hygiene.py                # PASS, 0 findings
    llmreg scan-secrets                                     # same scan via the CLI

    # the fixtures are unchanged by redaction (the guard against rewriting scored text):
    python3 -m pytest -q tests/test_redaction.py -k committed_fixtures

    # redaction runs before the hash, and the artifact proves it:
    python3 scripts/02_run_baseline.py
    python3 -c "import json,hashlib,pathlib; d=pathlib.Path('results/runs/smoke-baseline-recorded'); print(hashlib.sha256(d.joinpath('run.jsonl').read_bytes()).hexdigest() == json.loads(d.joinpath('run_meta.json').read_text())['records_sha256'])"

    # end to end, and byte-identical to before this milestone:
    python3 scripts/03_run_candidate.py
    python3 scripts/07_aggregate_runs.py
    python3 scripts/05_compare_runs.py
    python3 scripts/06_generate_report.py
    diff results/reports/regression_report.md docs/examples/regression_report.md

    ruff check src tests scripts/08_check_repo_hygiene.py   # clean (dev extra)

Human feedback
<!-- Human will fill this later. -->
