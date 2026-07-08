# Milestone 11 — Optional API adapter stub and cost guard

**Status: planned.**

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

## Definition of done

Guarded API stub with all five protections, all tests offline; docs/website
updated. Next: Milestone 12 — final portfolio report.
