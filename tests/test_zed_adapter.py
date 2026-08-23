"""Tests for backend/adapters/zed.py — threads.db scan.

Relies on the repo conftest autouse fixture (USAGE_DASH_HOME -> tmp_path,
sources caches cleared). Fixtures build a synthetic Zed threads tree under
the redirected home; because the adapter probes ALL per-platform candidate
paths, each test can seed the Windows/Linux/macOS location explicitly and
exercise every branch on any OS.
"""

import json
import os
import sqlite3
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO, "backend")
ADAPTERS = os.path.join(BACKEND, "adapters")
for _p in (BACKEND, ADAPTERS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sources  # noqa: E402
import zed  # noqa: E402


# --------------------------------------------------------------- helpers ----


def _day(days_ago: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - days_ago * 86400))


def _thread_json(model="claude-sonnet-4.5", inp=0, out=0, ccreate=0, cread=0):
    return json.dumps({
        "model": {"provider": "anthropic", "model": model},
        "request_token_usage": {
            "usermsg-1": {"input_tokens": 1, "output_tokens": 1},
        },
        "cumulative_token_usage": {
            "input_tokens": inp,
            "output_tokens": out,
            "cache_creation_input_tokens": ccreate,
            "cache_read_input_tokens": cread,
        },
    })


def _bucket(inp, out, cach):
    return {"input": inp, "output": out, "cached": cach,
            "total": inp + out}


