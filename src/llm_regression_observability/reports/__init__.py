"""Comparison records in, an actionable report out (Milestone 7).

A regression tool is only useful if a human can act on its output in seconds, so
this package is the product's surface. It reads the three artifacts
``llmreg compare`` wrote — and nothing else — and renders them as Markdown or as
one self-contained HTML file.

=====================  ======================================================
:mod:`.base`           the redaction gate, ordering, and number formatting
:mod:`.model`          artifacts → :class:`~.model.ReportModel` (selection)
:mod:`.markdown`       ``ReportModel`` → Markdown
:mod:`.html`           ``ReportModel`` → one self-contained HTML file
=====================  ======================================================

Four properties this package holds to:

* **A report is a pure function of a comparison directory.** No clock, no RNG, no
  network, and no generation timestamp — so two reports of the same comparison are
  byte-identical, and a report can be regenerated from artifacts alone.
* **It can only say what the artifacts say.** The selection logic lives in one
  place (:mod:`.model`), so Markdown and HTML can never disagree, and neither can
  reach past ``comparison.jsonl`` for a fact.
* **Nothing is written until it is redaction-clean.** Free text is masked on the way
  into the :class:`~.model.ReportModel` by the Milestone 9 redactor, and
  :func:`write_report` then runs :func:`~.base.assert_redaction_clean` on the fully
  rendered document — identifiers included — and raises rather than emit a
  credential-like string. Mask first, gate last; the gate is what makes the
  guarantee, the mask is what keeps it from costing you the report.
* **The silences are printed.** Unpaired cases, infra-attributed cases, and
  withheld flags get their own section. A clean headline over a comparison that
  could not look at half its cases is the failure mode this whole project exists
  to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..traces.storage import ENCODING, NEWLINE
from . import base, html, markdown, model
from .base import (
    Redactor,
    ReportError,
    assert_redaction_clean,
    identity_redactor,
    masking_redactor,
    secret_findings,
)
from .html import render_html
from .markdown import render_markdown
from .model import (
    DEFAULT_TOP,
    LIMITATIONS,
    CategoryRow,
    CostSection,
    Example,
    FlagRow,
    LatencySection,
    MoveRow,
    ReportModel,
    build_report,
    load_comparison,
    load_report_model,
)

__all__ = [
    "base",
    "model",
    "markdown",
    "html",
    # Errors and the redaction seam
    "ReportError",
    "Redactor",
    "identity_redactor",
    "masking_redactor",
    "secret_findings",
    "assert_redaction_clean",
    # Model
    "ReportModel",
    "FlagRow",
    "CategoryRow",
    "MoveRow",
    "Example",
    "LatencySection",
    "CostSection",
    "LIMITATIONS",
    "DEFAULT_TOP",
    "load_comparison",
    "build_report",
    "load_report_model",
    # Rendering and writing
    "FORMATS",
    "FORMAT_SUFFIX",
    "DEFAULT_REPORT_NAME",
    "render",
    "render_markdown",
    "render_html",
    "report_path",
    "write_report",
    "ReportResult",
    "generate_report",
]

#: ``llmreg report --format`` choices. ``both`` expands to the two below.
FORMATS: tuple[str, ...] = ("markdown", "html")

FORMAT_SUFFIX: dict[str, str] = {"markdown": ".md", "html": ".html"}

#: Used when ``--out`` names a directory rather than a file.
DEFAULT_REPORT_NAME = "regression_report"

_RENDERERS = {"markdown": render_markdown, "html": render_html}

#: Accepted spellings of a Markdown file, so ``--out report.markdown`` is not
#: silently rewritten to ``report.md``.
_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})


def render(report: ReportModel, fmt: str = "markdown") -> str:
    """Render ``report`` in ``fmt``. Pure: writes nothing, gates nothing."""
    try:
        renderer = _RENDERERS[fmt]
    except KeyError:
        raise ReportError(
            f"unknown report format {fmt!r}; expected one of {sorted(_RENDERERS)}"
        ) from None
    return renderer(report)


def report_path(out: Path | str, fmt: str) -> Path:
    """Resolve ``--out`` for one format.

    A directory gets ``regression_report.<ext>``; a path already carrying the
    right suffix is used verbatim; anything else has the suffix replaced — so
    ``--out r.md --format both`` writes ``r.md`` and ``r.html``.
    """
    if fmt not in FORMAT_SUFFIX:
        raise ReportError(
            f"unknown report format {fmt!r}; expected one of {sorted(FORMAT_SUFFIX)}"
        )
    path = Path(out)
    suffix = FORMAT_SUFFIX[fmt]
    if path.is_dir():
        return path / f"{DEFAULT_REPORT_NAME}{suffix}"
    accepted = _MARKDOWN_SUFFIXES if fmt == "markdown" else {suffix}
    if path.suffix in accepted:
        return path
    return path.with_suffix(suffix)


def write_report(report: ReportModel, out: Path | str, fmt: str = "markdown") -> Path:
    """Render, **gate**, then write. Returns the path actually written.

    The gate is the last thing between a rendered document and the disk. A report
    that still carries a credential-like string is not written and not returned;
    it raises :class:`~.base.ReportError`.
    """
    path = report_path(out, fmt)
    text = render(report, fmt)
    assert_redaction_clean(text, source=str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=ENCODING, newline=NEWLINE)
    return path


@dataclass
class ReportResult:
    """One built report, its rendered documents, and where they were written."""

    report: ReportModel
    documents: dict[str, str] = field(default_factory=dict)
    paths: dict[str, Path] = field(default_factory=dict)

    @property
    def written(self) -> bool:
        return bool(self.paths)


def generate_report(
    comparison_dir: Path | str,
    out: Path | str | None = None,
    *,
    formats: tuple[str, ...] = ("markdown",),
    top: int | None = DEFAULT_TOP,
    redactor: Redactor = masking_redactor,
) -> ReportResult:
    """Load a comparison directory, render it, and (unless ``out`` is ``None``)
    write one file per format. ``out=None`` is the dry-run path: everything is
    rendered and gated, nothing is written."""
    if not formats:
        raise ReportError("no report format requested")
    unknown = sorted(set(formats) - set(FORMATS))
    if unknown:
        raise ReportError(
            f"unknown report format(s) {unknown}; expected {sorted(FORMATS)}"
        )

    built = load_report_model(comparison_dir, top=top, redactor=redactor)
    result = ReportResult(report=built)
    for fmt in formats:
        text = render(built, fmt)
        # Gate on the dry-run path too: a `--dry-run` that passes where the real
        # write would refuse is a dry run that tested nothing.
        assert_redaction_clean(text, source=f"{built.comparison_id} ({fmt})")
        result.documents[fmt] = text
    if out is None:
        return result
    for fmt in formats:
        result.paths[fmt] = write_report(built, out, fmt)
    return result
