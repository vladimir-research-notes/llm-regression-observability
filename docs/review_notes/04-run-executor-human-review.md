Human review: Milestone 4 — Run-suite executor

TL;DR
Added the run executor that turns a suite plus an adapter into reproducible
artifacts: `llmreg run-suite` writes `results/runs/<run_id>/run.jsonl` (one
`RunRecord` per case, in suite order) and `run_meta.json` (identity, totals, cost
model, `sha256` of `run.jsonl`). Byte-stable persistence is centralized in
`traces/storage.py`; `datasets.load_eval_suite()` resolves a manifest into an
ordered `EvalSuite`. The executor owns run identity and re-stamps replayed
records, resolving the open question left by the Milestone 3 review. Full suite
is 190 passed (51 new). Ruff clean. Offline, dependency-free, deterministic.
Nothing pushed.

What changed
- `src/llm_regression_observability/executor.py` — `RunConfig`, `RunMeta`,
  `RunResult`, `CostModel`, `ExecutorError`; `execute_suite()` (pure),
  `write_run()` (persists), `run_suite()` (load → build → execute → write, with
  `out_root=None` as the dry-run path); `build_adapter()`, `default_run_id()`.
- `src/llm_regression_observability/traces/` — new package. `storage.py`:
  `write_jsonl` / `read_jsonl` / `write_json` / `read_json` / `render_jsonl` /
  `sha256_hex` / `file_sha256`. Sorted keys, compact JSONL separators,
  `ensure_ascii=False`, explicit UTF-8 and `\n` (trailing included), readers that
  validate against a `Schema` and report `path:lineno`.
- `src/llm_regression_observability/datasets/loader.py` — `load_eval_suite()`.
- `src/llm_regression_observability/cli.py` — `run-suite` subcommand:
  `--suite --adapter --app-version --out --run-id --model-id --prompt-version
  --tool-version --timestamp --cost-input-per-1k --cost-output-per-1k
  --recompute-cost --fixtures --dry-run --continue-on-error`.
- `scripts/02_run_baseline.py`, `scripts/03_run_candidate.py` — thin CLI wrappers.
- `tests/test_run_executor.py` — 51 CPU-only tests.
- Docs/site: milestone doc → Complete (with the observed run table);
  `docs/TRACE_FORMAT.md` gained `RunMeta` + the identity-vs-provenance rule;
  `docs/RESULTS.md` reports its first real (non-detection) numbers;
  `docs/LIMITATIONS.md` gained recorded-latency and synthetic-price-list entries;
  `docs/REPRODUCIBILITY.md` marks steps 1–3 as working; `site/index.html`,
  `architecture.html`, `trace-format.html`, `results.html`, `milestones.html`
  updated.

What should be checked later
- **The executor re-stamps `run_id` and `app_version` onto every record.** This
  settles the question deferred by the Milestone 2 and 3 review notes, in favor
  of *the executor owns identity*. The consequence: a run's `run.jsonl` is no
  longer byte-identical to the committed `data/fixtures/*/baseline.jsonl`, whose
  records carry per-case `run_id`s (`sj_invalid_json-baseline`). Milestone 3's
  "reproduces the fixtures exactly" test still passes because it asserts at the
  adapter layer, below the executor. **Confirm this is the intended split**
  before Milestone 6 pairs runs by `case_id`.
- **`model_id` differs between the two smoke runs** (`recorded-mock-v1` vs
  `scripted-regression-adapter`) because provenance is preserved rather than
  faked. That is honest but means a naive "changed_fields" diff in Milestone 6
  would report a model change that is really an adapter change. The comparison
  engine should compare *behavioral* fields (as `validation._behavioral_fields`
  already does) and treat identity/provenance separately.
- **`scripts/03_run_candidate.py` hardcodes synthetic labels** —
  `prompt_version=p-2026-06-08`, `tool_version=t-2026-06-08`,
  `timestamp=2026-06-08T12:00:00Z` — to stand for "the deployment the candidate
  represents". They are documented as synthetic in the script docstring and are
  recorded inputs, never clock reads, but they are arbitrary. If Milestone 6 ever
  keys on them, they should move into the suite manifest.
