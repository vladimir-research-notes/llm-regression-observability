"""Test path shim.

Prepends ``src/`` to ``sys.path`` so the package imports without an editable
install. This keeps ``python3 -m pytest`` runnable on a host that has only
``pytest`` available — no build step, no network, no ``pip install`` (the build
host has minimal free disk and no guaranteed network).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
