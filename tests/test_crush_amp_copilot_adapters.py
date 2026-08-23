"""Tests for the Crush / Amp / Copilot CLI adapters (synthetic fixtures)."""

from __future__ import annotations

import json
import os
import sqlite3
import time

import pytest

import sources


def _day(days_ago: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - days_ago * 86400))


def _bucket(inp, out, cached=0):
    return {"input": inp, "output": out, "cached": cached,
            "total": inp + out}


# ── crush ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def crush_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj = home / "work" / "proj-a"
    proj.mkdir(parents=True)
    # registry (modern object shape)
    reg = home / "AppData" / "Local" / "crush" / "projects.json"
    reg.parent.mkdir(parents=True)
    reg.write_text(json.dumps({
        "p1": {"path": str(proj), "title": "proj-a"},
    }), encoding="utf-8")
    (proj / ".crush").mkdir(parents=True)
    con = sqlite3.connect(proj / ".crush" / "crush.db")
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT,"
                " title TEXT, message_count INTEGER, prompt_tokens INTEGER,"
                " completion_tokens INTEGER, cost REAL, updated_at INTEGER,"
                " created_at INTEGER)")
    con.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT,"
                " role TEXT, parts TEXT, model TEXT, created_at INTEGER,"
                " updated_at INTEGER)")
    now = int(time.time())
    rows = [
        ("s1", None, "t", 3, 1000, 200, 0.1, now - 86400, now - 86410),
        ("s2", None, "t", 2, 500, 90, 0.02, now - 2 * 86400, now - 2 * 86400),
        ("sub", "s1", "child", 1, 9999, 9999, 0.0, now - 100, now - 100),   # skipped
        ("s0", None, "empty", 0, 0, 0, 0.0, now - 100, now - 100),          # skipped
        ("old", None, "old", 5, 7777, 111, 0.5,
         int(now) - 40 * 86400, int(now) - 40 * 86400),                     # out of window
    ]
    con.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT INTO messages VALUES ('m1','s1','assistant','[]','qwen3-coder',?,?)",
                (now - 86400, now - 86400))
    con.execute("INSERT INTO messages VALUES ('m2','s2','assistant','[]','gpt-5-mini',?,?)",
                (now - 2 * 86400, now - 2 * 86400))
    con.commit()
    con.close()

    monkeypatch.setenv("USAGE_DASH_HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    sources._cache.clear()
    return home


def test_crush_buckets(crush_home):
    mods = sources.discover_adapters()
    res = mods["crush"].scan(30)
    assert res["available"], res.get("error")
    t = res["totals"]
    assert t["input"] == 1500 and t["output"] == 290 and t["total"] == 1790
    assert res["models"] == {"crush/qwen3-coder": _bucket(1000, 200),
                             "crush/gpt-5-mini": _bucket(500, 90)}
    assert set(res["daily"]) == {_day(1), _day(2)}
    assert res["meta"]["events_used"] == 2 and res["meta"]["skipped_old"] == 1


def test_crush_missing_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_DASH_HOME", str(tmp_path / "none"))
    sources._cache.clear()
    mods = sources.discover_adapters()
    res = mods["crush"].scan(30)
    assert res["available"] is False and res["error"]


# ── amp ─────────────────────────────────────────────────────────────────────

@pytest.fixture()
def amp_home(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    home = tmp_path / "home"
    tdir = home / ".local" / "share" / "amp" / "threads"
    tdir.mkdir(parents=True)
    iso = lambda d: datetime.now(timezone.utc).replace(
        microsecond=0).__str__() if False else (
        datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def thread(tid, created, msgs):
        (tdir / f"T-{tid}.json").write_text(json.dumps(
            {"v": 2, "id": f"T-{tid}", "created": created, "messages": msgs}),
            encoding="utf-8")

    usage = lambda i, o, cr=0, cw=0: {"input_tokens": i, "output_tokens": o,
                                      "cache_read_input_tokens": cr,
                                      "cache_creation_input_tokens": cw}
    thread("a1", iso(0.5),
           [{"role": "user", "content": "hi"},
            {"model": "claude-sonnet-4-6", "role": "assistant",
             "usage": usage(300, 60, cr=40, cw=20)},
            {"model": "claude-sonnet-4-6", "role": "assistant",
             "usage": usage(100, 30)}])
    thread("a2", iso(35),
           [{"role": "assistant", "usage": usage(5000, 900)}])   # out of window
    (tdir / "T-bad.json").write_text("{nope", encoding="utf-8")  # malformed

    monkeypatch.setenv("USAGE_DASH_HOME", str(home))
    sources._cache.clear()
    return home


def test_amp_buckets(amp_home):
    mods = sources.discover_adapters()
    res = mods["amp"].scan(30)
    assert res["available"], res.get("error")
    t = res["totals"]
    # input folds cache: (300+60) + 100 = 460 ; output 90 ; cached 60
    assert t["input"] == 460 and t["cached"] == 60 and t["output"] == 90
    assert t["total"] == 550
    assert list(res["models"]) == ["amp/claude-sonnet-4-6"]
    assert res["meta"]["malformed"] == 1 and res["meta"]["skipped_old"] == 1
    assert res["meta"]["events_used"] == 2


def test_amp_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_DASH_HOME", str(tmp_path / "void"))
    sources._cache.clear()
    mods = sources.discover_adapters()
    res = mods["amp"].scan(30)
    assert res["available"] is False and res["error"]


# ── copilot cli ─────────────────────────────────────────────────────────────

@pytest.fixture()
def copilot_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    db_dir = home / ".copilot"
    db_dir.mkdir(parents=True)
    con = sqlite3.connect(db_dir / "session-store.db")
    con.execute("CREATE TABLE assistant_usage_events ("
                "id INTEGER PRIMARY KEY, created_at INTEGER, model TEXT,"
                " input_tokens INTEGER, output_tokens INTEGER,"
                " cache_read_tokens INTEGER, cache_write_tokens INTEGER)")
    now = int(time.time())
    rows = [
        # stored input is CACHE-INCLUSIVE: in=150 with cr=40+cw=10 → uncached 100
        (1, now - 3600, "gpt-5.6-codex", 150, 70, 40, 10),
        (2, now - 2 * 86400, "claude-sonnet-4-6", 80, 25, 0, 0),
        (3, now - 45 * 86400, "gpt-4.1-mini", 999, 99, 0, 0),      # old
        (4, None, "x", 10, 10, 0, 0),                              # bad ts
    ]
    con.executemany("INSERT INTO assistant_usage_events VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    monkeypatch.setenv("USAGE_DASH_HOME", str(home))
    sources._cache.clear()
    return home


def test_copilot_cache_inclusive_input(copilot_home):
    mods = sources.discover_adapters()
    res = mods["copilotcli"].scan(30)
    assert res["available"], res.get("error")
    t = res["totals"]
    # event1: uncached_in=100 + cach=50 → in=150, out=70, cached=50
    # event2: in=80, out=25, cached=0
    assert t["input"] == 230 and t["output"] == 95 and t["cached"] == 50
    assert t["total"] == 325
    assert res["models"]["copilot/gpt-5.6-codex"] == _bucket(150, 70, 50)
    assert res["models"]["copilot/claude-sonnet-4-6"] == _bucket(80, 25)
    assert res["meta"]["skipped_old"] == 1 and res["meta"]["malformed"] == 1


def test_copilot_missing_db(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_DASH_HOME", str(tmp_path / "nada"))
    sources._cache.clear()
    mods = sources.discover_adapters()
    res = mods["copilotcli"].scan(30)
    assert res["available"] is False and res["error"]
