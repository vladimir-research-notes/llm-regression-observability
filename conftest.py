"""Test path shim, and a repository-wide network kill switch.

Prepends ``src/`` to ``sys.path`` so the package imports without an editable
install. This keeps ``python3 -m pytest`` runnable on a host that has only
``pytest`` available — no build step, no network, no ``pip install`` (the build
host has minimal free disk and no guaranteed network).

``CLAUDE.md`` requires that no test reach the internet or a paid API. Milestone 11
turns that from a convention into a mechanism: :func:`_no_network` is autouse, so
**every** test in this repository runs with outbound TCP disabled. A test that
tries to open a connection fails with a clear message instead of hanging on a
socket, and the guard is armed even for code paths nobody thought to check.

The patch covers ``socket.socket.connect`` / ``connect_ex`` and
``socket.create_connection`` — every route to an outbound TCP connection in the
standard library, and therefore under any HTTP client built on it. It leaves
socket *construction* alone: constructing a socket costs nothing, and blocking it
would break unrelated standard-library machinery for no gain. ``sqlite3`` (the
only I/O-heavy stdlib module this project uses) touches no socket at all.

``tests/test_api_stub.py::test_the_no_network_guard_is_actually_armed`` asserts
this fixture is in force, so a future edit that quietly disarms it fails a test
rather than silently re-opening the door.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

NETWORK_BLOCKED_MESSAGE = (
    "network access attempted in a test: no test in this repository may reach "
    "the internet or a paid API (CLAUDE.md). See conftest.py."
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable outbound TCP for the duration of every test."""

    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(NETWORK_BLOCKED_MESSAGE)

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
