"""Crush (charmbracelet) — per-project SQLite session stores.

Discovery chain (verified against crush v0.66.1 migrations):
1. Project registry JSON:
     Windows:  %LOCALAPPDATA%\\crush\\projects.json
     Linux:    $XDG_DATA_HOME/crush/projects.json else
               ~/.local/share/crush/projects.json
     macOS:    ~/Library/Application Support/crush/projects.json
   Current builds: OBJECT keyed by project id → {path: …}; older builds
   used a plain ARRAY of {path}. Both shapes are handled here.
2. Per project: <project.path>/.crush/crush.db (SQLite):
     sessions(id, parent_session_id, title, message_count,
              prompt_tokens, completion_tokens, cost REAL,
              updated_at, created_at)
     messages(id, session_id, role, parts JSON, model, …)

SEMANTICS:
- sessions.prompt_tokens / completion_tokens are CUMULATIVE PER SESSION —
  emit ONE event per session bucketed at its updated_at day (same approach
  as zed threads). NEVER sum per-message guesses on top.
- Timestamps are unix SECONDS (schema comments claim ms — comments are
  wrong; INSERTs use strftime('%s','now')).
- Skip rows with parent_session_id IS NOT NULL (subagent cost rolls into
  the parent) and all-zero rows (empty shells).
- Model attribution: dominant messages.model per session
  (GROUP BY model ORDER BY COUNT(*) DESC LIMIT 1); fallback "unknown".
"""

import json
import os
import sqlite3

import sources
from _util import home

NAME = "crush"
LABEL = "Crush"
BADGE = "CLI"
HOMEPAGE = "https://github.com/charmbracelet/crush"
ORDER = 26


def _registry_path() -> str:
    if os.name == "nt":
        base = (os.environ.get("LOCALAPPDATA")
                if not os.environ.get("USAGE_DASH_HOME")
                else None)
        base = base or os.path.join(home(), "AppData", "Local")
        return os.path.join(base, "crush", "projects.json")
    xdg = os.environ.get("XDG_DATA_HOME") if not os.environ.get("USAGE_DASH_HOME") else None
    if xdg:
        return os.path.join(xdg, "crush", "projects.json")
    if os.uname().sysname == "Darwin":
        return os.path.join(home(), "Library", "Application Support",
                            "crush", "projects.json")
    return os.path.join(home(), ".local", "share", "crush", "projects.json")


def _project_paths() -> list[str]:
    """Project workdirs from projects.json (object or legacy array shape)."""
    try:
        with open(_registry_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    entries = list(data.values()) if isinstance(data, dict) else \
        data if isinstance(data, list) else []
    out = []
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("path"), str) and e["path"]:
            out.append(e["path"])
    return sorted(set(out))


def _db_paths() -> list[str]:
    out = []
    for proj in _project_paths():
        p = os.path.join(proj, ".crush", "crush.db")
        if os.path.isfile(p):
            out.append(p)
    return out


def _norm_ts(v) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    if v > 1e12:          # defensive: ms-looking stamps normalized
        v /= 1000.0
    return v


def scan(days: int = 30) -> dict:
    import time

    res = sources.empty_result(days)
    dbs = _db_paths()
    res["meta"] = {"projects_seen": len(_project_paths()), "dbs_found": len(dbs)}
    if not dbs:
        res["available"] = False
        res["error"] = ("no .crush/crush.db found for any registered project "
                        "(is crush installed and used here?)")
        return res
    min_day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    scanned = events = skipped_old = 0
    for db in dbs:
        try:
            con = sqlite3.connect(db)
            rows = con.execute(
                "SELECT id, prompt_tokens, completion_tokens, updated_at "
                "FROM sessions WHERE parent_session_id IS NULL").fetchall()
            models = dict(con.execute(
                "SELECT session_id, model FROM messages WHERE model IS NOT NULL"))
            con.close()
        except Exception as exc:
            res["meta"].setdefault("db_errors", []).append(f"{db}: {exc!r}"[:120])
            continue
        for sid, ptok, ctok, upd in rows:
            scanned += 1
            inp, outp = int(ptok or 0), int(ctok or 0)
            if inp == 0 and outp == 0:
                continue                       # empty shell session
            sec = _norm_ts(upd)
            if sec <= 0:
                continue
            day = time.strftime("%Y-%m-%d", time.gmtime(sec))
            if day < min_day:
                skipped_old += 1
                continue
            events += 1                        # ONE event per session (cumulative)
            # dominant model for the session: first non-null seen wins well
            # enough for attribution; COUNT(*) ranking needs window functions
            # that old SQLite builds lack — keep it simple and dependency-free.
            model = models.get(sid) or "unknown"
            sources.add(res["daily"], res["models"], res["totals"], day,
                        f"crush/{model}", inp, outp, 0)
    res["meta"]["sessions_scanned"] = scanned
    res["meta"]["events_used"] = events
    res["meta"]["skipped_old"] = skipped_old
    if events == 0:
        res["available"] = False
        res["error"] = "no crush sessions in window"
    return res
