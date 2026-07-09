# Milestone 10 — Integration contracts

## What changed

- **`datasets/versioning.py` (new)** — a versioned import/export contract
  (version `1.0`). A sealed *envelope* wraps a payload with: contract version,
  source id / schema / version, a recorded (not clock-derived) export timestamp,
  a `record_schema` fingerprint of the `RunRecord` shape, and a `sha256` over the
  payload. Seal and open both validate.
- **`datasets/importers/` (new package)** —
  - `base.py` — `TraceImporter` interface and shared field coercion
    (`tool_call`, record building, error paths that name the offending field).
  - `registry.py` — `SOURCES`, `get_importer`, and `--source auto` detection.
  - `native.py` — round-trips this repo's own exported bundles.
  - `runs.py` — an imported-record replay adapter (not a CLI adapter).
  - one importer per source: `code_agent_runtime`, `bioagentbench`,
    `bioscientist_os`, `agent_control_bench`, `claude_api`.
- **`cli.py`** — `import-trace` (with `--source`, `--dry-run`, `--lenient`,
  model/prompt/tool/timestamp overrides, cost-rate overrides) and `export-run`
  (seal a run directory into a versioned bundle).
- **`scripts/09_import_external_trace.py`** — a five-step demo: import each
  source, compare an imported baseline vs candidate, render a report, and export
  a sealed bundle.
- **`data/external/<source>/*.json`** — small synthetic example traces (48 KB
  total) for all five sources. No real repository data.
- **Docs/site** — `docs/INTEGRATION_CONTRACTS.md`, a bundle-format section in
  `docs/TRACE_FORMAT.md` (+46 lines), `site/integration-contracts.html` and a nav
  link on every page, status badges advanced to "Milestone 10 complete".
- **`.gitignore`** — ignore generated `results/external/*`.
- **`tests/test_integration_contracts.py`** — 101 tests.

## Why

The project is a foundation for later integration with sibling systems
(code-agent runtime, BioAgentBench, BioScientist OS, agent-control-bench, and the
Claude API). Defining a stable, versioned, hashed contract now — validated only
against committed synthetic examples — keeps that future cheap without adding any
real dependency today. The `sha256`/`record_schema` seal makes an imported trace
tamper-evident and a schema drift detectable.

## Key decisions

- **No external repo is required** to build or test; contracts are exercised on
  synthetic fixtures only.
- **Import is the boundary where a trace can legitimately arrive `flagged`** — the
  Milestone 9 redactor runs on imported records, so a credential in an external
  join key refuses the run rather than being silently rewritten.
- Determinism preserved: the export timestamp is a recorded input, never the
  clock; two exports of the same run are byte-identical.

## Validation

- `python3 -m pytest -q tests/test_integration_contracts.py` → 101 passed.
- `python3 -m pytest -q` → 752 passed (offline, CPU-only).
- `python3 scripts/09_import_external_trace.py` → import → compare → report →
  export all PASS; rendered report redaction-clean.
- `python3 scripts/08_check_repo_hygiene.py` → 0 findings.
- `python3 -m ruff check src tests scripts` → clean.

## Note

The implementation was written by the interrupted autonomous Milestone 10 run
(session limit hit before it committed); this commit finalizes it. No functional
code was changed during finalization — only the milestone status line, this
commit note, and the review placeholder were added after re-verifying the tree.
