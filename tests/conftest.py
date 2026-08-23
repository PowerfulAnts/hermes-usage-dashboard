"""Shared test infrastructure for hermes-usage-dashboard.

THE CORE MECHANISM — USAGE_DASH_HOME
------------------------------------
backend/adapters/_util.py::home() returns ``$USAGE_DASH_HOME`` when set,
else the real user home. Every adapter resolves its paths through home()
AT SCAN TIME (never at import time), so redirecting that env var redirects
every adapter to a synthetic fixture tree.

The autouse ``isolated_home`` fixture below points USAGE_DASH_HOME at a
fresh tmp_path for EVERY test, so the suite never reads or writes real
machine data and tests cannot interfere with each other. It also clears
the ``sources._cache`` memo (300s TTL would otherwise leak results between
tests) and resets ``sources.REGISTRY_ERRORS``.

Fixtures provided here
----------------------
isolated_home   (autouse) USAGE_DASH_HOME -> tmp_path, caches cleared.
make_home       Factory: make_home() -> fixture-root dir;
                make_home('.codex', 'sessions') -> nested dir (created).
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(REPO_ROOT, "backend")
for _p in (BACKEND_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sources  # noqa: E402  (backend/sources.py, path set up above)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test sees a pristine synthetic home and empty caches."""
    monkeypatch.setenv("USAGE_DASH_HOME", str(tmp_path))
    sources._cache.clear()
    sources.REGISTRY_ERRORS = []
    yield
    # Belt & braces: don't let a cached result survive into the next test
    # even if a future fixture forgets cleanup.
    sources._cache.clear()


@pytest.fixture
def make_home(tmp_path):
    """Factory for synthetic home trees rooted at this test's tmp_path.

    Usage:
        root = make_home()                      # the USAGE_DASH_HOME dir
        sess = make_home('.codex', 'sessions')  # nested dir, created on demand
    """

    def _make(*parts: str):
        p = tmp_path.joinpath(*parts) if parts else tmp_path
        p.mkdir(parents=True, exist_ok=True)
        return p

    return _make
