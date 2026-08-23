"""Tests for backend/adapters/opencode.py — SQLite usage-store scan.

Relies on the repo conftest autouse fixture: USAGE_DASH_HOME -> tmp_path
for every test and sources caches cleared. Fixtures below build a
synthetic ~/.local/share/opencode/opencode.db with relative-to-now
timestamps so the suite never touches real machine data.
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
import opencode  # noqa: E402


# --------------------------------------------------------------- helpers ----


def _day(days_ago: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - days_ago * 86400))


def _assistant_data(model="claude-sonnet-4.6", inp=0, out=0, reason=0,
                    cread=0, cwrite=0):
    return json.dumps({
        "role": "assistant",
        "modelID": model,
        "providerID": "anthropic",
        "cost": 0,
        "tokens": {"total": inp + out + reason + cread + cwrite,
                   "input": inp, "output": out, "reasoning": reason,
                   "cache": {"read": cread, "write": cwrite}},
    })


def _bucket(inp, out, cach):
    return {"input": inp, "output": out, "cached": cach,
            "total": inp + out}


def _seed_db(path, rows):
    """rows: list of (time_created, data_json_or_None)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT)")
    con.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT,"
                " time_created REAL, data TEXT)")
    con.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT,"
                " session_id TEXT, data TEXT)")
    for i, (ts, data) in enumerate(rows):
        con.execute("INSERT INTO message VALUES (?, ?, ?, ?)",
                    (f"msg_{i}", "ses_1", ts, data))
    con.commit()
    con.close()


@pytest.fixture()
def seeded_db(tmp_path):
    """DB exercising every parsing rule.

    Expected outcome (days=30):
      msg today   sonnet: inp=500+300+100=900, out=200+50=250, cached=400
      msg today   gpt:    inp=1000, out=400, cached=0
      msg yest.   gemini (MILLIS ts): inp=10, out=5, cached=0
      msg 40d old -> skipped_old; malformed JSON / user role -> ignored
    """
    db = tmp_path / ".local" / "share" / "opencode" / "opencode.db"
    now = time.time()
    _seed_db(str(db), [
        # today — cache folded into input, reasoning into output
        (now, _assistant_data("claude-sonnet-4.6", inp=500, out=200,
                              reason=50, cread=300, cwrite=100)),
        # today — second model, no cache/reasoning
        (now - 60, _assistant_data("gpt-5-codex", inp=1000, out=400)),
        # yesterday — MILLISECONDS timestamp must normalize to a valid day
        (now * 1000.0 - 86400 * 1000,
         _assistant_data("gemini-2.5-pro", inp=10, out=5)),
        # outside the window
        (now - 40 * 86400, _assistant_data("claude-sonnet-4.6",
                                           inp=9999, out=9999)),
        # malformed JSON payload -> tolerated
        (now, '{"role":"assistant","tokens":{"input":'),
        # non-assistant role -> ignored
        (now, json.dumps({"role": "user", "tokens": {"input": 500}})),
        # assistant without tokens dict -> ignored
        (now, json.dumps({"role": "assistant", "modelID": "x"})),
    ])
    sources._cache.clear()
    yield {"db": str(db), "today": _day(0), "yesterday": _day(1)}
    sources._cache.clear()


def _exp_msg1():
    return _bucket(900, 250, 400)


def _exp_msg2():
    return _bucket(1000, 400, 0)


def _exp_msg3():
    return _bucket(10, 5, 0)


# ----------------------------------------------------------------- tests ----


def test_scan_buckets_and_model_attribution(seeded_db):
    res = opencode.scan(days=30)

    assert res["available"] is True
    assert res["days"] == 30

    exp = {k: _exp_msg1()[k] + _exp_msg2()[k] + _exp_msg3()[k]
           for k in ("input", "output", "cached", "total")}
    assert res["totals"] == exp
    assert res["totals"]["total"] == \
        res["totals"]["input"] + res["totals"]["output"]

    assert res["daily"] == {
        seeded_db["today"]: {k: _exp_msg1()[k] + _exp_msg2()[k]
                             for k in ("input", "output", "cached", "total")},
        seeded_db["yesterday"]: _exp_msg3(),
    }

    assert res["models"] == {
        "opencode/claude-sonnet-4.6": _exp_msg1(),
        "opencode/gpt-5-codex": _exp_msg2(),
        "opencode/gemini-2.5-pro": _exp_msg3(),
    }

    # 7 rows scanned; 4 dropped (old / malformed / user-role / no-tokens)
    assert res["meta"] == {"messages_scanned": 7, "events_used": 3,
                           "skipped_old": 1}


def test_millisecond_timestamp_normalized_to_correct_day(seeded_db):
    """The >1e12 ms row must land on YESTERDAY's day string, not explode."""
    res = opencode.scan(days=30)
    assert res["daily"][seeded_db["yesterday"]] == _exp_msg3()


def test_window_boundary(seeded_db):
    r7 = opencode.scan(days=7)          # yesterday + today still inside
    assert r7["meta"]["events_used"] == 3
    sources._cache.clear()
    r_half = opencode.scan(days=1)      # cutoff excludes nothing new but
    assert set(r_half["daily"]) <= {seeded_db["today"], seeded_db["yesterday"]}


def test_missing_db_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_DASH_HOME", str(tmp_path))
    sources._cache.clear()
    res = opencode.scan(days=30)
    assert res["available"] is False
    expect = os.path.join(str(tmp_path), ".local", "share", "opencode",
                          "opencode.db")
    assert res["error"] == f"no db {expect}"
    assert res["totals"] == {"input": 0, "output": 0, "cached": 0, "total": 0}
    assert res["daily"] == {} and res["models"] == {}
    sources._cache.clear()


def test_xdg_data_home_override(tmp_path, monkeypatch):
    xdg = tmp_path / "xdgdata"
    now = time.time()
    _seed_db(str(xdg / "opencode" / "opencode.db"), [
        (now, _assistant_data("claude-haiku-4.5", inp=100, out=20)),
    ])
    monkeypatch.setenv("USAGE_DASH_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    sources._cache.clear()
    res = opencode.scan(days=30)
    assert res["available"] is True
    assert res["models"] == {
        f"opencode/claude-haiku-4.5": _bucket(100, 20, 0)}
    sources._cache.clear()


def test_corrupt_db_never_raises(tmp_path, monkeypatch):
    bad = tmp_path / ".local" / "share" / "opencode"
    bad.mkdir(parents=True)
    (bad / "opencode.db").write_bytes(b"this is not sqlite at all")
    monkeypatch.setenv("USAGE_DASH_HOME", str(tmp_path))
    sources._cache.clear()
    res = opencode.scan(days=30)
    assert res["available"] is False
    assert "cannot read opencode.db" in res["error"]
    sources._cache.clear()


def test_empty_message_table_unavailable(tmp_path, monkeypatch):
    _seed_db(str(tmp_path / ".local" / "share" / "opencode" / "opencode.db"),
             [])
    monkeypatch.setenv("USAGE_DASH_HOME", str(tmp_path))
    sources._cache.clear()
    res = opencode.scan(days=30)
    assert res["available"] is False
    assert res["error"] == "no assistant messages in window"
    sources._cache.clear()
