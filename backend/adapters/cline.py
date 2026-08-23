"""Cline (VS Code extension + standalone CLI) — task histories.

Data (per install variant, first existing root wins per task found):
  <globalStorage>/saoudrizwan.claude-dev/tasks/<taskId>/
  standalone CLI: ~/.cline/data/tasks/<taskId>/
globalStorage locations: %APPDATA%/<Code|Code - Insiders|VSCodium>/User/
globalStorage (Windows), ~/.config/... (Linux), ~/Library/Application
Support/... (macOS).

Files per task:
- ui_messages.json — array of events; the ones we parse look like
  {"ts": 1785701064304, "type": "say", "say": "api_req_started",
   "text": "<JSON-stringified>"}
  where text parses to {tokensIn, tokensOut, cacheReads, cacheWrites,
  cost?, apiProtocol}. ONE entry per API request → DELTA semantics (sum).
  ts = epoch MILLISECONDS.
- api_conversation_history.json — full prompt history; user-message text
  blocks embed the configured model as a <model>…</model> tag. Model is
  per-task config, so all of a task's requests share it (last tag wins);
  fallback "auto".

SEMANTICS: every api_req_started event is one billed request attempt
(retries included) — sum directly. cached = cacheReads (+ cacheWrites,
which ride in the input bucket like other Anthropic-style sources).
"""

import json
import os
import re

import sources
from _util import home

NAME = "cline"
LABEL = "Cline"
BADGE = "VS Code"
HOMEPAGE = "https://cline.bot"
ORDER = 22

_MODEL_RE = re.compile(r"<model>([^<]{1,120})</model>")

_ID_DIRS = ("saoudrizwan.claude-dev",)  # globalStorage extension folder


def _global_storage_roots():
    """All VS Code-family globalStorage dirs for this OS."""
    h = home()
    if os.name == "nt":
        base = os.path.join(h, "AppData", "Roaming")
        variants = ["Code", "Code - Insiders", "VSCodium"]
    elif os.name == "posix" and os.uname().sysname == "Darwin":
        base = os.path.join(h, "Library", "Application Support")
        variants = ["Code", "Code - Insiders", "VSCodium"]
    else:
        base = os.path.join(h, ".config")
        variants = ["Code", "Code - Insiders", "VSCodium"]
    return [os.path.join(base, v, "User", "globalStorage") for v in variants]


def _task_dirs():
    """Every Cline task dir visible on this machine."""
    roots = [os.path.join(r, *_ID_DIRS, "tasks") for r in _global_storage_roots()]
    roots.append(os.path.join(home(), ".cline", "data", "tasks"))
    out = []
    for root in roots:
        try:
            for name in sorted(os.listdir(root)):
                p = os.path.join(root, name)
                if os.path.isdir(p):
                    out.append(p)
        except OSError:
            continue
    return out


def _task_model(task_dir: str) -> str:
    """Configured model for a task: last <model> tag in the history, else auto."""
    try:
        with open(os.path.join(task_dir, "api_conversation_history.json"),
                  encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except Exception:
        return "auto"
    if not isinstance(data, list):
        return "auto"
    model = None
    for msg in data:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        blocks = content if isinstance(content, list) else [content]
        for b in blocks:
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                m = _MODEL_RE.findall(b["text"])
                if m:
                    model = m[-1]
    return model or "auto"


def _usage_of(text_json: str):
    """Parse the stringified payload of an api_req_started event."""
    try:
        o = json.loads(text_json)
    except Exception:
        return None
    if not isinstance(o, dict):
        return None
    tin = o.get("tokensIn")
    tout = o.get("tokensOut")
    if not isinstance(tin, (int, float)) and not isinstance(tout, (int, float)):
        return None  # no usable counters (e.g. error entries)
    cr = o.get("cacheReads") or 0
    cw = o.get("cacheWrites") or 0
    cach = int(cr or 0) + int(cw or 0)
    inp = int(tin or 0) + cach  # cache rides in input, like other sources
    return inp, int(tout or 0), cach


def scan(days: int = 30) -> dict:
    res = sources.empty_result(days)
    tasks = _task_dirs()
    if not tasks:
        res["available"] = False
        res["error"] = "no Cline task dirs found (VS Code globalStorage / ~/.cline)"
        return res
    import time
    cutoff_s = time.time() - days * 86400          # getmtime() is SECONDS
    cutoff_ms = cutoff_s * 1000.0                  # event `ts` fields are MS
    scanned = events = 0
    for task in tasks:
        try:
            if os.path.getmtime(task) < cutoff_s - 86400:
                continue  # cheap pre-filter; per-event ts below is authoritative
        except OSError:
            continue
        try:
            with open(os.path.join(task, "ui_messages.json"),
                      encoding="utf-8", errors="replace") as fh:
                data = json.load(fh)
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        scanned += 1
        model = _task_model(task)
        for ev in data:
            if not isinstance(ev, dict) or ev.get("say") != "api_req_started":
                continue
            ts = ev.get("ts")
            if not isinstance(ts, (int, float)) or ts < cutoff_ms:
                continue
            u = _usage_of(ev.get("text") or "")
            if u is None:
                continue
            day = __import__("datetime").datetime.utcfromtimestamp(
                ts / 1000.0).strftime("%Y-%m-%d")
            events += 1
            sources.add(res["daily"], res["models"], res["totals"], day,
                        f"cline/{model}", u[0], u[1], u[2])
    res["meta"] = {"tasks_scanned": scanned, "events_used": events}
    if scanned == 0:
        res["available"] = False
        res["error"] = "no Cline task files in window"
    return res
