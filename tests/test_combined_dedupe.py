"""combined() dedupe semantics with synthetic adapter modules.

Overlapping adapters (same DEDUPE_GROUP) contribute through the one with the
LOWEST COMBINED_PRIORITY; the loser still appears in per_source_totals and
is listed in skipped_overlap. Group-less sources always merge. Zero totals
must not divide by zero.
"""

from __future__ import annotations

import sys
import types

import sources


def _mod(name, label, total, group=None, priority=None):
    m = types.ModuleType(f"fake_{name}")
    m.NAME = name
    m.LABEL = label
    m.ORDER = 10
    m.DEDUPE_GROUP = group
    m.COMBINED_PRIORITY = priority
    m.scan = lambda days=30: {
        "available": True, "days": days,
        "totals": {"input": total, "output": 0, "cached": 0, "total": total},
        "daily": {}, "models": {}, "meta": {},
    }
    return m


def _run(monkeypatch, mods: list):
    table = {m.NAME: m for m in mods}
    monkeypatch.setattr(sources, "discover_adapters", lambda adapters_dir=None: table)
    out = sources.collect_all(days=30)
    return out["combined"]


def test_group_winner_takes_combined_loser_skipped(monkeypatch):
    # codex-family: rollouts (priority 100) beat bridge ledger (priority 200)
    mods = [_mod("codex", "Codex", 1000, "codex-family", 100),
            _mod("ledger", "Ledger", 400, "codex-family", 200),
            _mod("gemini", "Gemini", 50)]
    c = _run(monkeypatch, mods)
    assert c["totals"]["total"] == 1050          # 1000 + 50, ledger deduped
    # the deduped loser is excluded from per_source_totals entirely
    assert c["per_source_totals"] == {"codex": 1000, "gemini": 50}
    assert c["skipped_overlap"] == ["ledger"]
    assert c["source_share_pct"]["codex"] == round(1000 / 1050 * 100, 1)


def test_no_group_always_merges(monkeypatch):
    mods = [_mod("a", "A", 10), _mod("b", "B", 20)]
    c = _run(monkeypatch, mods)
    assert c["totals"]["total"] == 30
    assert c["skipped_overlap"] == []


def test_unavailable_source_never_blocks_group(monkeypatch):
    # winner unavailable → loser becomes the best AVAILABLE member
    mods = [_mod("big", "Big", 0, "g", 10), _mod("small", "Small", 7, "g", 20)]
    mods[0].scan = lambda days=30: {"available": False, "days": days,
                                    "totals": {}, "daily": {}, "models": {},
                                    "meta": {}, "error": "x"}
    c = _run(monkeypatch, mods)
    assert c["totals"]["total"] == 7
    assert c["skipped_overlap"] == []


def test_zero_total_guard(monkeypatch):
    mods = [_mod("z", "Z", 0)]
    c = _run(monkeypatch, mods)
    assert c["totals"]["total"] == 0
    # denom guard: 0/1 → 0.0 share, never ZeroDivision/NaN
    assert c["source_share_pct"] == {"z": 0.0}


def test_scan_exception_isolated(monkeypatch):
    def boom(days=30):
        raise RuntimeError("kaboom")
    bad = _mod("bad", "Bad", 0)
    bad.scan = boom
    good = _mod("good", "Good", 5)
    table = {m.NAME: m for m in [bad, good]}
    monkeypatch.setattr(sources, "discover_adapters", lambda adapters_dir=None: table)
    out = sources.collect_all(days=30)
    assert out["sources"]["bad"]["available"] is False
    assert "kaboom" in out["sources"]["bad"]["error"]
    assert out["combined"]["totals"]["total"] == 5


_ = sys  # keep import for future use
