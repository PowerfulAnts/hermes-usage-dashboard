"""Hermes desktop app — its own sqlite session store.

Covers the Nous provider plus every provider Hermes routed through
(openrouter, openai-codex, command-code-go, …). Rows are already per-(day,
model, provider) aggregates; reasoning tokens ride with output like on other
sources.

Windows note: a running Hermes backend can hold the DB write-lock, so open
read-only via URI first and fall back to copying db+wal+shm to a temp dir.
"""

import os
import shutil
import sqlite3
import tempfile
import time

import sources
from _util import home

NAME = "hermes"
LABEL = "Hermes"
BADGE = "Nous + routed"
ORDER = 40


_QUERY = """
    SELECT date(last_seen,'unixepoch','localtime') AS day,
           model, billing_provider,
           SUM(input_tokens), SUM(output_tokens),
           SUM(cache_read_tokens), SUM(reasoning_tokens)
    FROM session_model_usage
    WHERE last_seen > ?
    GROUP BY day, model, billing_provider"""


def fingerprint(days: int = 30):
    """(max mtime, size, row count) of the sqlite store — cheap.

    The DB changes on every routed response; stat + a COUNT(*) are far
    cheaper than re-running the aggregate query for nothing.
    """
    db_path = (os.path.join(home(), "AppData", "Local", "hermes", "state.db")
               if os.name == "nt" else os.path.join(home(), ".hermes", "state.db"))
    try:
        st = os.stat(db_path)
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        try:
            (rows,) = conn.execute(
                "SELECT COUNT(*) FROM session_model_usage").fetchone()
        finally:
            conn.close()
        return (st.st_mtime, st.st_size, rows)
    except Exception:
        return None


def scan(days: int = 30) -> dict:
    res = sources.empty_result(days)
    db_path = os.path.join(home(), "AppData", "Local", "hermes", "state.db") \
        if os.name == "nt" else os.path.join(home(), ".hermes", "state.db")
    if not os.path.exists(db_path):
        res["available"] = False
        res["error"] = f"no db at {db_path}"
        return res
    cutoff = time.time() - days * 86400
    conn = None
    rows = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        rows = conn.execute(_QUERY, (cutoff,)).fetchall()
    except Exception:
        conn = None
        tmpdir = tempfile.mkdtemp(prefix="usage_dash_")
        try:
            base = os.path.join(tmpdir, "state.db")
            shutil.copy2(db_path, base)
            for ext in ("-wal", "-shm"):
                src = db_path + ext
                if os.path.exists(src):
                    shutil.copy2(src, base + ext)
            conn = sqlite3.connect(base, timeout=5)
            rows = conn.execute(_QUERY, (cutoff,)).fetchall()
        except Exception as e:
            res["available"] = False
            res["error"] = str(e)
            return res
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            shutil.rmtree(tmpdir, ignore_errors=True)
            conn = None
    try:
        n = 0
        for day, model, provider, inp, outp, cach, reason in rows or []:
            inp = inp or 0; outp = outp or 0; cach = cach or 0
            outp += reason or 0  # reasoning rides with output on other sources
            key = f"{model or 'unknown'} [{provider or 'hermes'}]"
            sources.add(res["daily"], res["models"], res["totals"], day or "", key, inp, outp, cach)
            n += 1
        res["meta"]["rows"] = n
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    return res
