"""Tests for the Cline + Roo Code adapters (shared ui_messages.json format).

Builds a synthetic VS Code globalStorage tree under USAGE_DASH_HOME with
relative-to-now timestamps, then asserts exact bucket math, windowing,
model attribution from <model> tags, and graceful degradation.
"""

from __future__ import annotations

import json
import os
import time

import pytest

import sources


def _ts_ms(days_ago: float) -> int:
    return int((time.time() - days_ago * 86400) * 1000)


def _iso_z(days_ago: float) -> str:
    import datetime
    dt = datetime.datetime.utcfromtimestamp(_ts_ms(days_ago) / 1000.0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def cline_home(tmp_path, monkeypatch):
    """Synthetic home with one Cline task and one Roo task."""
    home = tmp_path / "home"
    gs = home / "AppData" / "Roaming" / "Code" / "User" / "globalStorage"

    # --- Cline task: two requests across 2 days/models -----------------------
    task = gs / "saoudrizwan.claude-dev" / "tasks" / "task-a"
    task.mkdir(parents=True)
    (task / "ui_messages.json").write_text(json.dumps([
        {"ts": _ts_ms(2.1), "type": "say", "say": "api_req_started",
         "text": json.dumps({"tokensIn": 100, "tokensOut": 40,
                             "cacheReads": 30, "cacheWrites": 10})},
        {"ts": _ts_ms(1.1), "type": "say", "say": "api_req_started",
         "text": json.dumps({"tokensIn": 50, "tokensOut": 20,
                             "cacheReads": 5, "cacheWrites": 0})},
        {"ts": _ts_ms(0.9), "type": "say", "say": "api_req_started",
         "text": "{not json"},                       # malformed → tolerated
        {"ts": _ts_ms(0.8), "type": "say", "say": "user_feedback",  # not usage
         "text": "hello"},
        {"ts": int((_ts_ms(0.7)) / 1000), "type": "say",            # seconds→excluded
         "say": "api_req_started",
         "text": json.dumps({"tokensIn": 999, "tokensOut": 999})},
    ]), encoding="utf-8")
    # model tag lives in the conversation history (last tag wins)
    hist = [{"role": "user",
             "content": [{"type": "text", "text": "<task>do</task>"
                          "<model>claude-sonnet-4-5</model>"}]},
            {"role": "assistant", "content": []},
            {"role": "user",
             "content": [{"type": "text", "text": "<model>gemini-3-pro</model>"}]}]
    (task / "api_conversation_history.json").write_text(json.dumps(hist),
                                                        encoding="utf-8")

    # --- Roo task: single request, out-of-window sibling dir -----------------
    roo_task = gs / "rooveterinaryinc.roo-cline" / "tasks" / "task-r"
    roo_task.mkdir(parents=True)
    (roo_task / "ui_messages.json").write_text(json.dumps([
        {"ts": _ts_ms(0.6), "type": "say", "say": "api_req_started",
         "text": json.dumps({"tokensIn": 200, "tokensOut": 80,
                             "cacheReads": 0, "cacheWrites": 25})},
    ]), encoding="utf-8")
    (roo_task / "api_conversation_history.json").write_text(
        json.dumps([{"role": "user",
                     "content": [{"type": "text", "text": "<model>gpt-5.6</model>"}]}]),
        encoding="utf-8")
    old_roo = gs / "rooveterinaryinc.roo-cline" / "tasks" / "task-old"
    old_roo.mkdir(parents=True)
    (old_roo / "ui_messages.json").write_text(json.dumps([
        {"ts": _ts_ms(45), "type": "say", "say": "api_req_started",
         "text": json.dumps({"tokensIn": 500, "tokensOut": 500})},
    ]), encoding="utf-8")
    # age the dir so the adapter's cheap mtime pre-filter also sees it as old
    old_stamp = time.time() - 45 * 86400
    os.utime(old_roo, (old_stamp, old_stamp))

    monkeypatch.setenv("USAGE_DASH_HOME", str(home))
    sources._cache.clear()
    return home


def test_cline_buckets(cline_home):
    mods = sources.discover_adapters()
    res = mods["cline"].scan(30)
    assert res["available"], res.get("error")
    t = res["totals"]
    # req1: in=100+30+10=140 cached=40 out=40 ; req2: in=55 cached=5 out=20
    assert t["input"] == 195 and t["cached"] == 45 and t["output"] == 60
    assert t["total"] == 255
    # model attribution is PER-TASK (last <model> tag in the history applies
    # to all its requests) — so BOTH requests carry gemini-3-pro
    m = res["models"]["cline/gemini-3-pro"]
    assert m["input"] == 195 and m["output"] == 60
    assert set(res["daily"].keys()) <= {
        __import__("datetime").datetime.utcfromtimestamp(
            _ts_ms(d) / 1000.0).strftime("%Y-%m-%d") for d in (1.1, 2.1)}
    assert res["meta"]["events_used"] == 2       # malformed + non-usage skipped


def test_roo_buckets_and_window(cline_home):
    mods = sources.discover_adapters()
    res = mods["roo"].scan(30)
    assert res["available"], res.get("error")
    t = res["totals"]
    assert t["input"] == 225 and t["cached"] == 25 and t["output"] == 80
    assert t["total"] == 305
    assert list(res["models"]) == ["roo/gpt-5.6"]
    assert res["meta"]["tasks_scanned"] == 1     # 45-day-old dir filtered


def test_missing_dirs_graceful(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_DASH_HOME", str(tmp_path / "empty"))
    sources._cache.clear()
    mods = sources.discover_adapters()
    for name in ("cline", "roo"):
        res = mods[name].scan(30)
        assert res["available"] is False
        assert res["error"]
