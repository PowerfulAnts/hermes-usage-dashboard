"""Command Code CLI — session transcripts + live billing limits.

Data: ~/.commandcode/projects/*/<uuid>.jsonl. Assistant lines carry top-level
`model` and `usage` {inputTokens, outputTokens, cacheReadTokens,
cacheWriteTokens, costUsd}. Each line is one API response DELTA — sum them
directly.

limits(): live call to the Command Code billing API with the key stored by
`commandcode login` (~/.commandcode/auth.json). Auth requires BOTH an
x-api-key-style Authorization Bearer header AND CLI identity headers, or you
get 403.
"""

import os

import sources
from _util import home, http_get_json, iter_lines, load_json, recent_files

NAME = "commandcode"
LABEL = "Command Code"
BADGE = "Go plan"
ORDER = 30



def scan(days: int = 30) -> dict:
    res = sources.empty_result(days)
    projects_dir = os.path.join(home(), ".commandcode", "projects")
    if not os.path.isdir(projects_dir):
        res["available"] = False
        res["error"] = f"no dir {projects_dir}"
        return res
    files = [p for p in recent_files(
        [os.path.join(projects_dir, "*", "*.jsonl")], days)
        if "checkpoints" not in os.path.basename(p)]
    scanned = events = 0
    for path in files:
        scanned += 1
        for line in iter_lines(path, ('"usage"',)):
            o = load_json(line)
            if not isinstance(o, dict):
                continue
            u = o.get("usage")
            if not isinstance(u, dict):
                continue
            ts = (o.get("timestamp") or "")[:10]
            if not ts:
                continue
            events += 1
            sources.add(res["daily"], res["models"], res["totals"], ts,
                        f"cmdcode/{o.get('model') or 'unknown'}",
                        int(u.get("inputTokens") or 0),
                        int(u.get("outputTokens") or 0),
                        int(u.get("cacheReadTokens") or 0))
    res["meta"] = {"files_scanned": scanned, "events_used": events}
    if scanned == 0:
        res["available"] = False
        res["error"] = "no session files in window"
    return res


def limits() -> dict:
    """Live window limits via GET /alpha/billing/credits.

    Returns {credits:{monthlyCredits,…}, windowLimits:{fiveHour:{used,cap,
    exceeded,resetAt}, weekly:{…}}}.
    """
    auth = _read_auth()
    key = (auth or {}).get("apiKey")
    if not key:
        return {"available": False}
    data = http_get_json(
        "https://api.commandcode.ai/alpha/billing/credits",
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": "command-code/1.32.1",       # required, else 403
            "x-cli-version": "1.32.1",
            "x-cli-environment": "production",
        })
    if not isinstance(data, dict):
        return {"available": False, "error": "billing endpoint unreachable"}
    wl = data.get("windowLimits") or {}
    windows = []
    for key_name, label in (("fiveHour", "5-hour"), ("weekly", "Weekly")):
        w = wl.get(key_name) or {}
        used, cap = w.get("used"), w.get("cap")
        if used is None or not cap:
            continue
        reset_ms = w.get("resetAt") or 0
        windows.append({
            "name": label,
            "used": used,
            "cap": cap,
            "used_pct": round(used / cap * 100, 1),
            "resets_at": int(reset_ms / 1000) if reset_ms else None,
            "exceeded": bool(w.get("exceeded")),
        })
    credits = data.get("credits") or {}
    if not windows and not credits:
        return {"available": False}
    return {
        "available": True,
        "plan_type": "Go",
        "windows": windows,
        "monthly_credits_remaining": credits.get("monthlyCredits"),
        "purchased_credits": credits.get("purchasedCredits"),
    }


def _read_auth() -> dict | None:
    import json
    try:
        with open(os.path.join(home(), ".commandcode", "auth.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None
