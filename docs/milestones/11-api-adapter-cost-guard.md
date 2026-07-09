# Milestone 11 — Optional API adapter stub and cost guard

**Status: Complete.** All five protections ship behind a seven-gate ladder; the
package contains no network client (asserted by parsing its own source), and a
repository-wide `conftest.py` fixture disables outbound TCP for all 849 tests.
97 tests in `tests/test_api_stub.py`. See [`../API_ADAPTER.md`](../API_ADAPTER.md).

## Goal

Provide a **disabled-by-default** interface for an optional real-API adapter,
with every guard required before any spend could occur. **No real API call is
made in any test.**

## Deliverables

Build out `adapters/api_stub.py` into a guarded adapter interface with:

- **explicit opt-in** — inert unless a clear flag/env is set;
- **dry-run mode** — default; estimates work without calling anything;
- **token/cost estimator** — transparent, shared with the cost evaluator;
- **hard max-cost guard** — refuses to proceed past a configured USD ceiling;
- **response cache** — keyed, gitignored, so repeated runs never re-spend;
- clear separation so the default path never imports a network client.

The concrete provider (e.g. a Claude API adapter) is documented but stays behind
the opt-in; if referenced, model ids and pricing follow the current Claude models
and are treated as configuration, not hard-coded facts.

## Validation

```bash
python3 -m pytest -q tests/test_api_stub.py
```

Tests assert: the adapter is inert by default; dry-run produces an estimate with
no network; the max-cost guard blocks an over-budget run; the cache prevents a
second call; and nothing in the default test path performs network I/O.

## Rationale

Optional paid-API support must be safe by construction. The guards are the whole
point of the milestone; the network call is not.

## What shipped

| Deliverable | Where | Proven by |
| --- | --- | --- |
| Explicit opt-in | `adapters/api_guard.py::ApiGuard` | `ApiAdapterStub()` refuses before it stats a cache file; `build_adapter("api-stub")` offers no way to opt in |
| Dry-run by default | `ApiGuard.dry_run=True` | opting in alone calls nothing; disabling dry-run is a second, separate decision |
| Token/cost estimator | `pricing.py` | one `COST_DECIMALS`, imported by the executor, the cost evaluator, the comparison engine, and the guard (was three copies kept in sync by comment) |
| Hard max-cost guard | `api_guard.py::CostLedger` | refuses *before* the transport, cumulatively, and never truncates the request to fit |
| Response cache | `adapters/api_cache.py` | a second run makes zero calls; a hit consumes no budget; a corrupt entry refuses rather than re-spending |
| No network client on the default path | `adapters/api_transport.py` | asserted by parsing the package's source with `ast`; the scanner is itself unit-tested against known-bad inputs |

Beyond the spec: `pricing.PriceList` carries `verified` / `priced_at` provenance
and the spend path **refuses an unverified price list**, because a ceiling
enforced against unchecked prices guarantees nothing. `llmreg estimate-cost`
prices a suite without opting in or calling anything. `conftest.py` disables
outbound TCP for the entire test suite, turning "no test uses the network" from a
convention into a mechanism.

## Definition of done

Guarded API stub with all five protections, all tests offline; docs/website
updated. **Done.** Next: Milestone 12 — final portfolio report.
