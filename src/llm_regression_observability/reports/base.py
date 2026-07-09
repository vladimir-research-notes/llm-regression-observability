"""Shared plumbing for the report generator: the redaction gate, and formatting.

Two concerns live here because both renderers need them and neither may disagree
with the other.

**The redaction gate.** ``docs/PLAN.md`` §11 lists "redaction works and runs
before reports are written" as a quality gate, and ``docs/milestones/07-reports.md``
requires every string in a report to pass through redaction on its way out. The
*masking* redactor is Milestone 9 (``traces/redaction.py``). What Milestone 7 owns
is the seam and the enforcement:

* :data:`Redactor` — the seam. :func:`build_report` takes one and applies it to
  every free-text field it copies out of a comparison artifact. The default,
  :func:`identity_redactor`, masks nothing.
* :func:`assert_redaction_clean` — the enforcement, and it is **fail-closed**. It
  runs on the fully rendered document immediately before the bytes hit the disk.
  A credential-like string that survived the redactor does not get written; it
  raises :class:`ReportError`.

So today a report containing a secret is *refused*; after Milestone 9 wires a
masking redactor into the seam it will be *cleaned*, and this gate stays exactly
where it is as the last assertion. The gate never echoes what it found — it names
the pattern and the line, because an error message is a place secrets leak too.

:data:`~..datasets.validation.SECRET_PATTERNS` is imported rather than restated;
Milestone 9 moves that table to ``traces/redaction.py`` and both callers follow it
there. One table, one definition of "credential-like".

**Formatting.** A number is rendered the same way in Markdown and in HTML, so the
two documents never disagree about whether latency moved by ``+250.0 ms`` or
``+250 ms``. Nothing here reads a clock: a report is a pure function of the
comparison artifacts it is built from, and carries no generation timestamp (a
timestamp would make two reports of the same comparison differ byte for byte).
"""

from __future__ import annotations

import math
from collections.abc import Callable

from ..comparison.regression import REGRESSION_CATEGORIES, SEVERITY_ORDER
from ..datasets.validation import SECRET_PATTERNS

__all__ = [
    "ReportError",
    "Redactor",
    "identity_redactor",
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

#: A text-in, text-out masking function. Milestone 9 supplies the real one.
Redactor = Callable[[str], str]


def identity_redactor(text: str) -> str:
    """The default redactor: masks nothing.

    Milestone 7 does not own a masking implementation, and pretending otherwise
    would be the kind of claim this repository does not make. The gate below is
    what makes the default safe: nothing credential-like gets written either way,
    it just raises instead of masking.
    """
    return text


def secret_findings(text: str) -> tuple[tuple[int, str], ...]:
    """Every ``(line_number, pattern_name)`` where ``text`` looks credential-like.

    The match itself is deliberately not returned. Callers report *that* a secret
    is present and *where*; a caller that wanted the value would be reintroducing
    the leak it is trying to prevent.
    """
    found: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        found.extend(
            (lineno, name) for name, pattern in SECRET_PATTERNS if pattern.search(line)
        )
    return tuple(found)


def assert_redaction_clean(text: str, *, source: str = "report") -> None:
    """Refuse to emit ``text`` if any credential-like pattern survived redaction.

    The last thing that runs before a report is written. Fail-closed on purpose:
    a report is the one artifact a human is expected to paste into a ticket, a
    pull request, or a chat window.
    """
    findings = secret_findings(text)
    if not findings:
        return
    where = ", ".join(f"line {lineno} ({name})" for lineno, name in findings)
    raise ReportError(
        f"{source}: refusing to write a report with {len(findings)} "
        f"unredacted credential-like string(s): {where}. "
        "Pass a masking redactor (Milestone 9) or fix the source trace."
    )


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
