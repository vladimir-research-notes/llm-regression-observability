"""``python3 -m llm_regression_observability`` entry point.

Delegates to the ``llmreg`` CLI so the package is runnable without an install
(e.g. ``PYTHONPATH=src python3 -m llm_regression_observability --help``).
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
