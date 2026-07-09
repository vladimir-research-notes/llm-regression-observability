# The optional API adapter, and the guards in front of it

> **Status: the guards ship; the network call does not.** `ApiAdapterStub` is
> disabled by default, this package contains no network client, `anthropic` is not
> a dependency, and no test in this repository performs network I/O — a
> repository-wide `conftest.py` fixture disables outbound TCP for every test.

This document describes the one code path in `llm-regression-observability` that
*could* cost money, and the seven gates it must pass first. The guards are the
deliverable (`docs/milestones/11-api-adapter-cost-guard.md`). The call is not.

## Why this is built the way it is

An evaluation harness is re-run constantly: after a scorer changes, after a
threshold moves, after a report template is edited. None of those is a reason to
pay a provider again. And an evaluation harness is exactly the kind of program
that gets pointed at a suite of 500 cases by someone who has not read it.

So the question is not "how do we call an API" — that is ten lines — but "what
must be true before a call is possible, and what does the system do when it is
about to spend more than you meant." Every design decision below follows from
answering that in the strictest direction available.

## The seven gates

Every call passes through the same fixed ladder, in this order
(`adapters/api_stub.py::ApiAdapterStub.call`):

| # | Gate | Refusal | Why it sits here |
| --- | --- | --- | --- |
| 1 | **Opt-in** | `ApiAdapterDisabled` | First, so a disabled adapter does not even stat a cache file. |
| 2 | **Cache** | *(returns)* | Before the budget: a hit spends nothing and must not consume budget. Before the dry-run branch: a dry run over a warm cache returns the real answers, free. |
| 3 | **Estimate** | `UnpricedModel` | Real input tokens; output priced at `max_output_tokens`. An unpriced model refuses rather than defaulting. |
| 4 | **Dry run** | *(returns)* | The default, even once opted in. Nothing below this line executes. |
| 5 | **Verified pricing** | `UnverifiedPricing` | A ceiling enforced against unchecked prices is theater. |
| 6 | **Max-cost ceiling** | `MaxCostExceeded` | Refuses — never truncates. Happens *before* the transport, so an over-budget suite makes zero calls. |
| 7 | **Transport** | `TransportMissing` | Absent by default. This package ships no network client. |

Gates 5 and 6 sit above gate 7 deliberately: budget and pricing are *policy*, and
policy is decided without reference to whether a client happens to be installed.

Every refusal subclasses `ApiGuardError`, which subclasses `AdapterError` — so
the executor's existing adapter-failure handling catches a refused API call the
same way it catches a missing fixture. A guard that needs bespoke plumbing to be
honored is a guard that will eventually be bypassed.

## The five protections `CLAUDE.md` requires

### 1. Explicit opt-in

`ApiAdapterStub()` is inert. The guard is a value (`ApiGuard`), and
`ApiGuard.from_env(env)` takes the environment **as an argument** — library code
that reaches for `os.environ` makes its own behavior depend on a variable no
artifact records, which is the same objection the determinism rule makes to
`time.time()`. The CLI may read the process environment at its boundary; nothing
below the boundary does.

```
LLMREG_API_ENABLED=1                     # opt in (still dry-run)
LLMREG_API_DRY_RUN=0                     # a second, separate decision
LLMREG_API_MAX_COST_USD=0.50             # the ceiling
LLMREG_API_ALLOW_UNVERIFIED_PRICING=1    # accept an unenforceable ceiling
LLMREG_API_CACHE_DIR=/path/to/cache      # default: <repo>/.api_cache (gitignored)
```

An unrecognized boolean (`LLMREG_API_DRY_RUN=maybe`) is **refused**, not
defaulted. A parser cannot know whether the safe fallback for the key it is
reading is "spend" or "don't", so refusing both is the only rule that is safe in
every position.

**No CLI subcommand in this repository can spend money.**
`executor.build_adapter("api-stub")` constructs the adapter with the default,
opted-out guard and offers no parameter that could opt it in. Opting in is a
programmatic act that additionally requires a transport this package does not
ship.

### 2. Dry-run mode, by default

`LLMREG_API_ENABLED=1` alone buys an *estimate*, not a call. "I want to use the
API adapter" and "I want to spend money right now" are different intentions, and
a single flag cannot tell them apart.

A dry-run record carries the cost estimate, an empty `final_output`, and one
`infra`-origin error of kind `api_dry_run`. `RunRecord` has no free-form metadata
field, and the alternative — an empty output with no explanation — would read
downstream as a catastrophic model regression. `infra` origin means the
comparison engine surfaces it and raises no flag (`docs/PLAN.md` §6).

