"""Contract conformance for EVERY bundled adapter (dynamically discovered).

Runs each scan()/limits() against the isolated synthetic home (conftest
autouse fixture), so this passes on any machine and never hardcodes adapter
names: only shape invariants are asserted, plus graceful available:false.
"""

from __future__ import annotations

import os

import pytest

import sources


@pytest.fixture()
def all_adapters():
    mods = sources.discover_adapters()
    assert mods, "no adapters discovered — bundled backend broken"
    return mods


BUCKET_KEYS = {"input", "output", "cached", "total"}


def _assert_shape(res, days):
    assert isinstance(res, dict)
    assert res.get("days") == days
    if not res.get("available"):
        # degraded results must say why and still carry the base shape
        assert isinstance(res.get("error"), str) and res["error"]
        return
    totals = res["totals"]
    assert BUCKET_KEYS <= set(totals.keys())
    for bucket in (totals, res["daily"], res["models"]):
        values = [bucket] if bucket is totals else bucket.values()
        for entry in values:
            for k in BUCKET_KEYS:
                assert isinstance(entry[k], int), f"non-int {k}"
            assert entry["total"] == entry["input"] + entry["output"], \
                "total must equal input + output"
    assert isinstance(res.get("meta"), dict)
    # UI contract: display meta embedded by collect_all; scan() alone may omit it


def test_scan_shape_all_adapters(all_adapters):
    for name, mod in sorted(all_adapters.items()):
        res = mod.scan(30)
        _assert_shape(res, 30)


def test_limits_shape_where_defined(all_adapters):
    checked = 0
    for name, mod in sorted(all_adapters.items()):
        if not hasattr(mod, "limits"):
            continue
        checked += 1
        lim = mod.limits()
        assert isinstance(lim, dict)
        assert isinstance(lim.get("available"), bool)
        if lim.get("available"):
            wins = lim.get("windows")
            assert isinstance(wins, list) and wins, f"{name}: available but no windows"
            for w in wins:
                assert w.get("name"), f"{name}: window missing name"
                assert "used_pct" in w or "spent_display" in w, \
                    f"{name}: window missing used_pct/spent_display"
    assert checked >= 2, "expected limits() on at least codex+commandcode"


def test_collect_all_embeds_meta_and_combined(all_adapters):
    out = sources.collect_all(days=30)
    for name, s in out["sources"].items():
        meta = s["meta"]
        assert meta.get("label"), f"{name}: no label embedded"
        assert isinstance(meta.get("order"), int)
    c = out["combined"]
    assert {"totals", "daily", "models", "per_source_totals",
            "source_share_pct"} <= set(c.keys())
    # combined totals equal sum of non-skipped available sources
    expect = sum(v for n, v in c["per_source_totals"].items()
                 if n not in set(c.get("skipped_overlap", [])))
    assert c["totals"]["total"] == expect


def test_days_parameter_propagates(all_adapters):
    for name, mod in list(sorted(all_adapters.items()))[:3]:
        for d in (1, 7):
            res = mod.scan(d)
            assert res["days"] == d


_ = os
