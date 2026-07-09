# Milestone 11 — Optional API adapter stub and cost guard

**Commit:** `Milestone 11: cost guard (five protections, and no network client to guard)`

## What changed

`ApiAdapterStub` was a two-method placeholder that raised `NotImplementedError`.
It is now the guarded paid-API adapter the plan promised, and it still cannot
spend money.

### New modules

| File | Contents |
| --- | --- |
| `pricing.py` | `COST_DECIMALS`, `estimate_tokens`, `billable_input_text`, `CostModel`, `SYNTHETIC_COST_MODEL`, `CostEstimate`, `PriceList`, `load_price_list` |
| `adapters/api_guard.py` | `ApiGuard`, `CostLedger`, and the refusal hierarchy |
| `adapters/api_cache.py` | `CacheEntry`, `ResponseCache`, `cache_key`, `default_cache_dir` |
| `adapters/api_transport.py` | the seam a real client plugs into; imported by nothing |
| `config/api_pricing.example.json` | unverified placeholder price list |
| `docs/API_ADAPTER.md` | the guards, the gate order, and the reasoning |
| `tests/test_api_stub.py` | 97 tests |

### Changed

- `adapters/api_stub.py` — rewritten as the seven-gate ladder.
- `executor.py`, `evaluators/cost.py`, `comparison/base.py` — import the estimator
  from `pricing.py` and re-export their old names.
- `adapters/base.py` — `estimate_tokens` re-exported from `pricing.py`.
- `cli.py` — new `estimate-cost` subcommand.
- `conftest.py` — autouse fixture disabling outbound TCP for every test.
- `tests/test_adapters.py` — retired the `..._until_milestone_11` placeholder.

## Why it is shaped this way

**The guards are the deliverable; the call is not.** The interesting question is
not "how do we call an API" (ten lines) but "what must be true before a call is
possible." Seven gates, in a fixed order, each refusing for its own reason:

1. **Opt-in** — first, so a disabled adapter does not even stat a cache file.
2. **Cache** — before the budget (a hit spends nothing, so it must not consume
   budget, or a completed suite could never be re-run under the ceiling it already
   paid) and before dry-run (so a dry run over a warm cache returns real answers,
   free).
3. **Estimate** — an unpriced model refuses rather than defaulting to a number
   that looks like a cost.
4. **Dry run** — the default, even once opted in. "I want to use the API adapter"
   and "I want to spend money right now" are different intentions; one flag cannot
   tell them apart.
5. **Verified pricing** — a ceiling enforced against unchecked prices is theater.
6. **Ceiling** — refuses before the transport is reached, so an over-budget suite
   makes zero calls, not *n* − 1. It refuses rather than truncating: silently
   lowering `max_tokens` to fit a budget changes the experiment, and a harness
   that quietly changes the experiment to stay in budget is worse than one that
   stops.
7. **Transport** — absent by default.

Gates 5–6 sit above gate 7 because budget and pricing are *policy*, and policy is
decided without reference to whether a client happens to be installed.

**The estimator got one home.** `COST_DECIMALS` was declared in three modules with
a comment asking the reader to keep them in sync. That is survivable while the
only consumer is a synthetic price list; it stops being survivable when an
estimate is what stands between a run and a bill, because a guard that rounds cost
to eight decimals while the evaluator rounds to six is a guard that can be argued
with. `pricing.py` now backs the executor's artifacts, the cost evaluator's
metric, the comparison engine's delta, and the guard's ledger. The refactor is
byte-preserving: `sha256(run.jsonl)` for the smoke baseline is still `ee23bfde…`,
the value committed at Milestone 4.

**A pre-call estimate bounds cost; it does not predict it.** Input tokens are
knowable before a call; output tokens are not. Output is priced at
`max_output_tokens` — the worst case the API is permitted to produce — because a
budget guard that priced the *average* output is a guard that lets the tail
through. `CostEstimate.output_tokens_bounded` records which of the two any number
is, so a report can never present an upper bound as a measurement. The ledger then
settles from the provider's reported usage: the guard reasons about what *could*
be spent, the ledger tracks what *was*.

**Prices are configuration with provenance.** `PriceList` carries `verified` and
`priced_at`. It is `verified: false` for every list committed here, because this
repository cannot check a price — it has no network, and a number copied into
source on the day it was written silently rots. Estimating with unverified prices
is fine (both sides of a delta use the same prices, so the delta is sound).
*Spending* against them is refused. A `verified: true` list with no `priced_at` is
rejected outright: a "verified" claim with no date cannot go stale, which is the
problem.

**A transport may not invent a measurement.** A response reporting zero tokens or
zero latency is refused — the rule Milestone 10's importers established. A missing
measurement recorded as `0.0` is not missing, it is a *number*, and the comparison
engine believes numbers: a zero-latency baseline makes every candidate an
unbounded `latency_regression`.

**The network client is absent, and that is tested by parsing source.**
`api_transport.py` holds a reference implementation; `api_stub.py` does not import
it, `adapters/__init__.py` does not re-export it, and its `import anthropic` lives
inside a function body. `tests/test_api_stub.py` asserts all of this with `ast`
rather than by watching `sys.modules`, because a runtime check only sees the
imports that happened to run. The scanner is itself unit-tested against a
top-level import, a `try:`-wrapped import and an `if:`-wrapped import, so it
cannot silently match nothing and make every assertion below it vacuous.

**"No test uses the network" became a mechanism.** `conftest.py` disables
`socket.socket.connect`, `connect_ex` and `socket.create_connection` for every
test in the repository, and one test asserts the fixture is armed.

## Beyond the spec

- `pricing.PriceList` provenance and the `UnverifiedPricing` refusal.
- `llmreg estimate-cost` — prices a suite without opting in, opening a cache, or
  being able to reach a provider. Exits 1 over the ceiling, so it can gate a job
  before the job can spend.
- The repository-wide network kill switch.

## Results

- `python3 -m pytest -q` — **849 passed**, all offline, CPU-only.
- `tests/test_api_stub.py` — **97 tests**.
- Smoke `sha256(run.jsonl)` unchanged (`ee23bfde…` baseline), comparison still
  detects the same six clusters, committed example report regenerates
  byte-identically.
- `ruff check src tests conftest.py` — clean.

Adversarially checked during development: removing the ceiling check fails 5 tests
(including the gate-ordering one); removing the cache lookup fails 6.

## Next

Milestone 12 — final portfolio report.