### 3. A transparent token/cost estimator, shared with the cost evaluator

The estimator lives in `pricing.py`, once. Before Milestone 11 `COST_DECIMALS`
was declared in three modules with a comment asking the reader to keep them in
sync. That is survivable while the only consumer is a synthetic price list; it
stops being survivable when an estimate is what stands between a run and a bill,
because a guard that rounds cost to eight decimals while the evaluator rounds to
six is a guard that can be argued with. `executor.py`, `evaluators/cost.py`,
`comparison/base.py` and the API adapter now all import it.

Two honesty properties are enforced rather than described:

- **The token estimate is crude.** `tokens ≈ chars / 4` is not a tokenizer. It is
  identical on both sides of a comparison, which is the only property a *delta*
  needs. It is not the property a *bill* needs.
- **A pre-call estimate bounds cost; it does not predict it.** Input tokens are
  knowable before a call; output tokens are not. The estimator prices output at
  `max_output_tokens` — the worst case the API is permitted to produce. A budget
  guard that priced the *average* output would be a guard that lets the tail
  through. `CostEstimate.output_tokens_bounded` records which of the two any given
  number is, so a report can never present an upper bound as a measurement.

### 4. A hard max-cost guard

`CostLedger` refuses a call whose worst-case estimated cost would push cumulative
spend past the ceiling. Three properties matter:

- **The ceiling defaults to `0.0`.** An opted-in adapter with no configured budget
  can spend nothing at all. The failure mode "I meant to set it and forgot" must
  cost nothing, so the unset value is the strictest one, not the loosest.
- **It refuses; it does not truncate.** Silently lowering `max_tokens` to fit a
  budget changes the experiment, and a harness that quietly changes the experiment
  to stay in budget is worse than one that stops.
- **It is cumulative and checked before the call.** Twenty cases each individually
  under the ceiling cannot collectively breach it. The refusal happens before the
  transport is reached, so an over-budget suite makes *zero* calls, not
  *n − 1*.

Actual spend is settled from the provider's reported usage after the call, so the
ledger tracks what was *billed* while the guard reasons about what *could* be.

### 5. A keyed, gitignored response cache

`cache_key` hashes exactly the fields that determine the provider's answer: model
id, prompt, structured input, the output-token bound, and the prompt / tool
versions. It omits `case_id` (two cases with an identical request must share one
response), `app_version` (a label this repository attaches, invisible to the
provider) and `timestamp` (which would make every key unique and the cache
useless). Getting this set wrong in either direction is expensive: too many fields
and you re-spend on every run; too few and you serve a stale answer for a changed
prompt.

- **A cache hit consumes no budget** but still reports what the call cost when it
  was made. That is the case's cost, and the comparison engine is right to see it;
  the ledger is untouched because *this* run spent nothing. Conflating the two
  would mean a completed suite could not be re-run under the ceiling it already
  paid for.
- **A corrupt entry is refused, not read as a miss.** A miss re-spends, and a
  corrupt file is not evidence the call was never made. Writes are atomic
  (temp file, then rename), so the reader should never see one.
- `.api_cache/` is gitignored: it holds real provider responses, which `CLAUDE.md`
  forbids committing.

## Prices are configuration, not facts baked into source

Vendor pricing changes; a constant in a Python file does not. `PriceList` is
loaded from JSON and carries its own provenance:

```json
{
  "verified": false,
  "priced_at": "",
  "note": "PLACEHOLDER PRICES - not any vendor's pricing",
  "models": {
    "claude-opus-4-8": {"input_usd_per_1k": 0.005, "output_usd_per_1k": 0.025}
  }
}
```

`verified` means *a human checked these against the provider's published prices
on* `priced_at`. It is `false` for every price list committed to this repository,
because this repository cannot check a price: it has no network, and a number
copied into source on the day it was written is a number that silently rots. A
`verified: true` list with no `priced_at` is rejected — a "verified" claim with no
date cannot go stale, which is the problem.

Estimating and comparing with an unverified list is fine: both sides of a delta
use the same prices, so the delta is sound regardless. **Spending** against one is
refused, unless the operator sets `allow_unverified_pricing` — an explicit
statement that they know the ceiling is being enforced against numbers nobody
checked.

`config/api_pricing.example.json` ships unverified, with nonzero placeholder
prices (a zero-priced example would make every estimate `$0.00` and teach
nothing) and real Claude model identifiers shown as examples of the key format —
not as a claim about what any of them costs.

## What a transport may not invent

