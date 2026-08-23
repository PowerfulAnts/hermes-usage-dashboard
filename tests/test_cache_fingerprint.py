"""Cache freshness: fingerprint changes must invalidate a live TTL cache.

Guards the fix for "codex usage does not update appropriately": within the
300s TTL an adapter whose input files changed (fingerprint differs) must be
RESCANNED on the next collect_all, while an unchanged fingerprint keeps
serving the memoized result.
"""

from __future__ import annotations

import types

import sources


def _codex_like(total: int):
    """Fake adapter shaped like the codex one (heavy scan + fingerprint)."""
    m = types.ModuleType("fake_codex")
    m.NAME = "fakecodex"
    m.LABEL = "Fake Codex"
    m.ORDER = 10

    def scan(days=30):
        return {
            "available": True, "days": days,
            "totals": {"input": total, "output": 0, "cached": 0,
                       "total": total},
            "daily": {}, "models": {}, "meta": {"scanned_at_total": total},
        }

    calls = {"n": 0}

    def wrapped(days=30):
        calls["n"] += 1
        return scan(days)

    m.scan = wrapped
    m.calls = calls
    m._total = total
    return m


def test_fingerprint_change_invalidates_cache(monkeypatch):
    mod = _codex_like(100)
    fp = {"v": 1}
    mod.fingerprint = lambda days=30: fp["v"]
    table = {mod.NAME: mod}
    monkeypatch.setattr(sources, "discover_adapters",
                        lambda adapters_dir=None: table)

    out1 = sources.collect_all(days=30)
    assert out1["sources"]["fakecodex"]["totals"]["total"] == 100
    assert mod.calls["n"] == 1

    # second poll inside TTL with UNCHANGED inputs → cached
    out2 = sources.collect_all(days=30)
    assert out2["sources"]["fakecodex"]["totals"]["total"] == 100
    assert mod.calls["n"] == 1

    # input files "changed" (new rollout bytes) → rescan despite fresh TTL
    mod._total = 250
    mod.scan = lambda days=30: {
        "available": True, "days": days,
        "totals": {"input": 250, "output": 0, "cached": 0, "total": 250},
        "daily": {}, "models": {}, "meta": {},
    }
    fp["v"] = 2
    out3 = sources.collect_all(days=30)
    assert out3["sources"]["fakecodex"]["totals"]["total"] == 250


def test_no_fingerprint_still_caches_within_ttl(monkeypatch):
    mod = _codex_like(50)          # no fingerprint attr at all
    table = {mod.NAME: mod}
    monkeypatch.setattr(sources, "discover_adapters",
                        lambda adapters_dir=None: table)
    sources.collect_all(days=30)
    sources.collect_all(days=30)
    assert mod.calls["n"] == 1     # served from cache as before
