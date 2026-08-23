"""Roo Code (VS Code extension) — task histories.

Same on-disk shape as Cline (shared lineage): per-task dirs under
<globalStorage>/rooveterinaryinc.roo-cline/tasks/<taskId>/ containing
ui_messages.json with api_req_started events whose stringified `text`
carries {tokensIn, tokensOut, cacheReads, cacheWrites, cost?} — one event
per API request (DELTA semantics, sum directly; ts = epoch MILLISECONDS).
Model comes from <model>…</model> tags inside api_conversation_history.json,
fallback "auto".

See adapters/cline.py for the full field documentation.
"""

import json
import os
import time
from datetime import datetime

import sources
from _util import home

NAME = "roo"
LABEL = "Roo Code"
BADGE = "VS Code"
HOMEPAGE = "https://roocode.com"
ORDER = 23

_ID_DIRS = ("rooveterinaryinc.roo-cline",)


def _global_storage_roots():
    h = home()
    if os.name == "nt":
        base = os.path.join(h, "AppData", "Roaming")
    elif os.uname().sysname == "Darwin":
        base = os.path.join(h, "Library", "Application Support")
    else:
        base = os.path.join(h, ".config")
    return [os.path.join(base, v, "User", "globalStorage")
            for v in ("Code", "Code - Insiders", "VSCodium")]


def _task_dirs():
    out = []
    for root in _global_storage_roots():
        tasks_root = os.path.join(root, *_ID_DIRS, "tasks")
        try:
            for name in sorted(os.listdir(tasks_root)):
                p = os.path.join(tasks_root, name)
                if os.path.isdir(p):
                    out.append(p)
        except OSError:
            continue
    return out


# Reuse the Cline parsing verbatim — identical event format. Importing the
# sibling keeps ONE implementation of the tricky bits (<model> tag scan,
# usage payload parse); the registry still sees two independent adapters.
from cline import _task_model, _usage_of  # noqa: E402


def scan(days: int = 30) -> dict:
    res = sources.empty_result(days)
    tasks = _task_dirs()
    if not tasks:
        res["available"] = False
        res["error"] = "no Roo Code task dirs found (VS Code globalStorage)"
        return res
    cutoff_s = time.time() - days * 86400          # getmtime() is SECONDS
    cutoff_ms = cutoff_s * 1000.0                  # event `ts` fields are MS
    scanned = events = 0
    for task in tasks:
        try:
            if os.path.getmtime(task) < cutoff_s - 86400:
                continue
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
            day = datetime.utcfromtimestamp(ts / 1000.0).strftime("%Y-%m-%d")
            events += 1
            sources.add(res["daily"], res["models"], res["totals"], day,
                        f"roo/{model}", u[0], u[1], u[2])
    res["meta"] = {"tasks_scanned": scanned, "events_used": events}
    if scanned == 0:
        res["available"] = False
        res["error"] = "no Roo Code task files in window"
    return res