- **Latency is recorded, not measured** — the executor never times the adapter.
  This is required by the determinism rule, but it means the framework cannot
  currently detect a latency regression that exists only in live wall-clock time.
  Written up in `docs/LIMITATIONS.md`. Milestone 11's live adapter, or an
  imported production trace (Milestone 10), is where a real measurement would
  enter, and it would need a scored/unscored split for the timing field.
- **The cost model's default prices ($0.003 / $0.015 per 1k) are synthetic.**
  They are in a plausible order of magnitude and are not any vendor's pricing.
  `--recompute-cost` reprices every record under one model; without it, recorded
  fixture costs win. Decide before Milestone 6 whether comparisons should force
  `--recompute-cost` so both sides are priced identically — otherwise a
  `cost_regression` could in principle be an artifact of two different price
  lists rather than of token growth.
- **The token estimator remains ~4 chars/token.** The executor bills prompt +
  `input_payload` (sorted-key JSON) + retrieval-doc text for input, and
  `final_output` for output, but only when the adapter supplied zeros — which the
  recorded fixtures never do. Consequently the candidate's token totals equal the
  baseline's even where an injector truncated the output. Real accounting is
  Milestone 11.
- **`--continue-on-error` tags an adapter failure as `origin="infra"`** and emits
  an otherwise-empty record. That keeps harness breakage out of
  `error_rate_regression`, but an empty record will also score as a total failure
  on every content metric in Milestone 5. Decide whether such records should be
  *excluded* from scoring rather than scored as zero.
- **`run_meta.json` reports `"(mixed)"`** for a provenance field that is not
  constant across a run's records. No shipped adapter produces a mixed run, so
  the sentinel is exercised only by a test-local adapter that varies `model_id`
  per case. If Milestone 6 or 7 ever renders `RunMeta.model_id` into a report,
  decide whether `"(mixed)"` should instead be a list of the distinct values.

Technical risks
- Low. Pure stdlib (`json`, `hashlib`, `re`, `dataclasses`, `pathlib`, `ast` in
  tests); no network, clock, RNG, paid API, or secrets. Enforced rather than
  asserted: `test_no_ambient_time_randomness_or_network_in_scored_paths` parses
  each new module's AST and fails if it imports `time`, `datetime`, `random`,
  `secrets`, `socket`, `urllib`, `http`, or `requests`.
- `--adapter api-stub` is selectable and constructs, but raises before any call —
  the run exits 1 with the "disabled by default" message. No default path reaches
  a paid API before Milestone 11.
- `run_id` names an output directory, so it is validated against
  `^[A-Za-z0-9][A-Za-z0-9._-]*$`; `--run-id ../../etc` raises rather than writing
  outside `--out`. Worth a second look — that regex is the only thing between a
  caller-supplied string and `Path(out_root) / run_id`.
- `write_run()` re-hashes `run.jsonl` after writing and raises on a mismatch with
  the in-memory digest, so a truncated or re-encoded write cannot pass silently.
- Generated `results/` contents stay gitignored; nothing large or private is
  committed.

Commands to verify
    python3 -m pytest -q                                   # 190 passed
    python3 -m pytest -q tests/test_run_executor.py        # 48 passed
    python3 scripts/01_validate_fixtures.py evals/fixtures # PASS (unchanged)
    python3 scripts/02_run_baseline.py                     # -> results/runs/smoke-baseline-recorded/
    python3 scripts/03_run_candidate.py                    # -> results/runs/smoke-candidate-scripted-regression/
    python3 scripts/02_run_baseline.py --out results/processed/_check
    diff -r results/runs/smoke-baseline-recorded \
            results/processed/_check/smoke-baseline-recorded   # byte-identical
    ruff check src tests conftest.py                       # clean (optional dev extra)

Human feedback
<!-- Human will fill this later. -->