A response reporting zero tokens, or zero latency, is **refused**. This is the
rule Milestone 10's importers established (`docs/INTEGRATION_CONTRACTS.md`): a
missing measurement recorded as `0.0` is not missing, it is a *number*, and the
comparison engine believes numbers. A zero-latency baseline makes every candidate
an unbounded `latency_regression`. Cost cannot be settled from usage that was
never reported either, so the ledger would silently under-count the spend it
exists to cap.

## The network client that isn't here

`adapters/api_transport.py` is the seam where a real provider client goes. It
holds a documented reference implementation and **nothing imports it**:

- `api_stub.py` does not import it, so `import llm_regression_observability`
  cannot pull a network client into the process.
- `adapters/__init__.py` does not re-export it, so `from ...adapters import *`
  cannot either.
- The client is imported *inside* the function, so even importing the transport
  module does not import `anthropic`.
- `anthropic` is not a dependency of this package and is not installed by the test
  environment.

`tests/test_api_stub.py` asserts all four by **parsing the package's own source**
with `ast`, not by watching `sys.modules` — a runtime check only sees the imports
that happened to run in that process. The scanner itself is unit-tested against
known-bad inputs (top-level import, `try:`-wrapped import, `if:`-wrapped import)
so it cannot silently match nothing and make every assertion below it vacuous.

The transport function has **no test coverage and never runs in CI**, because
running it would cost money. It is the documented shape of the thing, not a
load-bearing part of the system.

## Determinism

Nothing in the guard ladder reads a clock, an RNG, or a socket. `stored_at` on a
cache entry is the run's recorded timestamp. Latency is whatever the transport
reports.

A live call is inherently nondeterministic — that is a property of calling a real
API, not of this code — and it is confined to the transport at the very bottom of
the ladder, behind all seven gates, in a module nothing imports by default. The
`perf_counter` that measures its latency is imported inside the function body for
the same reason the client is: it is the one place where the ambient value *is*
the measurement being taken.

## Estimating without any of this

`llmreg estimate-cost` prices a suite it never runs. It is a calculator: it opts
in to nothing, opens no cache, and cannot reach a provider. It exits `1` if the
bound exceeds `--max-cost-usd`, so it can gate a job before the job can spend.

```bash
llmreg estimate-cost --suite evals/suites/smoke.yaml \
    --pricing config/api_pricing.example.json \
    --model claude-opus-4-8 --max-cost-usd 0.01
```

```
suite            : smoke (8 cases)
model            : claude-opus-4-8
pricing          : config/api_pricing.example.json (UNVERIFIED)
                   estimates only; the API adapter refuses to spend against prices nobody has checked
input tokens     : 239 (estimated, ~4 chars/token)
output tokens    : 8192 (upper bound: max_output_tokens)
cost upper bound : $0.205995
max-cost ceiling : $0.010000
within ceiling   : no
api calls        : 0 (this command cannot make one)
result           : FAIL
```

The ceiling it reports is the one the guarded adapter would enforce, computed by
the same estimator — so a number printed here and a number a guard refuses on
cannot disagree.

## Wiring it up, if you must

```python
from llm_regression_observability.adapters import ApiAdapterStub, ApiGuard
from llm_regression_observability.adapters.api_transport import anthropic_transport
from llm_regression_observability.pricing import load_price_list

adapter = ApiAdapterStub(
    guard=ApiGuard(enabled=True, dry_run=False, max_cost_usd=0.50),
    model_id="claude-sonnet-5",
    pricing=load_price_list("config/api_pricing.json"),  # yours, verified
    transport=anthropic_transport(),                     # requires `anthropic`
)
```

Four deliberate acts: opt in, disable dry-run, set a ceiling, supply a verified
price list — plus install a dependency this project does not declare and pass a
transport it does not ship. That is the intended amount of friction.

## Limitations

- The token estimator is `chars / 4`. Real bills are computed by a real tokenizer,
  and the two disagree. See `docs/LIMITATIONS.md`.
- The ceiling is enforced against *estimated* cost. If a supplied price list is
  wrong, the ceiling is wrong by the same factor. `verified` / `priced_at` make
  that an operator decision rather than a silent one, but they cannot check a
  price.
- A cache hit is keyed on request content, not on time. A provider that changes a
  model's behavior behind a stable model id will be served the old response until
  the cache is cleared. This is the correct trade for reproducibility and the
  wrong one for freshness; clear `.api_cache/` when you want the latter.
- Nothing here has ever been run against a real provider inside this repository.
  The transport is a reference implementation, and it is untested by construction.
