# Milestone 2 — Fixture suites

## What changed
Added the committed, synthetic ground-truth fixtures that drive every downstream
scorer, plus the `datasets` package that loads and validates them.

- **Fixtures (26 cases across six suites).** Per suite:
  - `evals/fixtures/<suite>/cases.json` — a list of `EvalCase`.
  - `evals/fixtures/<suite>/expected.json` — a list of the new `CaseExpectation`
    ground-truth records (`injected_defect`, `expected_category`,
    `expected_severity`, `expected_attribution`, `note`).
  - `data/fixtures/<suite>/{baseline,candidate}.jsonl` — recorded `RunRecord`
    streams; each candidate carries **exactly one injected defect** (or a benign /
    sub-threshold change for the 8 control cases).
  - Suites: `structured_json`, `tool_use`, `retrieval_evidence`,
    `refusal_boundary`, `latency_cost`, `agent_trace` (18 defects + 8 controls).
- **`evals/suites/smoke.yaml`** — an 8-case cross-section (6 defects + a benign
  control + an infra-attributed control) for the eventual end-to-end smoke run.
- **`src/llm_regression_observability/datasets/`** — new package:
  - `_yaml.py` — a deliberately restricted, stdlib-only YAML reader (mappings,
    nested mappings, scalar sequences, comments; raises on everything else) so
    manifests parse without a PyYAML dependency.
  - `fixtures.py` — suite names, on-disk roots (resolved to the repo root), and
    the suite→risk-category / suite→allowed-regression-category maps.
  - `loader.py` — `CaseExpectation` (a `Schema` subclass), JSON/JSONL loaders,
    and `load_suite_manifest`.
  - `validation.py` — `validate_fixtures` and a credential-pattern scanner.
- **`cli.py`** — `validate-fixtures` subcommand (lazy import).
- **`scripts/01_validate_fixtures.py`** — text and `--json` report; non-zero exit
  on any failure.
- **`tests/test_fixtures.py`** — 25 CPU-only tests.

## Why / key decisions
- **Ground truth per case, not just data.** Each candidate defect is annotated
  with the category/severity/attribution a correct detector must emit, so
  Milestone 6 can assert "the injected regression was detected" as a stable test.
- **One injected defect per case + explicit controls.** Baseline and candidate
  differ on exactly the dimension under test; the validator asserts a non-control
  candidate is behaviorally different from its baseline (so an annotation cannot
  describe a defect that was never injected). Controls prove benign changes (a
  JSON key reorder) and sub-threshold drifts do not flag — the false-positive
  guardrail from `docs/REGRESSION_DETECTION.md`.
- **Infra-attribution fixture.** `agent_trace/at_infra_noise` is an
  `infra`-attributed degradation with `expected_category: none` — the ground
  truth behind research question 9 (surface harness noise, do not flag it).
- **Restricted YAML instead of PyYAML.** The dependency-free-core constraint is
  load-bearing (tight disk, no guaranteed network); a small, tested subset reader
  keeps suite manifests readable without a third-party dependency.
- **Schema-shaped fixtures.** Everything parses against the Milestone 1 contracts
  as it loads, so a malformed fixture fails at load time with a path-qualified
  `SchemaError`, and the recorded runs feed Milestone 3 (recorded adapter) and
  Milestone 6 (comparison) directly.

## Validation run
- `python3 -m pytest -q` → **93 passed** (14 scaffold + 54 schema + 25 fixtures).
- `python3 scripts/01_validate_fixtures.py evals/fixtures` → PASS (26 cases; 18
  defects, 8 controls; secrets scan clean).
- `llmreg validate-fixtures` → PASS.
- `ruff check src tests scripts/01_validate_fixtures.py conftest.py` → clean
  (optional; ruff is a dev extra).

Next: Milestone 3 — adapters (mock / recorded / scripted-regression + interface).
