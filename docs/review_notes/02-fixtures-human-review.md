Human review: Milestone 2 — Fixture suites

TL;DR
Added the six synthetic fixture suites that give every downstream scorer ground
truth, plus a `datasets` package that loads and validates them and a stdlib-only
reader for the smoke-suite YAML manifest. 26 cases (18 injected defects + 8
negative controls) across structured JSON, tool use, retrieval/evidence, refusal
boundary, latency/cost, and agent traces. New: `llmreg validate-fixtures` and
`scripts/01_validate_fixtures.py`. Full suite is 93 passed (25 new). Offline,
dependency-free, deterministic. Nothing pushed.

What changed
- `evals/fixtures/<suite>/cases.json` — `EvalCase` manifests per suite.
- `evals/fixtures/<suite>/expected.json` — new `CaseExpectation` ground-truth
  records: `injected_defect`, `expected_category`, `expected_severity`,
  `expected_attribution`, `note`.
- `data/fixtures/<suite>/{baseline,candidate}.jsonl` — recorded `RunRecord`
  streams; each candidate carries exactly one injected defect (or a benign /
  sub-threshold change, for controls).
- `evals/suites/smoke.yaml` — an 8-case cross-section (6 defects + 2 controls,
  one of which is infra-attributed) for the eventual end-to-end smoke run.
- `src/llm_regression_observability/datasets/` — new package:
  `_yaml.py` (restricted-YAML reader), `fixtures.py` (roots, suite names, the
  suite→risk-category and suite→allowed-category maps), `loader.py`
  (`CaseExpectation`, JSON/JSONL/manifest loaders), `validation.py`
  (`validate_fixtures`, secret scanner).
- `cli.py` — `validate-fixtures` subcommand (lazy import; `version`/`check-env`
  unaffected).
- `scripts/01_validate_fixtures.py` — text + `--json` report; non-zero on failure.
- `tests/test_fixtures.py` — 25 tests.
- Docs/site: milestone doc → Complete; `data/README.md` layout rewritten to match;
  `site/regression-categories.html` gained a ground-truth-fixtures section;
  `site/index.html` and `site/milestones.html` status/implementation updated.

What should be checked later
- **Restricted YAML reader.** `datasets/_yaml.py` parses only the subset the
  manifests use (mappings, nested mappings, scalar sequences, comments) and
  raises `YamlError` on flow style, tabs, sequences-of-mappings, block scalars,
  anchors, and multi-doc streams. Confirm this is acceptable versus taking a
  PyYAML dependency (rejected here to keep the dependency-free-core constraint).
  If a future manifest needs a richer shape, extend the reader and its tests.
- **Recorded runs are full `RunRecord`s.** They carry synthetic `run_id`s
  (`<case>-baseline` / `-candidate`) and recorded timestamps/versions. Milestone 3
  (RecordedModelAdapter) and Milestone 4 (executor) should agree on whether the
  executor overrides run-level identity fields or preserves the recorded ones.
- **"One injected defect per case."** The validator only asserts a non-control
  candidate *differs* from its baseline on some behavioral field; it does not (by
  design for this milestone) score direction or magnitude — that is Milestones 5–6.
  A couple of cases have an incidental second-order change (e.g. `at_more_retries`
  latency 300→360 ms, deliberately below the +50 ms / +25% floor). Confirm those
  stay single-category once the detector exists.
- **Severities/attributions are hand-assigned ground truth.** They encode the
  expected Milestone 6 output; if the detector's severity rules differ, reconcile
  the fixtures and the detector (the fixtures are the intended source of truth).
- **Secret scan is a precursor, not the redactor.** It matches specific
  credential prefixes / key headers / bearer tokens; Milestone 9 owns full
  redaction. The synthetic under-refusal case deliberately "discloses" an obvious
  non-secret placeholder (`example-not-a-real-secret`).

Technical risks
- Low. Pure stdlib (`json`, `re`, `dataclasses`, `pathlib`); no network, clock,
  RNG, paid API, or secrets. All fixtures synthetic and safe (non-clinical,
  non-exploitative). Deterministic: same inputs → same validation result.

Commands to verify
    python3 -m pytest -q                                   # 93 passed
    python3 -m pytest -q tests/test_fixtures.py            # 25 passed
    python3 scripts/01_validate_fixtures.py evals/fixtures # PASS
    python3 -c "import sys; sys.path.insert(0,'src'); from llm_regression_observability.cli import main; raise SystemExit(main(['validate-fixtures']))"
    ruff check src tests scripts/01_validate_fixtures.py conftest.py  # clean (optional dev extra)

Human feedback
<!-- Human will fill this later. -->
