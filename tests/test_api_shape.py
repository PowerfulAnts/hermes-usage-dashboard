"""API-shape test: the FastAPI router returns the contract the UI expects.

Runs against a synthetic home (USAGE_DASH_HOME) containing a minimal Codex
rollout + Hermes sqlite db, with dates RELATIVE TO NOW so window filtering
includes them whenever tests run. Portal is unavailable outside Hermes →
portal.available must be false, not an error.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH_DIR = os.path.join(REPO_ROOT, "dashboard")
for p in (os.path.join(REPO_ROOT, "backend"), DASH_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def synthetic_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    # --- codex rollout ------------------------------------------------------
    sess = home / ".codex" / "sessions" / "2026" / "01" / "01"
    sess.mkdir(parents=True)
    day_tag = yesterday.strftime("%Y-%m-%d")
    rollout = sess / f"{day_tag}T00-00-00-aaa-rollout.jsonl"
    ctx_line = {"type": "turn_context", "timestamp": _iso(yesterday),
                "payload": {"model": "gpt-test"}}
    tok_line = {"type": "event_msg", "timestamp": _iso(yesterday),
                "payload": {"type": "token_count", "info": {
                    "last_token_usage": {"input_tokens": 100, "output_tokens": 50,
                                         "cached_input_tokens": 10,
                                         "reasoning_output_tokens": 5},
                    "total_token_usage": {"input_tokens": 100, "output_tokens": 55,
                                          "cached_input_tokens": 10}}}}
    rollout.write_text(json.dumps(ctx_line) + "\n" + json.dumps(tok_line) + "\n",
                       encoding="utf-8")

    # --- hermes sqlite -------------------------------------------------------
    if os.name == "nt":
        db_dir = home / "AppData" / "Local" / "hermes"
    else:
        db_dir = home / ".hermes"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(db_dir / "state.db")
    conn.execute(
        "CREATE TABLE session_model_usage (last_seen REAL, model TEXT,"
        " billing_provider TEXT, input_tokens INTEGER, output_tokens INTEGER,"
        " cache_read_tokens INTEGER, reasoning_tokens INTEGER)")
    conn.execute("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?)",
                 (yesterday.timestamp(), "m", "nous", 10, 4, 2, 1))
    conn.commit()
    conn.close()

    monkeypatch.setenv("USAGE_DASH_HOME", str(home))
    # clear caches so this fixture's data is what gets scanned
    sources = __import__("sources")
    sources._cache.clear()
    return home


def _summary(days=7):
    import plugin_api
    return asyncio.run(plugin_api.summary(days=days))


def test_summary_contract(synthetic_home):
    data = _summary(7)
    assert "generated_at" in data
    assert isinstance(data["sources"], dict)

    # The API path runs with background=True: heavy adapters may answer
    # "scanning" on the very first call and land their numbers one poll
    # later. Wait for the background scans to finish, then re-fetch.
    deadline = time.time() + 60
    while time.time() < deadline:
        codex = data["sources"].get("codex") or {}
        hermes = data["sources"].get("hermes") or {}
        if codex.get("available") and hermes.get("available"):
            break
        time.sleep(1)
        data = _summary(7)

    # codex parsed the synthetic rollout: 100 in (cached 10 already inside
    # input, OpenAI-style), 50 out + 5 reasoning → 155 total
    codex = data["sources"].get("codex")
    assert codex and codex["available"], codex.get("error") if codex else "missing"
    t = codex["totals"]
    assert t["total"] == 155
    assert t["input"] == 100 and t["output"] == 55 and t["cached"] == 10

    # hermes parsed the sqlite row (reasoning rides into output: 4+1=5)
    hermes = data["sources"].get("hermes")
    assert hermes and hermes["available"], hermes.get("error") if hermes else "missing"
    ht = hermes["totals"]
    assert ht["total"] == 15 and ht["input"] == 10 and ht["output"] == 5

    # every source carries embedded display meta
    for name, s in data["sources"].items():
        assert s["meta"]["label"], name

    # combined sums available sources
    c = data["combined"]
    assert c["totals"]["total"] == 155 + 15

    # limits block exists; entries are well-formed. The synthetic rollout has
    # no rate_limit snapshots → codex limits legitimately absent here.
    assert isinstance(data["limits"], dict)
    for pname, lim in data["limits"]["providers"].items():
        assert isinstance(lim.get("available"), bool)
        if lim["available"]:
            assert lim.get("label"), pname
    # portal degrades gracefully outside Hermes but may answer inside it
    assert isinstance(data["portal"].get("available"), bool)
