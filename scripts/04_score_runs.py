#!/usr/bin/env python3
"""Score a run's records with the Milestone 5 evaluators.

Reads ``results/runs/<run_id>/run.jsonl`` (written by ``llmreg run-suite``),
scores every record against its ``EvalCase``, prints a per-case table, and writes
``results/processed/<run_id>/metrics.jsonl`` — one ``MetricResult`` per line,
byte-stable.

    python3 scripts/04_score_runs.py                      # both smoke runs
    python3 scripts/04_score_runs.py --run smoke-baseline-recorded
    python3 scripts/04_score_runs.py --no-write           # print only

This scores each run **independently**. It computes no deltas and raises no
regression flags: pairing a baseline against a candidate is Milestone 6's
``llmreg compare``. Offline, CPU-only, deterministic — two invocations on the
same run artifacts produce byte-identical ``metrics.jsonl``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from llm_regression_observability.datasets import load_eval_suite  # noqa: E402
from llm_regression_observability.evaluators import (  # noqa: E402
    cost_stats,
    error_rate,
    evaluate_run,
    infra_error_rate,
    latency_stats,
    retry_total,
)
from llm_regression_observability.executor import (  # noqa: E402
    RUN_JSONL,
    RUN_META_JSON,
    RunMeta,
)
from llm_regression_observability.schemas import RunRecord  # noqa: E402
from llm_regression_observability.traces.storage import (  # noqa: E402
    read_json,
    read_jsonl,
    write_jsonl,
)

DEFAULT_RUNS = ("smoke-baseline-recorded", "smoke-candidate-scripted-regression")

# Metrics whose value is a rate or a score in [0, 1]; the rest are native units.
_SUMMARY_METRICS = (
    "task_success",
    "json_schema_validity",
    "allowed_tool_ratio",
    "tool_sequence_similarity",
    "evidence_coverage",
    "unsupported_claim_count",
    "refusal_match",
    "app_error_count",
    "retry_count",
    "trace_completeness",
)


def score_run(run_dir: Path, out_root: Path | None) -> int:
    meta: RunMeta = read_json(run_dir / RUN_META_JSON, RunMeta)
    records: list[RunRecord] = read_jsonl(run_dir / RUN_JSONL, RunRecord)
    suite = load_eval_suite(_ROOT / meta.suite_path)

    scored = evaluate_run(suite.cases, records)
    flat = [result for case_id in scored for result in scored[case_id]]

    print(f"run id           : {meta.run_id}")
    print(f"suite            : {meta.suite_id} ({meta.suite_path})")
    print(f"adapter          : {meta.adapter}")
    print(f"records scored   : {len(records)}")
    print(f"metrics emitted  : {len(flat)}")
    print()
    print(f"{'case':24} " + " ".join(f"{name[:14]:>14}" for name in _SUMMARY_METRICS))
    for record in records:
        by_name = {r.metric: r.value for r in scored[record.case_id]}
        cells = " ".join(
            f"{by_name[name]:>14.4f}" if name in by_name else f"{'-':>14}"
            for name in _SUMMARY_METRICS
        )
        print(f"{record.case_id:24} {cells}")

    latency = latency_stats(records)
    cost = cost_stats(records)
    print()
    print(f"latency p50 / p95: {latency.p50_ms:.1f} ms / {latency.p95_ms:.1f} ms")
    print(f"cost total       : ${cost.cost_total_usd:.6f} (estimate, not a bill)")
    print(f"app error rate   : {error_rate(records):.4f}")
    print(f"infra error rate : {infra_error_rate(records):.4f} (never flags)")
    print(f"retries          : {retry_total(records)}")

    if out_root is not None:
        path = write_jsonl(out_root / meta.run_id / "metrics.jsonl", flat)
        print(f"metrics          : {path}")
    else:
        print("metrics          : none written (--no-write)")
    print("result           : PASS")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--run",
        action="append",
        default=None,
        help="run id under --runs-root (repeatable; default: both smoke runs)",
    )
    parser.add_argument("--runs-root", default="results/runs")
    parser.add_argument("--out", default="results/processed")
    parser.add_argument(
        "--no-write", action="store_true", help="print the table, write nothing"
    )
    args = parser.parse_args(argv)

    runs_root = Path(args.runs_root)
    out_root = None if args.no_write else Path(args.out)
    run_ids = args.run or list(DEFAULT_RUNS)

    for i, run_id in enumerate(run_ids):
        run_dir = runs_root / run_id
        if not (run_dir / RUN_JSONL).is_file():
            print(f"error            : no {RUN_JSONL} under {run_dir}")
            print("hint             : run scripts/02_run_baseline.py first")
            print("result           : FAIL")
            return 1
        if i:
            print()
        score_run(run_dir, out_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
