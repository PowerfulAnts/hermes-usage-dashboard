"""OpenCode CLI — SQLite usage store.

Data: ``~/.local/share/opencode/opencode.db`` (v1.14+). OpenCode honors
xdg-basedir conventions, so on Windows the store lands at
``%USERPROFILE%\\.local\\share\\opencode\\opencode.db`` and
``$XDG_DATA_HOME`` overrides the base dir entirely. Legacy (<v1.14) kept
one JSON file per message under ``<dataDir>/storage/session/message/``;
we only read the modern SQLite store.

Schema (Drizzle ORM): ``session``, ``message``(id, session_id,
time_created, `data` JSON), ``part``(id, message_id, session_id, `data`).
Only ``message`` carries usage. Its ``data`` payload looks like::

    {"role": "assistant", "modelID": "claude-sonnet-4.6",
     "providerID": "anthropic", "cost": 0,
     "tokens": {"total": N, "input": N, "output": N, "reasoning": N,
                "cache": {"read": N, "write": N}}}

SEMANTICS (where we parse tokens):
- Every assistant row is ONE billed response → DELTA semantics; sum rows.
- OpenCode reports Anthropic-style counts: ``tokens.input`` EXCLUDES cache.
  Fold cache.read+cache.write into the contract's "input" bucket so
  total == input + output stays comparable across providers:
      input  = tokens.input + cache.read + cache.write
      output = tokens.output + tokens.reasoning   (reasoning rides in output)
      cached = cache.read + cache.write           (informational)
- ``time_created`` is unix SECONDS; older builds stored MILLISECONDS, so
  any value > 1e12 is treated as ms (defensive normalization).
"""

import json
import os
import sqlite3
import time

import sources
from _util import home, cutoff_day

NAME = "opencode"
LABEL = "OpenCode"
BADGE = "CLI"
HOMEPAGE = "https://opencode.ai"
ORDER = 23


def _db_path() -> str:
    """opencode.db location honoring $XDG_DATA_HOME."""
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return os.path.join(xdg, "opencode", "opencode.db")
    return os.path.join(home(), ".local", "share", "opencode", "opencode.db")


def _norm_ts(v) -> float:
    """time_created -> unix seconds ('' -> 0 when unusable).

    Defensive: some builds wrote milliseconds; >1e12 can only be ms.
    """
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    if v > 1e12:
        v /= 1000.0
    return v


def _usage_of(data_json) -> tuple | None:
    """Parse a message.data payload -> (inp, out, cach, modelID) or None."""
    if isinstance(data_json, (bytes, bytearray)):
        try:
            data_json = data_json.decode("utf-8", "replace")
        except Exception:
            return None
    try:
        o = json.loads(data_json) if isinstance(data_json, str) else data_json
    except Exception:
        return None
    if not isinstance(o, dict) or o.get("role") != "assistant":
        return None
    t = o.get("tokens")
    if not isinstance(t, dict):
        return None
    inp = int(t.get("input") or 0)
    out = int(t.get("output") or 0)
    reason = int(t.get("reasoning") or 0)
    cache = t.get("cache") if isinstance(t.get("cache"), dict) else {}
    cr = int(cache.get("read") or 0)
    cw = int(cache.get("write") or 0)
    cach = cr + cw
    model = o.get("modelID") or "unknown"
    return inp + cach, out + reason, cach, str(model)


def scan(days: int = 30) -> dict:
    res = sources.empty_result(days)
    db = _db_path()
    if not os.path.isfile(db):
        res["available"] = False
        res["error"] = f"no db {db}"
        return res
    try:
        con = sqlite3.connect(db)
        cur = con.execute(
            "SELECT id, session_id, time_created, data FROM message")
        rows = cur.fetchall()
        con.close()
    except Exception as exc:  # corrupt/unexpected schema — never raise
        res["available"] = False
        res["error"] = f"cannot read opencode.db: {exc}"
        return res

    min_day = cutoff_day(days)
    scanned = events = skipped_old = 0
    for _mid, _sid, ts, data_json in rows:
        scanned += 1
        u = _usage_of(data_json)
        if u is None:
            continue
        sec = _norm_ts(ts)
        if sec <= 0:
            continue
        day = time.strftime("%Y-%m-%d", time.gmtime(sec))
        if day < min_day:
            skipped_old += 1
            continue
        events += 1
        sources.add(res["daily"], res["models"], res["totals"], day,
                    f"opencode/{u[3]}", u[0], u[1], u[2])
    res["meta"] = {"messages_scanned": scanned, "events_used": events,
                   "skipped_old": skipped_old}
    if events == 0:
        res["available"] = False
        res["error"] = "no assistant messages in window"
    return res
