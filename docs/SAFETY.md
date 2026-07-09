# Safety and privacy

This project is a **reliability/evaluation framework**. It is not a medical,
legal, or security authority, and it makes no safety certification of any model
or application.

## Data scope

- **All fixtures are synthetic.** Prompts, model outputs, tool traces, and
  retrieval documents are authored for this repository. There is **no real user
  data** anywhere in the repo.
- Fixtures are **small and safe**. Any biomedical- or security-adjacent example
  is kept non-clinical, non-actionable, and non-exploitative — it exists only to
  exercise the refusal/boundary and evidence scorers, never to provide real
  guidance.
- The refusal/boundary suite uses prompts where the *expected* behavior is stable
  and benign; it tests whether a change shifts refusal behavior, not whether the
  model should comply with anything harmful.

## Redaction: the mask and the gate

Two mechanisms, one pattern table. `traces/redaction.py` is the single definition of
"credential-like" in this repository; `reports/base.py` (the report gate),
`datasets/validation.py` (`llmreg validate-fixtures`), `observability/spans.py` (the
span writer) and `executor.py` (the trace writer) all read it from there. A second
copy of a pattern table is a false sense of safety, because the copy that misses a
pattern is the one that runs.

**The mask.** `redact_text()` replaces a match with `[REDACTED:<rule>]`. It runs on
every content field of a `RunRecord` inside `execute_suite` — before tokens are
accounted and before `run.jsonl` is hashed — and on every free-text field the report
generator copies out of a comparison artifact. The placeholder matches no rule, so
redaction is idempotent.

**The gate.** `assert_redaction_clean` scans the fully rendered report immediately
before the bytes hit the disk and is **fail-closed**: a credential-like string that
reached the document means the report is *refused*, not written. `llmreg report
--dry-run` runs the same gate, so a dry run cannot pass where a real write would
refuse. `write_spans` gates `spans.jsonl` the same way.

The two are not redundant. The mask sees only *free text*, because identifiers —
case ids, run ids, metric names, tool names — are join keys, and rewriting one would
corrupt the artifact it claims to protect. The gate reads the whole document,
identifiers included. A secret in an explanation is masked; a secret in a run id is
refused. Neither ever echoes what it found: an error message is a place secrets leak
too.

### Two tiers: refuse on evidence, mask on suspicion

| Tier | Rules | Behavior |
| --- | --- | --- |
| `certain` | `sk-…`, `AKIA…`, `gh[pousr]_…`, `AIza…`, `xox[baprs]-…`, JWT, private-key header, `Bearer …`, `Authorization: …` | mask; **and** refuse an artifact that still contains one |
| `heuristic` | credential-named assignment (`api_key=…`), email address, generic high-entropy token | mask only — **never** blocks an artifact |

A heuristic rule can fire on legitimate text, and a false-positive refusal is a denial
of service on your own report. A false-positive mask costs one mangled string, and
`redaction_status` records that it happened. The generic-token rule additionally
requires mixed character classes, which is what keeps it off the SHA-256 digest this
project writes into every `run_meta.json`.

### `redaction_status`

Derived by the redactor, never believed from an adapter. The values form a lattice —
`clean < redacted < flagged` — and redaction takes the maximum of what it found and
what the record already claimed, so the status can only escalate.

- `clean` — no rule fired.
- `redacted` — a rule fired in a content field and was masked.
- `flagged` — a certain-tier rule fired in an **identity field** (a join key the
  redactor will not rewrite). The executor **refuses the run**, naming the field and
  the pattern. A `flagged` record therefore never appears in a file this project
  wrote; it is reachable only on a trace imported from elsewhere.

Independently of all of this: a comparison artifact carries no model-generated prose
to begin with. Flag explanations, per-case summaries and cluster labels are authored
by the rules from metric names, numbers, tool names and property names — never by
copying a model's output. The report renders only those, so the gate is a backstop,
not the only line of defence.

## What is never committed

Secrets, API keys, real API responses, private data, `.env` files, large logs,
response caches, model weights, and `.claude/settings*.json` are all
`.gitignore`d.

The repository-hygiene check scans the committed tree for what slipped through
anyway:

```bash
python3 scripts/08_check_repo_hygiene.py     # exits 1 on any finding
llmreg scan-secrets [PATH]                    # the same scan, from the CLI
```

It applies the **certain** tier only. The heuristic tier would flag every SHA-256
digest in `docs/` and every author email in `pyproject.toml`, and a hygiene check that
cries wolf is a hygiene check that gets skipped. A file that must legitimately name a
credential shape — a test asserting the scanner works — marks the line with
`redaction:allow`. The scan reports the file, the line and the pattern name, never the
matched value.

## Optional paid-API mode

**No paid API is used by default or in any test**, and no CLI subcommand in this
repository can spend money. The package contains no network client: `anthropic` is
not a dependency, nothing imports it, and `tests/test_api_stub.py` asserts that by
parsing the package's own source with `ast` (a runtime check would only see the
imports that happened to run). Offline is enforced rather than assumed — an autouse
fixture in `conftest.py` disables outbound TCP for all 913 tests.

The guarded adapter (`adapters/api_stub.py`, Milestone 11) passes every call
through seven gates in a fixed order, each refusing for its own reason:

1. **explicit opt-in** — first, so a disabled adapter does not even stat a file;
2. **response cache** — a hit spends nothing and consumes no budget;
3. **token/cost estimator** — an unpriced model refuses rather than defaulting;
4. **dry-run mode** — the default, even once opted in;
5. **verified pricing** — a ceiling over unchecked prices is refused;
6. **hard max-cost guard** — refuses *before* the transport, cumulatively, and
   never truncates the request to fit a budget;
7. **transport** — absent by default.

Credentials are never read, stored, logged, or cached by this package: the
reference transport defers to the provider SDK's own key resolution. The response
cache (`.api_cache/`) is gitignored. Real API responses are never committed. See
[`API_ADAPTER.md`](API_ADAPTER.md) for the reasoning behind the gate order, and
[`LIMITATIONS.md`](LIMITATIONS.md) for what the ceiling cannot guarantee.

## Honest framing

Where a metric is approximate (token/cost estimation) or a placeholder
(explanation-quality scoring), the documentation and the code say so. This system
measures *relative* behavioral change on *synthetic* fixtures. It does not
validate correctness, safety, or fitness for any real-world use.
