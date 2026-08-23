"""Tests for backend/adapters/aider.py — opt-in analytics.jsonl scan.

Relies on the repo conftest autouse fixture (USAGE_DASH_HOME -> tmp_path,
sources caches cleared). Fixtures write a synthetic ~/.aider/analytics.jsonl
with relative-to-now timestamps.
"""

import json
import os
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
import aider  # noqa: E402


# --------------------------------------------------------------- helpers ----


def _day(days_ago: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - days_ago * 86400))


def _event(model, prompt, completion, ts, **extra_props):
    props = {"main_model": model, "edit_format": "diff",
             "prompt_tokens": prompt, "completion_tokens": completion,
             "total_tokens": prompt + completion, "cost": 0.01}
    props.update(extra_props)
    return json.dumps({"event": "message_send", "properties": props,
                       "user_id": "u1", "time": ts})


def _bucket(inp, out):
    # Aider reports no cache breakdown: cached is always 0
    return {"input": inp, "output": out, "cached": 0, "total": inp + out}


@pytest.fixture()
def seeded_log(tmp_path, monkeypatch):
    """~/.aider/analytics.jsonl exercising every parsing rule.

    Expected outcome (days=30):
      today   gemini/gemini-2.5-pro: inp=10006, out=81
      yest.   claude/claude-sonnet-4-5 via aider/ prefix: inp=500, out=900
      old(40d) -> skipped; wrong event / malformed / no-props -> ignored
    """
    monkeypatch.setenv("USAGE_DASH_HOME", str(tmp_path))
    now = time.time()
    log = tmp_path / ".aider" / "analytics.jsonl"
    log.parent.mkdir(parents=True)
    lines = [
        _event("gemini/gemini-2.5-pro", 10006, 81, now),
        _event("claude-sonnet-4-5", 500, 900, now - 86400),
        # outside the window
        _event("gpt-4.1-mini", 9999, 9999, now - 40 * 86400),
        # wrong event type (carries the prefilter substring) -> ignored
        json.dumps({"event": "message_send_other", "properties": {},
                    "time": now}),
        # malformed JSON carrying the prefilter substring -> tolerated
        '{"event":"message_send","properties":{"prompt_tokens":',
        # missing properties dict -> counted as malformed
        json.dumps({"event": "message_send", "time": now}),
        # unusable timestamp -> counted as malformed
        _event("o3-mini", 10, 5, None),
        # non-message_send line without the substring -> never even parsed
        json.dumps({"event": "app_exit", "time": now}),
    ]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sources._cache.clear()
    yield {"log": str(log), "today": _day(0), "yesterday": _day(1)}
    sources._cache.clear()


# ----------------------------------------------------------------- tests ----


def test_scan_buckets_and_model_attribution(seeded_log):
    res = aider.scan(days=30)

    assert res["available"] is True
    assert res["days"] == 30

    exp_today = {k: _bucket(10006, 81)[k] + _bucket(500, 900)[k]
                 for k in ("input", "output", "cached", "total")}
    assert res["totals"] == exp_today
    assert res["totals"]["total"] == \
        res["totals"]["input"] + res["totals"]["output"]

    # two models on different days — exact daily split
    assert res["daily"] == {
        seeded_log["today"]: _bucket(10006, 81),
        seeded_log["yesterday"]: _bucket(500, 900),
    }

    # models key uses the aider/<main_model> convention
    assert res["models"] == {
        "aider/gemini/gemini-2.5-pro": _bucket(10006, 81),
        "aider/claude-sonnet-4-5": _bucket(500, 900),
    }

    # prefilter passes 6 lines; the message_send_other decoy never matches
    # '"message_send"'; 2 events used, 1 old + 2 malformed dropped
    assert res["meta"] == {"lines_scanned": 6, "events_used": 2,
                           "skipped_old": 1, "malformed": 2}


def test_window_boundary(seeded_log):
    r7 = aider.scan(days=7)
    assert r7["meta"]["events_used"] == 2
    sources._cache.clear()
    r90 = aider.scan(days=90)
    assert r90["meta"]["events_used"] == 3          # old event now inside
    assert "aider/gpt-4.1-mini" in r90["models"]
    assert r90["daily"][seeded_log["today"]]["input"] == 10006


def test_missing_file_unavailable(tmp_path, monkeypatch):
    """The common case: analytics are opt-in, file absent -> graceful."""
    monkeypatch.setenv("USAGE_DASH_HOME", str(tmp_path))
    sources._cache.clear()
    res = aider.scan(days=30)
    assert res["available"] is False
    assert "opt-in" in res["error"]
    assert res["totals"] == {"input": 0, "output": 0, "cached": 0, "total": 0}
    assert res["daily"] == {} and res["models"] == {}
    sources._cache.clear()


def test_config_dir_fallback(tmp_path, monkeypatch):
    """No ~/.aider log but ~/.config/aider/analytics.jsonl present."""
    monkeypatch.setenv("USAGE_DASH_HOME", str(tmp_path))
    now = time.time()
    log = tmp_path / ".config" / "aider" / "analytics.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(_event("deepseek/deepseek-chat", 700, 33, now) + "\n",
                   encoding="utf-8")
    sources._cache.clear()
    res = aider.scan(days=30)
    assert res["available"] is True
    assert res["models"] == {"aider/deepseek/deepseek-chat":
                             _bucket(700, 33)}
    sources._cache.clear()


def test_empty_file_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_DASH_HOME", str(tmp_path))
    log = tmp_path / ".aider" / "analytics.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("", encoding="utf-8")
    sources._cache.clear()
    res = aider.scan(days=30)
    assert res["available"] is False
    assert res["error"] == "no message_send events in window"
    sources._cache.clear()
