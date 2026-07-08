# Milestone 10 — Integration contracts

**Status: planned.**

## Goal

Define — but do not depend on — trace import/export contracts so this framework
can later consume traces from sibling systems.

## Deliverables

- A documented, versioned import/export schema (`docs/` + a
  `datasets/versioning.py` / importer stub) that maps external trace shapes to
  this repo's `RunRecord`/span model.
- Adapter/importer **contracts** (interfaces + example fixtures) for:
  - a code-agent runtime (see read-only `~/claude/code-agent-runtime`);
  - BioAgentBench;
  - BioScientist OS;
  - agent-control-bench;
  - optional Claude API traces.
- A tiny synthetic example of an imported external trace being scored by the
  existing evaluators unchanged.

**None of those repositories are required** to build or test this one. Contracts
are validated against committed synthetic examples only.

## Validation

```bash
python3 -m pytest -q tests/test_integration_contracts.py
```

Tests assert a synthetic external trace imports into a valid `RunRecord` and runs
through the existing comparison/report path.

## Rationale

The project is a foundation for later integration. Defining stable contracts now
keeps that future cheap without adding real dependencies today.

## Definition of done

Import contracts documented and exercised on synthetic examples; no external repo
required; docs/website updated. Next: Milestone 11 — API adapter stub.
