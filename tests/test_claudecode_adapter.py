"""Tests for backend/adapters/claudecode.py — Claude Code transcript scan.

Standalone: does NOT rely on a repo conftest.py (one may appear later with
its own USAGE_DASH_HOME autouse fixture; monkeypatch.setenv here composes
fine with that). Every test redirects USAGE_DASH_HOME to a synthetic home
tree and clears the sources TTL cache so scans are always fresh.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO, "backend")
ADAPTERS = os.path.join(BACKEND, "adapters")
for _p in (BACKEND, ADAPTERS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sources  # noqa: E402
import claudecode  # noqa: E402


# --------------------------------------------------------------- helpers ----


def _iso_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _assistant_line(msg_id, model, ts, inp, out, ccreate, cread,
                    session="s1"):
    return json.dumps({
        "type": "assistant",
        "uuid": "u-" + msg_id,
        "parentUuid": None,
        "timestamp": ts,
        "sessionId": session,
        "cwd": "/proj-a",
        "message": {
            "id": msg_id,
            "model": model,
            "content": [{"type": "text", "text": "hi"}],
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_creation_input_tokens": ccreate,
                "cache_read_input_tokens": cread,
            },
        },
    })


def _write_jsonl(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _bucket(inp, out, cach):
    return {"input": inp, "output": out, "cached": cach,
            "total": inp + out}


# -------------------------------------------------------------- fixtures ----


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """Synthetic ~/.claude/projects tree + redirected USAGE_DASH_HOME."""
    monkeypatch.setenv("USAGE_DASH_HOME", str(tmp_path))
    sources._cache.clear()
    yield tmp_path
    sources._cache.clear()


@pytest.fixture()
def seeded_home(fake_home):
    """One session file exercising every parsing rule of the adapter.

    Expected outcome (days=30):
      msg_1 streamed-duplicate -> counted ONCE with LAST usage:
          inp = 120+35+250 = 405, out = 80, cached = 285   (day: now-1d)
      msg_2 -> inp = 1000+0+0 = 1000, out = 400, cached = 0 (day: today)
      msg_old (40d old) -> skipped_old, never counted
      user/system/no-usage/malformed lines -> ignored/tolerated
    """
    now = datetime.now(timezone.utc)
    d_yest = _iso_z(now - timedelta(days=1))
    d_today = _iso_z(now)
    d_old = _iso_z(now - timedelta(days=40))

    session = fake_home / ".claude" / "projects" / "proj-a" / "session-1.jsonl"
    _write_jsonl(str(session), [
        # streamed-block duplicate: SAME message.id, different usage;
        # adapter must keep the LAST occurrence.
        _assistant_line("msg_1", "claude-sonnet-4-5", d_yest,
                        inp=100, out=50, ccreate=30, cread=200),
        _assistant_line("msg_1", "claude-sonnet-4-5", d_yest,
                        inp=120, out=80, ccreate=35, cread=250),
        # second model, next day
        _assistant_line("msg_2", "claude-opus-4-1", d_today,
                        inp=1000, out=400, ccreate=0, cread=0),
        # outside the 30-day window -> excluded
        _assistant_line("msg_old", "claude-sonnet-4-5", d_old,
                        inp=9999, out=9999, ccreate=9999, cread=9999),
        # malformed JSON carrying the prefilter substring -> tolerated
        '{"type":"assistant","timestamp":"' + d_today
        + '","message":{"id":"msg_bad","usage":{"input_tokens":',
        # non-assistant lines with no usage -> ignored
        json.dumps({"type": "user", "timestamp": d_today,
                    "sessionId": "s1", "message": {"role": "user",
                                                   "content": "hello"}}),
        json.dumps({"type": "system", "timestamp": d_today, "sessionId": "s1"}),
        # assistant line WITHOUT usage dict -> ignored
        json.dumps({"type": "assistant", "timestamp": d_today,
                    "sessionId": "s1",
                    "message": {"id": "msg_nousage",
                                "model": "claude-sonnet-4-5"}}),
    ])
    return {"home": fake_home, "yesterday": d_yest[:10], "today": d_today[:10]}


# ----------------------------------------------------------------- tests ----


def test_scan_buckets_and_dedupe(seeded_home):
    res = claudecode.scan(days=30)

    assert res["available"] is True
    assert res["days"] == 30

    # totals: cache folded into input; only the two in-window ids counted
    exp_msg1 = _bucket(405, 80, 285)     # last duplicate occurrence wins
    exp_msg2 = _bucket(1000, 400, 0)
    exp_tot = {k: exp_msg1[k] + exp_msg2[k] for k in exp_msg1}
    assert res["totals"] == exp_tot
    assert res["totals"]["total"] == \
        res["totals"]["input"] + res["totals"]["output"]

    # daily buckets, exact
    assert res["daily"] == {
        seeded_home["yesterday"]: exp_msg1,
        seeded_home["today"]: exp_msg2,
    }

    # model attribution: 'claude/' prefix over message.model
    assert res["models"] == {
        "claude/claude-sonnet-4-5": exp_msg1,
        "claude/claude-opus-4-1": exp_msg2,
    }

    # meta counters: one file, two deduped events
    assert res["meta"] == {"files_scanned": 1, "events_used": 2}


def test_streamed_duplicate_keeps_last_usage_only_once(seeded_home):
    """Same message.id twice with different usage: exactly one event,
    values taken from the LAST line on disk."""
    res = claudecode.scan(days=30)
    sonnet = res["models"]["claude/claude-sonnet-4-5"]
    # FIRST occurrence was inp=330/out=50/cached=230 — must NOT appear.
    assert sonnet["input"] == 405
    assert sonnet["output"] == 80
    assert sonnet["cached"] == 285
    assert sonnet["total"] == 485


def test_out_of_window_line_excluded(seeded_home):
    res = claudecode.scan(days=30)
    all_vals = [res["totals"][k] for k in ("input", "output", "cached")]
    assert 9999 not in all_vals
    assert len(res["daily"]) == 2  # only yesterday + today


def test_missing_dir_unavailable_with_error(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_DASH_HOME", str(tmp_path))  # no .claude at all
    sources._cache.clear()
    res = claudecode.scan(days=30)
    assert res["available"] is False
    root = os.path.join(str(tmp_path), ".claude", "projects")
    assert res["error"] == f"no dir {root}"
    assert res["totals"] == {"input": 0, "output": 0, "cached": 0, "total": 0}
    assert res["daily"] == {} and res["models"] == {}
    sources._cache.clear()


def test_empty_projects_dir_reports_no_files(fake_home):
    os.makedirs(fake_home / ".claude" / "projects", exist_ok=True)
    res = claudecode.scan(days=30)
    assert res["available"] is False
    assert res["error"] == "no transcript files in window"
    assert res["meta"] == {"files_scanned": 0, "events_used": 0}


def test_days_window_boundary(seeded_home):
    """A tight window (7d) still includes yesterday's msg_1 and drops nothing
    new; a 3-day window behaves identically — proves day strings drive the
    window, not mtimes."""
    r7 = claudecode.scan(days=7)
    assert r7["meta"]["events_used"] == 2
    sources._cache.clear()
    r3 = claudecode.scan(days=3)
    assert r3["meta"]["events_used"] == 2
    assert set(r3["daily"]) == {seeded_home["yesterday"],
                                seeded_home["today"]}
