"""Shared plumbing for the report generator: the redaction gate, and formatting.

Two concerns live here because both renderers need them and neither may disagree
with the other.

**The redaction gate.** ``docs/PLAN.md`` §11 lists "redaction works and runs
before reports are written" as a quality gate, and ``docs/milestones/07-reports.md``
requires every string in a report to pass through redaction on its way out. The
mechanism is a seam plus an enforcement, and Milestone 9 filled the seam:

* :data:`Redactor` — the seam. :func:`build_report` takes one and applies it to
  every free-text field it copies out of a comparison artifact. The default is now
  :func:`~..traces.redaction.redact_text`, which **masks**.
  :func:`identity_redactor` (mask nothing) remains, and is what a test passes to
  prove the gate below still holds on its own.
* :func:`assert_redaction_clean` — the enforcement, and it is **fail-closed**. It
  runs on the fully rendered document immediately before the bytes hit the disk.
  A credential-like string that survived the redactor does not get written; it
  raises :class:`ReportError`.

The two are not redundant. The redactor sees only *free text* — flag explanations,
per-case summaries, cluster labels — because identifiers (case ids, run ids, metric
names) are join keys and rewriting one would corrupt the report it protects. The
gate reads the whole rendered document, identifiers included, and refuses. So a
secret in an explanation is *masked*; a secret in a run id is *refused*. The gate
never echoes what it found — it names the pattern and the line, because an error
message is a place secrets leak too.

The gate refuses on the certain tier only (:data:`~..traces.redaction.CERTAIN_RULES`);
heuristic rules mask but never block. See ``traces/redaction.py`` for why that
asymmetry is deliberate. That module owns the one pattern table; nothing is restated
here.

**Formatting.** A number is rendered the same way in Markdown and in HTML, so the
two documents never disagree about whether latency moved by ``+250.0 ms`` or
``+250 ms``. Nothing here reads a clock: a report is a pure function of the
comparison artifacts it is built from, and carries no generation timestamp (a
timestamp would make two reports of the same comparison differ byte for byte).
"""

from __future__ import annotations

import math

from ..comparison.regression import REGRESSION_CATEGORIES, SEVERITY_ORDER
from ..traces.redaction import Redactor, assert_clean, redact_text, secret_findings

__all__ = [
    "ReportError",
    "Redactor",
    "identity_redactor",
    "masking_redactor",
    "secret_findings",
    "assert_redaction_clean",
    "CATEGORY_ORDER",
    "severity_rank",
    "flag_sort_key",
    "fmt_value",
    "fmt_delta",
    "fmt_ms",
    "fmt_ms_delta",
    "fmt_usd",
    "fmt_usd_delta",
    "fmt_pct",
    "one_line",
]


class ReportError(RuntimeError):
    """Raised when a report cannot be built, or must not be written."""


# --------------------------------------------------------------------------- #
# Redaction: the seam, and the gate
# --------------------------------------------------------------------------- #

#: The redactor the report generator uses unless a caller substitutes one.
masking_redactor: Redactor = redact_text


def identity_redactor(text: str) -> str:
    """A redactor that masks nothing.

    Retained after Milestone 9 for one reason: it is how a test asks "would the gate
    have caught this on its own?". Passing it to :func:`build_report` disables the
    mask and leaves only the fail-closed gate, which is the property under test.
    """
    return text


def assert_redaction_clean(text: str, *, source: str = "report") -> None:
    """Refuse to emit ``text`` if any credential-like pattern survived redaction.

    The last thing that runs before a report is written. Fail-closed on purpose:
    a report is the one artifact a human is expected to paste into a ticket, a
    pull request, or a chat window. Raises :class:`ReportError` rather than
    :class:`~..traces.redaction.RedactionError` so the CLI's report path keeps one
    error type for "this report cannot be produced".
    """
    assert_clean(text, source=source, error=ReportError)


# --------------------------------------------------------------------------- #
# Ordering
# --------------------------------------------------------------------------- #

#: Taxonomy position per category, so every table in every renderer agrees.
CATEGORY_ORDER: dict[str, int] = {
    category: index for index, category in enumerate(REGRESSION_CATEGORIES)
}


def severity_rank(severity: str | None) -> int:
    """``critical`` > ``warn`` > ``info`` > nothing. Unknown severities sort last."""
    if severity is None:
        return -1
    return SEVERITY_ORDER.get(severity, -1)


def flag_sort_key(case_id: str, category: str, severity: str) -> tuple[int, int, str]:
    """Worst first: severity descending, then taxonomy order, then case id.

    Ties break on a stable, content-derived key rather than on the order the
    engine happened to emit, so the "worst regressions" table is byte-stable.
    """
    return (-severity_rank(severity), CATEGORY_ORDER.get(category, len(CATEGORY_ORDER)),
            case_id)


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #


def fmt_value(value: float) -> str:
    """A metric value: ``1``, ``0.666667``, ``0.0005``, ``250``.

    Never scientific notation and never truncated to fewer decimals than the
    engine rounds to (``comparison.base.DELTA_DECIMALS`` is 6). ``%g`` would print
    ``1.66667`` for an ``observed`` of ``1.666667``, silently disagreeing with the
    artifact a reader is about to open.
    """
    if not math.isfinite(value):
        return str(value)
    if value == int(value):
        return str(int(value))
    text = f"{value:.6f}".rstrip("0")
    return f"{text}0" if text.endswith(".") else text


def fmt_delta(value: float) -> str:
    """A unitless delta: ``0`` when it did not move, otherwise signed."""
    if value == 0:
        return "0"
    sign = "+" if value > 0 else "-"
    return f"{sign}{fmt_value(abs(value))}"


def fmt_ms(value: float) -> str:
    return f"{value:.1f} ms"


def fmt_ms_delta(value: float) -> str:
    """``+250.0 ms`` / ``-3.5 ms``; an unsigned ``0.0 ms`` when nothing moved."""
    return f"{value:.1f} ms" if value == 0 else f"{value:+.1f} ms"


def fmt_usd(value: float) -> str:
    return f"${value:.6f}"


def fmt_usd_delta(value: float) -> str:
    """``+$0.001200`` / ``-$0.000050`` — the sign leads, the unit follows.

    A zero delta is printed unsigned: ``+$0.000000`` reads like a rounded-down
    increase, which is precisely the ambiguity a cost report must not have.
    """
    if value == 0:
        return f"${abs(value):.6f}"
    sign = "-" if value < 0 else "+"
    return f"{sign}${abs(value):.6f}"


def fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def one_line(text: str) -> str:
    """Collapse whitespace so a value cannot break a table row."""
    return " ".join(text.split())
