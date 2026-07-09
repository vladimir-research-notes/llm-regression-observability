Human review: Milestone 11 — Optional API adapter stub and cost guard

TL;DR

`ApiAdapterStub` becomes a real, guarded paid-API adapter that still cannot spend
money. Seven gates in a fixed order (opt-in → cache → estimate → dry-run →
verified pricing → ceiling → transport), and the package ships no network client
at all — `anthropic` is not a dependency, nothing imports it, and a test proves
that by parsing the package's own source rather than by watching `sys.modules`.
The token/cost estimator moved to a new `pricing.py` so the number a guard
refuses on is the same number the cost evaluator scores. 97 new tests; 849 total,
all offline. `conftest.py` now disables outbound TCP for every test in the repo.

What changed

- **New `src/llm_regression_observability/pricing.py`** — the one estimator.
  `COST_DECIMALS` (previously declared three times, kept in sync by comment),
  `estimate_tokens`, `billable_input_text`, `CostModel`, `SYNTHETIC_COST_MODEL`,
  and a new `PriceList` / `CostEstimate` / `load_price_list`. `executor.py`,
  `evaluators/cost.py` and `comparison/base.py` now import from it and re-export
  their old names, so nothing downstream changed shape.
- **New `adapters/api_guard.py`** — `ApiGuard` (opt-in, dry-run, ceiling,
  `allow_unverified_pricing`, cache dir), `CostLedger`, and the refusal hierarchy
  `ApiGuardError(AdapterError)` → `ApiAdapterDisabled`, `MaxCostExceeded`,
  `TransportMissing`, `UnverifiedPricing`.
- **New `adapters/api_cache.py`** — content-addressed, atomically written,
  gitignored response cache. `CacheEntry` is a `Schema`, so entries are
  byte-stable JSON.
- **New `adapters/api_transport.py`** — the seam a real client plugs into.
  Imported by nothing; its `import anthropic` lives inside the function body.
- **Rewrote `adapters/api_stub.py`** — the seven-gate ladder, `ApiRequest` /
  `ApiResponse` / `Transport`, `ApiCall`, and `ApiPlan`.
- **New `llmreg estimate-cost`** — prices a suite without opting in or calling
  anything; exits 1 if the bound exceeds `--max-cost-usd`.
- **New `config/api_pricing.example.json`** — unverified placeholder prices.
- **`conftest.py`** — autouse fixture disabling `socket.socket.connect`,
  `connect_ex` and `socket.create_connection` for every test.
- **New `docs/API_ADAPTER.md`**; `tests/test_adapters.py` lost the
  `..._until_milestone_11` placeholder test that this milestone retires.

What should be checked later

- **Is the gate order right?** In particular: the cache is consulted *before* the
  budget and *before* the dry-run branch. The reasoning is that a hit spends
  nothing (so it must not consume budget, or a completed suite could not be
  re-run) and that a dry run over a warm cache should return real answers for
  free. Both are defensible; both are choices.
- **The dry-run record uses an `infra`-origin `ErrorRecord` of kind
  `api_dry_run`** as its marker, because `RunRecord` has no metadata field. This
  is a schema workaround. If dry-run records ever get compared for real, consider
  whether `RunRecord` should grow an explicit provenance field instead.
- **`plan()` and `estimate()` require no opt-in.** They are pure arithmetic and
  cannot call anything, so gating them seemed like security theatre — but it does
  mean `ApiAdapterStub().plan(...)` works on a disabled adapter. Confirm that
  reads as intended rather than as a hole.
- **`allow_unverified_pricing` is a real escape hatch.** It lets an operator spend
  against prices nobody checked. It is off by default and the error text explains
  the consequence, but it exists.
- Whether `ApiPlan.input_tokens_total` / `output_tokens_bound_total` counting
  *all* cases while `cost_upper_bound_usd` counts only *uncached* ones is more
  confusing than useful.

Technical risks

- **The ceiling is only as good as the price list.** `verified` / `priced_at`
  make that an operator decision rather than a silent one, but nothing in this
  repository can check a price — it has no network. A wrong price list yields a
  ceiling wrong by the same factor.
- **The token estimator is `chars / 4`.** It is not a tokenizer and will disagree
  with a real bill. It is identical on both sides of a comparison, which is all a
  delta needs, and it is explicitly not what a bill needs.
- **`api_transport.py` has no test coverage and never runs in CI**, because
  running it would cost money. It is a reference implementation. Its Messages-API
  field mapping (`message.content` blocks, `message.usage`) is written from the
  SDK's documented shape and has not been executed against a live response.
- **The `conftest.py` network block patches `socket`, not `os`-level syscalls.** A
  library that bypasses the `socket` module (via `ctypes`, say) would not be
  caught. Nothing in this dependency-free repo does, but the guarantee is
  "no test opens a TCP connection through the standard library", not "no test can
  possibly emit a packet".
- **The `pricing.py` refactor touched scored paths.** The mitigation is that
  `sha256(run.jsonl)` for the smoke baseline is still `ee23bfde…`, the value
  committed at Milestone 4, and the committed example report regenerates
  byte-identically.

Commands to verify

```bash
python3 -m pytest -q                                  # 849 passed, all offline
python3 -m pytest -q tests/test_api_stub.py           # 97 passed

# The adapter is inert, and says so.
python3 -c "
import sys; sys.path.insert(0,'src')
from llm_regression_observability.adapters import ApiAdapterStub
ApiAdapterStub().generate(None, None)"                # ApiAdapterDisabled

# Estimating costs nothing and calls nothing.
llmreg estimate-cost --suite evals/suites/smoke.yaml \
    --pricing config/api_pricing.example.json \
    --model claude-opus-4-8 --max-cost-usd 0.01       # exits 1: over ceiling

# The pipeline is byte-identical across the pricing.py refactor.
llmreg run-suite --suite evals/suites/smoke.yaml --adapter recorded \
    --app-version baseline --out results/runs         # sha256 ee23bfde…

# No network client anywhere on the default path.
grep -rn "^import anthropic\|^import httpx\|^import requests" src/   # no output
```

Adversarial checks worth running: comment out `self.ledger.check(...)` in
`api_stub.py::call` (5 tests fail, including the gate-ordering one) or replace the
cache lookup with `entry = None` (6 tests fail). Both were run during development.

Human feedback
<!-- Human will fill this later. -->