def _seed_threads_db(path, rows):
    """rows: list of (updated_at, data_type, data_bytes_or_None)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, summary TEXT,"
                " updated_at TEXT, data_type TEXT, data BLOB)")
    for i, (upd, dtype, blob) in enumerate(rows):
        con.execute("INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                    (f"t-{i}", f"summary {i}", upd, dtype, blob))
    con.commit()
    con.close()


@pytest.fixture()
def zed_paths(tmp_path):
    def _db(which: str) -> str:
        h = str(tmp_path)
        return {
            "windows": os.path.join(h, "AppData", "Local", "Zed",
                                    "threads", "threads.db"),
            "linux": os.path.join(h, ".local", "share", "zed",
                                  "threads", "threads.db"),
            "macos": os.path.join(h, "Library", "Application Support",
                                  "Zed", "threads", "threads.db"),
        }[which]
    return _db


# ----------------------------------------------------------------- tests ----


def test_scan_buckets_and_model_attribution(zed_paths):
    """One authoritative cumulative event per thread — the (incomplete)
    request_token_usage map must NOT be summed."""
    now = time.time()
    db = zed_paths("windows")   # adapter probes all paths; branch is free
    _seed_threads_db(db, [
        # thread today: cumulative 400/150 + caches 200+50 → Anthropic fold
        (now, "json",
         _thread_json("claude-sonnet-4.5", inp=400, out=150,
                      ccreate=200, cread=50).encode()),
        # thread yesterday: second model, zero-valued fields omitted upstream
        (now - 86400, "json",
         json.dumps({
             "model": {"provider": "openai", "model": "gpt-5"},
             "cumulative_token_usage": {"output_tokens": 30},
         }).encode()),
        # outside the window
        (now - 40 * 86400, "json",
         _thread_json(inp=9999, out=9999).encode()),
        # current-build zstd rows: counted as skipped, never parsed
        (now, "zstd", b"\x28\xb5\x2f\xfd-fake-zstd-bytes"),
        (now - 3600, "zstd", b"\x28\xb5\x2f\xfd-more-fake"),
        # unknown data_type -> skipped_other
        (now, "pickle", b"\x80\x04\x95garbage"),
        # json row without cumulative block -> tolerated
        (now, "json", b'{"model": {"provider": "x", "model": "y"}}'),
        # malformed json -> tolerated
        (now, "json", b'{"cumulative_token_usage": {'),
    ])

    res = zed.scan(days=30)
    assert res["available"] is True
    assert res["days"] == 30

    exp_t1 = _bucket(400 + 250, 150, 250)
    exp_t2 = _bucket(0, 30, 0)
    exp_tot = {k: exp_t1[k] + exp_t2[k]
               for k in ("input", "output", "cached", "total")}
    assert res["totals"] == exp_tot
    assert res["totals"]["total"] == \
        res["totals"]["input"] + res["totals"]["output"]

    assert res["daily"] == {
        _day(0): exp_t1,
        _day(1): exp_t2,
    }
    assert res["models"] == {
        "zed/claude-sonnet-4.5": exp_t1,
        "zed/gpt-5": exp_t2,
    }

    # 8 rows scanned; 6 dropped (1 old / 2 zstd / 1 other / no-cumulative /
    # malformed)
    assert res["meta"] == {"threads_scanned": 8, "events_used": 2,
                           "skipped_zstd": 2, "skipped_other": 1,
                           "skipped_old": 1}


def test_request_map_not_double_counted(zed_paths):
    """cumulative is authoritative: request map entries add NOTHING."""
    now = time.time()
    payload = json.dumps({
        "model": {"provider": "anthropic", "model": "claude-opus-4-6"},
        "request_token_usage": {
            "u1": {"input_tokens": 5000, "output_tokens": 700},
            "u2": {"input_tokens": 8000},
        },
        "cumulative_token_usage": {"input_tokens": 100, "output_tokens": 10},
    }).encode()
    _seed_threads_db(zed_paths("linux"), [(now, "json", payload)])
    res = zed.scan(days=30)
    # exactly the cumulative numbers — not 13100/710
    assert res["totals"] == _bucket(100, 10, 0)


def test_zstd_only_db_reports_unavailable_with_honest_meta(zed_paths):
    """Current Zed writes zstd rows only; stdlib has no zstd → the adapter
    must say available:false and count what it could not read."""
    now = time.time()
    _seed_threads_db(zed_paths("macos"), [
        (now, "zstd", b"\x28\xb5\x2f\xfd-a"),
        (now - 60, "zstd", b"\x28\xb5\x2f\xfd-b"),
    ])
    res = zed.scan(days=30)
    assert res["available"] is False
    assert res["meta"]["skipped_zstd"] == 2
    assert res["meta"]["events_used"] == 0
    assert "zstd" in res["error"]
    assert res["totals"] == {"input": 0, "output": 0, "cached": 0, "total": 0}


def test_updated_at_formats(zed_paths):
    """updated_at may be unix seconds, milliseconds, or an ISO string."""
    now = time.time()
    rows = [
        (now, "json", _thread_json("m-sec", inp=10, out=1).encode()),
        (now * 1000.0, "json", _thread_json("m-ms", inp=20, out=2).encode()),
        (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "json",
         _thread_json("m-iso", inp=30, out=3).encode()),
    ]
    _seed_threads_db(zed_paths("linux"), rows)
    res = zed.scan(days=30)
    assert set(res["daily"]) == {_day(0)}      # all three land today
    assert res["meta"]["events_used"] == 3
    assert res["models"]["zed/m-sec"] == _bucket(10, 1, 0)
    assert res["models"]["zed/m-ms"] == _bucket(20, 2, 0)
    assert res["models"]["zed/m-iso"] == _bucket(30, 3, 0)


def test_missing_db_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_DASH_HOME", str(tmp_path))
    sources._cache.clear()
    res = zed.scan(days=30)
    assert res["available"] is False
    assert res["error"] == "no Zed threads.db found on this platform"
    assert res["totals"] == {"input": 0, "output": 0, "cached": 0, "total": 0}
    assert res["daily"] == {} and res["models"] == {}
    sources._cache.clear()


def test_corrupt_db_never_raises(tmp_path, monkeypatch):
    d = tmp_path / ".local" / "share" / "zed" / "threads"
    d.mkdir(parents=True)
    (d / "threads.db").write_bytes(b"not a database")
    monkeypatch.setenv("USAGE_DASH_HOME", str(tmp_path))
    sources._cache.clear()
    res = zed.scan(days=30)
    assert res["available"] is False
    assert "cannot read threads.db" in res["error"]
    sources._cache.clear()
