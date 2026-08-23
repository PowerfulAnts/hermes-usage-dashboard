"""Gemini CLI — chat transcripts.

Data: ~/.gemini/tmp/<hash>/chats/session-*.jsonl (recursive). Assistant lines
carry tokens:{input,output,cached,thoughts,tool,total} plus a per-message
model and timestamp. The sibling chats/<uuid>/ SUBDIRS are separate subagent
sessions with zero message overlap — they MUST be included; the thousands of
tool-output .txt files elsewhere in tmp/ are not transcripts.

Some lines lack `tokens` or a timestamp — guard everything.
"""

import glob
import os
import time

import sources
from _util import home, iter_lines, load_json

NAME = "gemini"
LABEL = "Gemini CLI"
BADGE = "Gemini CLI"
ORDER = 20


def fingerprint(days: int = 30):
    """(max mtime, total bytes) over in-window chat files — cheap."""
    chats_dir = os.path.join(home(), ".gemini", "tmp")
    try:
        paths = glob.glob(os.path.join(chats_dir, "*", "chats", "**", "*.jsonl"),
                          recursive=True)
    except Exception:
        return None
    cutoff_mtime = time.time() - (days + 1) * 86400
    latest = 0.0
    total = 0
    seen = False
    for path in set(paths):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_mtime < cutoff_mtime:
            continue
        seen = True
        if st.st_mtime > latest:
            latest = st.st_mtime
        total += st.st_size
    return (latest, total) if seen else None


def scan(days: int = 30) -> dict:
    res = sources.empty_result(days)
    chats_dir = os.path.join(home(), ".gemini", "tmp")
    if not os.path.isdir(chats_dir):
        res["available"] = False
        res["error"] = f"no dir {chats_dir}"
        return res
    pattern = os.path.join(chats_dir, "*", "chats", "**", "*.jsonl")
    try:
        paths = glob.glob(pattern, recursive=True)
    except Exception:
        paths = []
    cutoff_mtime = time.time() - (days + 1) * 86400
    files = []
    for path in sorted(set(paths)):
        try:
            if os.path.getmtime(path) >= cutoff_mtime:
                files.append(path)
        except OSError:
            continue
    scanned = events = 0
    for path in files:
        scanned += 1
        model = None
        for line in iter_lines(path, ('"tokens"',)):
            o = load_json(line.strip())
            if not isinstance(o, dict) or o.get("type") != "gemini":
                continue
            tok = o.get("tokens")
            if not isinstance(tok, dict):
                continue
            if o.get("model"):
                model = o["model"]
            ts = (o.get("timestamp") or "")[:10]
            if not ts:
                continue
            events += 1
            sources.add(res["daily"], res["models"], res["totals"], ts,
                        model or "gemini-cli",
                        int(tok.get("input") or 0),
                        int(tok.get("output") or 0) + int(tok.get("thoughts") or 0),
                        int(tok.get("cached") or 0))
    res["meta"] = {"files_scanned": scanned, "events_used": events}
    if scanned == 0:
        res["available"] = False
        res["error"] = "no session files in window"
    return res
