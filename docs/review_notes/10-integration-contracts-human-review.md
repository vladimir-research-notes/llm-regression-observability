Human review: Milestone 10 — Integration contracts

TL;DR
Added a versioned trace import/export layer so this framework can consume traces
from sibling systems without depending on any of them. `datasets/versioning.py`
defines a contract (version `1.0`): a sealed *envelope* around a payload, carrying
the contract version, source id/schema/version, a recorded (non-clock) export
timestamp, a `record_schema` fingerprint, and a `sha256` over the payload.
`datasets/importers/` is a package: a `TraceImporter` base with shared field
coercion, a source registry with `--source auto` detection, a `native`
round-trip importer, an imported-record replay adapter (`runs.py`), and five
source importers — code-agent-runtime, BioAgentBench, BioScientist OS,
agent-control-bench, and the Claude API. Small synthetic examples under
`data/external/` (48 KB total) import into valid `RunRecord`s and run through the
existing comparison → report path unchanged. `llmreg import-trace` /
`export-run`, `scripts/09_import_external_trace.py`,
`docs/INTEGRATION_CONTRACTS.md`, `site/integration-contracts.html` (+ nav on all
pages), and a `docs/TRACE_FORMAT.md` bundle section. Full suite 752 passed (101
new); ruff clean; hygiene scan 0 findings; offline, dependency-free,
deterministic; nothing pushed.

Provenance note
The implementation was produced by the autonomous Milestone 10 `claude -p` run,
which was interrupted by a session limit *after* the code, tests, docs, and site
were complete and green but *before* it committed. In the follow-up, no
functional code was changed: the tree was verified (752 passed, ruff clean,
hygiene PASS, end-to-end import→compare→report and export re-run), only this
review note, the commit note, and the milestone-doc status line were added, and
the result was committed. This note was not written by a human reviewer.

What changed
- `datasets/versioning.py` — the envelope/bundle contract (version `1.0`),
  `record_schema` fingerprint, `sha256(payload)`, seal/open with validation.
- `datasets/importers/` — `base.py` (`TraceImporter`, coercion helpers),
  `registry.py` (`SOURCES`, `get_importer`, auto-detect), `native.py`,
  `runs.py` (imported-record replay adapter), and one importer per source:
  `code_agent_runtime.py`, `bioagentbench.py`, `bioscientist_os.py`,
  `agent_control_bench.py`, `claude_api.py`.
- `cli.py` — `import-trace` and `export-run` subcommands (with `--source`,
  `--dry-run`, `--lenient`, cost-rate overrides).
- `scripts/09_import_external_trace.py` — 5-step demo (import each source →
  compare → report → export sealed bundle).
- `data/external/<source>/*.json` — synthetic example traces (no real repo data).
- `evals/suites/external.yaml`, `docs/INTEGRATION_CONTRACTS.md`,
  `docs/TRACE_FORMAT.md` (+46 lines: bundle format), `site/integration-contracts.html`,
  nav link added across all site pages, `.gitignore` (`results/external/*`).
- `tests/test_integration_contracts.py` — 101 tests.

What should be checked later
- Whether the five source-importer field mappings match those systems' *real*
  trace shapes; today they are validated only against committed synthetic
  examples, which is the milestone's stated scope.
- Whether the `record_schema` fingerprint should be pinned into a contract test,
  so a future `RunRecord` change to the schema is a deliberate contract bump.
- The Claude API importer maps a recorded/exported shape only; it does not (and
  must not, per the plan) make any network call — confirm that separation holds
  once Milestone 11 adds the guarded API adapter.

Technical risks
- Auto-detection (`--source auto`) keys on envelope/shape hints; an ambiguous
  external document could be misrouted — `--source` forces it, and `--dry-run`
  shows the choice first.
- `--lenient` relaxes import strictness; make sure it is not the default anywhere
  a silent coercion could hide a malformed external trace.

Commands to verify
```bash
python3 -m pytest -q tests/test_integration_contracts.py   # 101 passed
python3 -m pytest -q                                        # 752 passed
python3 scripts/09_import_external_trace.py                 # import→compare→report→export, PASS
python3 scripts/08_check_repo_hygiene.py                    # 0 findings
python3 -m ruff check src tests scripts                     # clean
```

Human feedback
<!-- Human will fill this later. -->
