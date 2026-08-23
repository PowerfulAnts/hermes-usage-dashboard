"""Amp (Sourcegraph) — local thread mirrors.

Data: ``~/.local/share/amp/threads/T-<uuid>.json`` (Windows honors
``%USERPROFILE%\\.local\\share\\amp\\threads\\``; $XDG_DATA_HOME style
override via env is NOT used by amp itself). One JSON object per thread::

    {"v": 2, "id": "T-…", "created": "<ISO-8601>", "messages": […], …}

SEMANTICS (format is UNDOCUMENTED — treat as brittle, guard everything):
- Assistant messages carry Anthropic-shape usage:
    message.usage.{input_tokens, output_tokens,
                   cache_creation_input_tokens, cache_read_input_tokens}
  These are per-response DELTAS → sum across messages.
- Per-message timestamps are NOT confirmed in the file format, so the
  thread's top-level `created` timestamp anchors the day bucket: all of a
  thread's tokens land on its start day. Documented clustering caveat.
- Anthropic cache semantics → fold caches into input like claudecode.py:
  input = input_tokens + creation + read; cached = creation + read.
"""

import glob
import json
import os
import time

import sources
from _util import home

NAME = "amp"
LABEL = "Amp"
BADGE = "Sourcegraph"
HOMEPAGE = "https://ampcode.com"
ORDER = 27


def _threads_dir() -> str:
    return os.path.join(home(), ".local", "share", "amp", "threads")


def _iso_to_epoch(v) -> float:
    """Parse ISO-8601 (or unix s/ms) to seconds; 0.0 when unusable."""
    if isinstance(v, (int, float)):
        v = float(v)
        return v / 1000.0 if v > 1e12 else v
    if isinstance(v, str) and v:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return 0.0
    return 0.0


def _usage_of(msg: dict):
    """(inp, out, cach) from one message's Anthropic-shape usage, or None."""
    u = msg.get("usage")
    if not isinstance(u, dict):
        return None
    tin, tout = u.get("input_tokens"), u.get("output_tokens")
    if not isinstance(tin, (int, float)) and not isinstance(tout, (int, float)):
        return None
    cr = int(u.get("cache_read_input_tokens") or 0)
    cw = int(u.get("cache_creation_input_tokens") or 0)
    cach = cr + cw
    return int(tin or 0) + cach, int(tout or 0), cach


def scan(days: int = 30) -> dict:
    res = sources.empty_result(days)
    tdir = _threads_dir()
    pattern = os.path.join(tdir, "T-*.json")
    try:
        files = sorted(glob.glob(pattern))
    except Exception:
        files = []
    if not files:
        res["available"] = False
        res["error"] = f"no amp threads found at {tdir}"
        return res
    min_day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    cutoff_s = time.time() - days * 86400
    scanned = events = skipped_old = malformed = 0
    for path in files:
        o = None
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                o = json.load(fh)
        except Exception:
            malformed += 1
            continue
        if not isinstance(o, dict):
            malformed += 1
            continue
        created = _iso_to_epoch(o.get("created"))
        if created <= 0:
            malformed += 1
            continue
        if created < cutoff_s:
            skipped_old += 1
            continue
        day = time.strftime("%Y-%m-%d", time.gmtime(created))
        msgs = o.get("messages")
        if not isinstance(msgs, list):
            continue
        scanned += 1
        model = "unknown"
        # best-effort model name from any assistant message that carries one
        for msg in msgs:
            if isinstance(msg, dict) and isinstance(msg.get("model"), str):
                model = msg["model"]
                break
        counted = False
        for msg in msgs:
            if not isinstance(msg, dict):
                continue
            u = _usage_of(msg)
            if u is None:
                continue
            events += 1
            counted = True
            sources.add(res["daily"], res["models"], res["totals"], day,
                        f"amp/{model}", u[0], u[1], u[2])
        if not counted:
            scanned -= 1   # a thread without any usable usage isn't evidence
    res["meta"] = {"threads_scanned": scanned, "events_used": events,
                   "skipped_old": skipped_old, "malformed": malformed}
    if scanned == 0 and events == 0:
        res["available"] = False
        res["error"] = "no amp threads in window"
    return res
