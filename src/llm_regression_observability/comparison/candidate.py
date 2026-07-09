"""Loading the **candidate** side, and deciding whether it may be compared at all.

A candidate run is loaded exactly like a baseline (:func:`.baseline.load_run_side`);
what belongs here is the normalization that only exists once there are two runs.

**Same suite, or no comparison.** Two runs of different suites share nothing but
a file format. Pairing them by ``case_id`` would silently compare unrelated cases,
so a suite mismatch is an error, not a warning.

**Same price list, or no cost delta.** ``cost_estimate_usd`` is an estimate
produced by the executor from a token count and a :class:`~..executor.CostModel`.
A baseline priced at one rate and a candidate priced at another differ in cost
even when they emit identical tokens. Reporting that as a ``cost_regression``
would be an artifact of two price lists, not of the change under test — the exact
false positive ``evaluators/cost.py`` warns about. So a cost-model mismatch does
not abort the comparison: it disables the cost rule, and
:class:`Comparability` records why, so the report can say "cost not compared"
instead of "cost did not regress".

Everything else — a different adapter, a different model id, a different
timestamp — is *expected* to differ. The candidate is the change.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .base import ComparisonError, RunSide
from .baseline import load_run_side

__all__ = ["Comparability", "check_comparable", "load_candidate"]


@dataclass(frozen=True)
class Comparability:
    """What may be compared between two loaded sides, and what may not.

    ``cost_comparable`` is ``False`` when the two runs were priced under different
    cost models. ``notes`` explains every disabled dimension in a stable order, so
    the reason lands in ``comparison_meta.json`` rather than in nothing.
    """

    suite_id: str
    cost_comparable: bool = True
    notes: tuple[str, ...] = ()


def check_comparable(baseline: RunSide, candidate: RunSide) -> Comparability:
    """Decide whether (and how far) ``candidate`` may be compared to ``baseline``.

    Raises :class:`~.base.ComparisonError` on a suite mismatch. Returns a
    :class:`Comparability` describing any dimension that had to be switched off.
    """
    if baseline.suite_id != candidate.suite_id:
        raise ComparisonError(
            f"suite mismatch: baseline ran {baseline.suite_id!r}, candidate ran "
            f"{candidate.suite_id!r}; two runs of different suites are not comparable"
        )
    if baseline.run_id == candidate.run_id:
        raise ComparisonError(
            f"baseline and candidate are the same run ({baseline.run_id!r}); "
            "a run cannot regress against itself"
        )

    notes: list[str] = []
    cost_comparable = True
    both_priced = baseline.cost_model is not None and candidate.cost_model is not None
    if both_priced and baseline.cost_model != candidate.cost_model:
        cost_comparable = False
        notes.append(
            "cost_regression disabled: the two runs were priced under different "
            f"cost models ({baseline.cost_model} vs {candidate.cost_model}); a "
            "cost delta across price lists measures the price list, not the change"
        )

    if baseline.adapter == candidate.adapter:
        notes.append(
            f"both sides were produced by the {baseline.adapter!r} adapter; the "
            "comparison is still valid, but the candidate is not an independent run"
        )

    return Comparability(
        suite_id=baseline.suite_id,
        cost_comparable=cost_comparable,
        notes=tuple(notes),
    )


def load_candidate(
    run_dir: Path | str,
    *,
    baseline: RunSide | None = None,
    root: Path | None = None,
    suite_path: Path | str | None = None,
) -> RunSide:
    """Load the side under test. When ``baseline`` is given, check comparability now.

    Checking at load time means a suite mismatch fails before any case is paired
    or scored, with a message that names both suites.
    """
    side = load_run_side(run_dir, root=root, suite_path=suite_path)
    if baseline is not None:
        check_comparable(baseline, side)
    return side
