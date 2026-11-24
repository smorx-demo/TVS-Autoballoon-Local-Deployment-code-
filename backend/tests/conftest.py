"""Test isolation: general-tolerance rules and settings never touch the real data files, and no
test may open a network connection to anything but this machine (local-only installation)."""

from __future__ import annotations

import os
import socket

import pytest

_LOCAL = {"127.0.0.1", "localhost", "::1", "testserver"}
OUTBOUND_ATTEMPTS: list[str] = []


@pytest.fixture(autouse=True, scope="session")
def _no_outbound_network():
    """Fail any attempt to reach a non-local host; the attempts are recorded for assertions."""
    real_connect, real_gai = socket.socket.connect, socket.getaddrinfo

    def connect(self, addr):
        host = addr[0] if isinstance(addr, tuple) else str(addr)
        if host not in _LOCAL:
            OUTBOUND_ATTEMPTS.append(host)
            raise OSError(f"test blocked outbound connection to {host}")
        return real_connect(self, addr)

    def getaddrinfo(host, *a, **k):
        if host is not None and str(host) not in _LOCAL:
            OUTBOUND_ATTEMPTS.append(str(host))
            raise OSError(f"test blocked DNS lookup of {host}")
        return real_gai(host, *a, **k)

    socket.socket.connect, socket.getaddrinfo = connect, getaddrinfo
    os.environ.pop("SMORX_ALLOW_EXTERNAL_AI", None)
    os.environ.pop("SMORX_ALLOW_CLOUD_STORAGE", None)
    yield
    socket.socket.connect, socket.getaddrinfo = real_connect, real_gai


def _isolate(mp: pytest.MonkeyPatch, d) -> None:
    from app.api.routes import app_settings
    from app.services.general_tolerance import store

    mp.setattr(app_settings, "JSON_PATH", d / "app_settings.json")
    mp.setattr(store, "JSON_PATH", d / "general_tolerance_rules.json")
    mp.setattr(store, "_use_pg", lambda: False)
    mp.setattr(app_settings, "_use_pg", lambda: False)


@pytest.fixture(autouse=True, scope="session")
def _session_store(tmp_path_factory):
    """Session-wide (module-scoped fixtures such as `built` run before function fixtures)."""
    mp = pytest.MonkeyPatch()
    _isolate(mp, tmp_path_factory.mktemp("gt_store"))
    yield
    mp.undo()


@pytest.fixture(autouse=True)
def _isolated_general_tolerance_store(tmp_path, monkeypatch):
    """Each test starts with an empty rule / settings store."""
    _isolate(monkeypatch, tmp_path)
    yield
