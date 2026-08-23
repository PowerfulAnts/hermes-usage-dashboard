"""OpenAI Codex CLI — rollout transcripts.

Data: ~/.codex/sessions/**/<date>T*.jsonl and ~/.codex/archived_sessions/*.jsonl.

SEMANTICS (critical, verified against real files):
- `token_count` events carry info.last_token_usage = per-event DELTA (sum
  these) and info.total_token_usage = CUMULATIVE (never sum — you would get
  nonsense numbers many times too large).
- The model can change MID-FILE; the authoritative model for each token_count
  event is the most recent preceding turn_context line.
- Rollouts are huge (GBs over months): every line is substring-prefiltered
  before json.loads, and file selection uses the date in the filename.
"""

import glob
import os

import sources
from _util import home, tail_text, iter_lines, load_json, recent_files

NAME = "codex"
LABEL = "OpenAI Codex CLI"
BADGE = "Codex CLI"
ORDER = 10
# The bridge ledger re-reports this same traffic; within DEDUPE_GROUP "codex"
# the LOWER COMBINED_PRIORITY feeds combined totals → Codex rollouts win.
COMBINED_PRIORITY = 100



def scan(days: int = 30) -> dict:
    res = sources.empty_result(days)
    codex_dir = os.path.join(home(), ".codex")
    root = os.path.join(codex_dir, "sessions")
    if not os.path.isdir(root):
        res["available"] = False
        res["error"] = f"no dir {root}"
        return res
    files = recent_files(
        [os.path.join(root, "**", "*.jsonl"),
         os.path.join(codex_dir, "archived_sessions", "*.jsonl")],
        days, recursive=True)
    scanned = events = 0
    for path in files:
        scanned += 1
        model = None
        for line in iter_lines(path, ('"token_count"', '"turn_context"')):
            o = load_json(line)
            if not isinstance(o, dict):
                continue
            p = o.get("payload") or {}
            if o.get("type") == "turn_context" and p.get("model"):
                model = p["model"]
                continue
            if p.get("type") != "token_count":
                continue
            info = p.get("info")
            if not isinstance(info, dict):
                continue
            u = info.get("last_token_usage") or {}   # DELTA — see module docstring
            ts = (o.get("timestamp") or "")[:10]
            if not ts:
                continue
            events += 1
            sources.add(res["daily"], res["models"], res["totals"], ts,
                        f"codex/{model or 'unknown'}",
                        int(u.get("input_tokens") or 0),
                        int(u.get("output_tokens") or 0)
                        + int(u.get("reasoning_output_tokens") or 0),
                        int(u.get("cached_input_tokens") or 0))
    res["meta"] = {"files_scanned": scanned, "events_used": events}
    if scanned == 0:
        res["available"] = False
        res["error"] = "no rollout files in window"
    return res


def limits(max_files: int = 40) -> dict:
    """Latest rate-limit snapshot Codex itself writes into rollouts (no API).

    token_count events carry payload.rate_limits: primary {used_percent,
    window_minutes(10080=weekly), resets_at}, optional secondary window,
    credits.balance, plan_type. Scan newest files first, tail-read only.
    """
    codex_dir = os.path.join(home(), ".codex")
    root_sessions = os.path.join(codex_dir, "sessions")
    if not os.path.isdir(root_sessions):
        return {"available": False}
    try:
        files = sorted(
            glob.glob(os.path.join(root_sessions, "**", "*.jsonl"), recursive=True)
            + glob.glob(os.path.join(codex_dir, "archived_sessions", "*.jsonl")),
            key=lambda f: os.path.getmtime(f), reverse=True)[:max_files]
    except Exception:
        return {"available": False}
    best = None  # (timestamp, rate_limits)
    for path in files:
        tail = tail_text(path, 400_000)
        for line in tail.split("\n"):
            if '"rate_limits"' not in line or '"token_count"' not in line:
                continue
            o = load_json(line)
            if not isinstance(o, dict):
                continue
            rl = (o.get("payload") or {}).get("rate_limits")
            if isinstance(rl, dict) and isinstance(rl.get("primary"), dict):
                ts = o.get("timestamp") or ""
                if best is None or ts > best[0]:
                    best = (ts, rl)
        if best:
            break  # newest file that has a real snapshot wins
    if not best:
        return {"available": False}
    ts, rl = best
    prim = rl.get("primary") or {}
    sec = rl.get("secondary") or {}
    credits = rl.get("credits") or {}
    out = {
        "available": True,
        "plan_type": rl.get("plan_type"),
        "observed_at": ts,
        "windows": [],
        "credit_balance": float(credits["balance"]) if str(credits.get("balance") or "").replace(".", "", 1).isdigit() else None,
    }
    if prim:
        out["windows"].append({
            "name": "Weekly" if (prim.get("window_minutes") or 0) >= 10000 else "5-hour",
            "used_pct": prim.get("used_percent"),
            "window_minutes": prim.get("window_minutes"),
            "resets_at": prim.get("resets_at"),
        })
    if sec:
        out["windows"].append({
            "name": "Secondary",
            "used_pct": sec.get("used_percent"),
            "window_minutes": sec.get("window_minutes"),
            "resets_at": sec.get("resets_at"),
        })
    return out
