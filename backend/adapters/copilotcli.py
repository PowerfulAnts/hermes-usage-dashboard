"""GitHub Copilot CLI — session-store usage events.

Data: ``~/.copilot/session-store.db`` (SQLite/WAL, written unconditionally
by Copilot CLI >= 1.0.x and the Copilot desktop app).

Table ``assistant_usage_events``: ONE ROW PER API REQUEST → DELTA semantics,
with real timestamps and per-model attribution.

CRITICAL SEMANTICS (verified against community schema docs):
- ``input_tokens`` in this table is CACHE-INCLUSIVE (= input + cache_read +
  cache_write). We emit the UNCACHED remainder as the contract's "input" and
  report the cache parts in "cached" (folded inside input for the total, like
  every other adapter — total stays input + output).
- The exact column set has shifted between builds. We PRAGMA the table first
  and adapt: required = a timestamp column (created_at/created_at_ms/
  timestamp), a model column (model/model_name), input_tokens, output_tokens;
  optional = cache_read_tokens/cache_read_input_tokens,
  cache_write_tokens/cache_creation_input_tokens. If required columns are
  missing we degrade to available:false with the observed columns in meta.
- WAL: copy db (+ -wal/-shm) to a temp dir before opening when the live
  store is locked (same pattern as the hermes adapter).
"""

import os
import shutil
import sqlite3
import tempfile
import time

import sources
from _util import home

NAME = "copilotcli"
LABEL = "Copilot CLI"
BADGE = "GitHub"
HOMEPAGE = "https://docs.github.com/copilot"
ORDER = 28

_TS_CANDIDATES = ("created_at", "timestamp", "created_at_ms", "ts")
_MODEL_CANDIDATES = ("model", "model_name", "model_id")
_CACHE_READ_CANDIDATES = ("cache_read_tokens", "cache_read_input_tokens",
                          "cached_read_tokens")
_CACHE_WRITE_CANDIDATES = ("cache_write_tokens", "cache_creation_input_tokens",
                           "cached_write_tokens")


def _db_path() -> str:
    return os.path.join(home(), ".copilot", "session-store.db")


def _open_copy(db: str):
    """Copy db+wal+shm to temp and open (avoids WAL locks). Returns conn."""
    tmpdir = tempfile.mkdtemp(prefix="usage_dash_copilot_")
    base = os.path.join(tmpdir, "session-store.db")
    shutil.copy2(db, base)
    for ext in ("-wal", "-shm"):
        if os.path.exists(db + ext):
            shutil.copy2(db + ext, base + ext)
    return sqlite3.connect(base), tmpdir


def _column_map(cur) -> dict | None:
    """Map semantic roles to actual column names; None when incompatible."""
    cols = [r[1] for r in cur.execute("PRAGMA table_info(assistant_usage_events)")]
    if not cols:
        return None

    def first(*cands):
        for c in cands:
            if c in cols:
                return c
        return None

    m = {
        "ts": first(*_TS_CANDIDATES),
        "model": first(*_MODEL_CANDIDATES),
        "in": "input_tokens" if "input_tokens" in cols else None,
        "out": "output_tokens" if "output_tokens" in cols else None,
        "cr": first(*_CACHE_READ_CANDIDATES),
        "cw": first(*_CACHE_WRITE_CANDIDATES),
        "cols": cols,
    }
    if not (m["ts"] and m["model"] and m["in"] and m["out"]):
        return m  # caller inspects and degrades
    return m


def scan(days: int = 30) -> dict:
    res = sources.empty_result(days)
    db = _db_path()
    if not os.path.isfile(db):
        res["available"] = False
        res["error"] = f"no session-store at {db}"
        return res
    conn = tmpdir = None
    try:
        try:
            conn = sqlite3.connect(db)
            conn.execute("SELECT count(*) FROM assistant_usage_events").fetchone()
        except Exception:
            if conn:
                conn.close()
            conn, tmpdir = _open_copy(db)
        cmap = _column_map(conn.cursor())
        if not cmap or not (cmap["ts"] and cmap["model"] and cmap["in"] and cmap["out"]):
            res["available"] = False
            res["error"] = ("assistant_usage_events schema not recognized; "
                            f"columns seen: {', '.join((cmap or {}).get('cols', []))}")
            return res
        sel = (f'SELECT "{cmap["ts"]}", "{cmap["model"]}", '
               f'"{cmap["in"]}", "{cmap["out"]}"'
               + (f', "{cmap["cr"]}"' if cmap["cr"] else "")
               + (f', "{cmap["cw"]}"' if cmap["cw"] else "")
               + " FROM assistant_usage_events")
        rows = conn.execute(sel).fetchall()
    except Exception as exc:
        res["available"] = False
        res["error"] = f"cannot read session-store.db: {exc!r}"[:180]
        return res
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    min_day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    scanned = events = skipped_old = malformed = 0
    for row in rows:
        scanned += 1
        ts_v, model, tin, tout = row[0], row[1], row[2], row[3]
        cr = row[4] if cmap["cr"] else 0
        cw = row[5] if cmap["cw"] else 0
        # timestamp → seconds (defensive ms normalization)
        try:
            sec = float(ts_v)
        except (TypeError, ValueError):
            malformed += 1
            continue
        if sec > 1e12:
            sec /= 1000.0
        if sec <= 0:
            malformed += 1
            continue
        day = time.strftime("%Y-%m-%d", time.gmtime(sec))
        if day < min_day:
            skipped_old += 1
            continue
        tin = int(tin or 0)
        tout = int(tout or 0)
        cr = int(cr or 0)
        cw = int(cw or 0)
        cach = cr + cw
        uncached_in = max(0, tin - cach)   # stored input is CACHE-INCLUSIVE
        events += 1
        sources.add(res["daily"], res["models"], res["totals"], day,
                    f"copilot/{model or 'unknown'}",
                    uncached_in + cach, tout, cach)
    res["meta"] = {"rows_scanned": scanned, "events_used": events,
                   "skipped_old": skipped_old, "malformed": malformed,
                   "columns": cmap["cols"]}
    if events == 0:
        res["available"] = False
        res["error"] = "no usage events in window"
    return res
