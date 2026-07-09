"""Loading the **baseline** side: the reference frame a candidate is judged in.

The baseline is not merely "the first run". It fixes three things the rest of the
comparison takes for granted:

* **The suite.** ``run_meta.json`` records the manifest the run executed
  (``suite_path``, repo-relative). Resolving it here means the comparison scores
  both sides against the *same* ``EvalCase`` objects — the same
  ``expected_properties``, the same ``json_schema``, the same ``allowed_tools`` —
  rather than against whatever each side happened to be run with.
* **The thresholds.** They live on the suite manifest, so a suite can be tuned
  without touching code (``docs/REGRESSION_DETECTION.md``).
* **The reference values.** Every delta and every relative change is measured
  against this side.

A candidate run is loaded by exactly the same code path (:func:`load_run_side`);
:mod:`.candidate` adds the checks that only make sense once there is something to
compare *to*. Loading is offline and read-only: two ``json``/``jsonl`` files per
side, validated against the Milestone 1 schemas on the way in.
"""

from __future__ import annotations

from pathlib import Path

from ..datasets import load_eval_suite, repo_root
from ..executor import RUN_JSONL, RUN_META_JSON, RunMeta
from ..schemas import RunRecord, SchemaError
from ..traces.storage import read_json, read_jsonl
from .base import ComparisonError, RunSide

__all__ = ["resolve_suite_path", "load_run_side", "load_baseline"]


def resolve_suite_path(meta: RunMeta, *, root: Path | None = None) -> Path:
    """Locate the suite manifest a run was executed against.

    ``RunMeta.suite_path`` is stored repo-relative so artifacts leak no home
    directory (see ``executor._display_path``). It is resolved against the
    repository root, falling back to interpreting it as given — which is what
    happens when a run was executed from a checkout somewhere else.
    """
    root = root or repo_root()
    candidate = root / meta.suite_path
    if candidate.is_file():
        return candidate
    literal = Path(meta.suite_path)
    if literal.is_file():
        return literal
    raise ComparisonError(
        f"run {meta.run_id!r}: cannot resolve suite manifest {meta.suite_path!r} "
        f"(looked in {candidate} and {literal})"
    )


def load_run_side(
    run_dir: Path | str,
    *,
    root: Path | None = None,
    suite_path: Path | str | None = None,
) -> RunSide:
    """Load and score one run directory written by ``llmreg run-suite``.

    Reads ``run.jsonl`` and ``run_meta.json``, resolves the suite, and returns a
    scored :class:`~.base.RunSide`. ``suite_path`` overrides the manifest the run
    recorded, for the case where a suite has moved on disk.
    """
    run_dir = Path(run_dir)
    meta_path = run_dir / RUN_META_JSON
    records_path = run_dir / RUN_JSONL
    for path in (meta_path, records_path):
        if not path.is_file():
            raise ComparisonError(
                f"{run_dir}: not a run directory (missing {path.name}); "
                "generate one with `llmreg run-suite`"
            )

    try:
        meta: RunMeta = read_json(meta_path, RunMeta)
        records: list[RunRecord] = read_jsonl(records_path, RunRecord)
    except SchemaError as exc:
        raise ComparisonError(f"{run_dir}: malformed run artifact: {exc}") from exc

    if suite_path is not None:
        manifest = Path(suite_path)
    else:
        manifest = resolve_suite_path(meta, root=root)
    try:
        suite = load_eval_suite(manifest)
    except (SchemaError, OSError) as exc:
        raise ComparisonError(
            f"{run_dir}: cannot load suite {manifest}: {exc}"
        ) from exc

    # The executor forces one ``run_id`` onto every record in a run; a stream
    # that disagrees is a hand-edited or concatenated artifact, not a run.
    stray = sorted({r.run_id for r in records} - {meta.run_id})
    if stray:
        raise ComparisonError(
            f"{records_path}: records carry run_id(s) {stray} but run_meta.json "
            f"says {meta.run_id!r}"
        )

    return RunSide.from_records(
        run_id=meta.run_id,
        app_version=meta.app_version,
        suite_id=meta.suite_id,
        adapter=meta.adapter,
        cases={case.case_id: case for case in suite.cases},
        records=records,
        suite_path=meta.suite_path,
        thresholds=suite.thresholds,
        cost_model=meta.cost_model.to_dict(),
    )


def load_baseline(
    run_dir: Path | str,
    *,
    root: Path | None = None,
    suite_path: Path | str | None = None,
) -> RunSide:
    """Load the reference side of a comparison from ``results/runs/<run_id>/``."""
    return load_run_side(run_dir, root=root, suite_path=suite_path)
