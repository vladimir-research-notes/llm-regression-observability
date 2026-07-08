"""llm-regression-observability.

A CPU-first framework for detecting, explaining, and reporting regressions in
LLM applications and agentic systems. Model / prompt / tool / deployment
versions go in; evaluation traces, observability metrics, and regression
reports come out.

The core package is intentionally **dependency-free** (Python 3.12 standard
library only). Optional extras (``pydantic``, ``pandas``) are never required to
run the package or its tests. See ``docs/PLAN.md`` for the architecture and the
milestone-by-milestone build plan.
"""

from __future__ import annotations

__version__ = "0.1.0"

# Public metadata, kept here as the single source of truth (``pyproject.toml``
# reads ``__version__`` dynamically so the two never drift).
__title__ = "llm-regression-observability"
__summary__ = (
    "CPU-first framework for detecting, explaining, and reporting regressions "
    "in LLM applications and agentic systems."
)

__all__ = ["__version__", "__title__", "__summary__"]
