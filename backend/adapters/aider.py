"""Aider (AI pair programming CLI) — opt-in analytics log.

Data: ``~/.aider/analytics.jsonl`` — ONLY written when the user opts in via
``--analytics-log`` / analytics config; the conventional path may vary, so
``~/.config/aider/analytics.jsonl`` is probed as a fallback. Default
artifacts (.aider.chat.history.md etc.) carry NO token counts and are
deliberately not used.

Line format (verified against aider/analytics.py source)::

    {"event": "message_send",
     "properties": {"main_model": "gemini/gemini-2.5-pro",
                    "prompt_tokens": 10006, "completion_tokens": 81,
                    "total_tokens": 10087, "cost": 0.0133,
                    "total_cost": 0.0327},
     "time": 1755100406}

SEMANTICS:
- OpenAI-style field names: ``prompt_tokens`` / ``completion_tokens``
  (NOT input_tokens/output_tokens). One line per sent message → DELTA
  semantics; sum directly.
- Aider reports no cache breakdown → cached = 0,
  input = prompt_tokens, output = completion_tokens.
- ``time`` is unix SECONDS; ``properties.total_cost`` is a cumulative cost
  figure, NOT tokens — ignored here.
- Model key: ``aider/<main_model>``.
- The file is usually ABSENT (opt-in) → graceful available:false.
"""

import json
import os
import time

import sources
from _util import home, iter_lines, load_json, cutoff_day

NAME = "aider"
LABEL = "Aider"
BADGE = "CLI"
HOMEPAGE = "https://aider.chat"
ORDER = 24

_CANDIDATES = (
    os.path.join(".aider", "analytics.jsonl"),
    os.path.join(".config", "aider", "analytics.jsonl"),
)


def _log_path() -> str | None:
    """First existing analytics.jsonl candidate, else None."""
    for rel in _CANDIDATES:
        p = os.path.join(home(), rel)
        if os.path.isfile(p):
            return p
    return None


def scan(days: int = 30) -> dict:
    res = sources.empty_result(days)
    path = _log_path()
    if path is None:
        res["available"] = False
        res["error"] = ("no analytics.jsonl (~/.aider or ~/.config/aider) "
                        "— aider analytics are opt-in")
        return res
    min_day = cutoff_day(days)
    scanned = events = skipped_old = malformed = 0
    for line in iter_lines(path, ('"message_send"',)):
        scanned += 1
        o = load_json(line)
        if not isinstance(o, dict) or o.get("event") != "message_send":
            continue
        props = o.get("properties")
        if not isinstance(props, dict):
            malformed += 1
            continue
        pin = props.get("prompt_tokens")
        pout = props.get("completion_tokens")
        if not isinstance(pin, (int, float)) and \
                not isinstance(pout, (int, float)):
            malformed += 1
            continue
        ts = o.get("time")
        try:
            sec = float(ts)
        except (TypeError, ValueError):
            malformed += 1
            continue
        if sec > 1e12:      # defensive: ms-looking timestamps normalized
            sec /= 1000.0
        day = time.strftime("%Y-%m-%d", time.gmtime(sec))
        if day < min_day:
            skipped_old += 1
            continue
        model = str(props.get("main_model") or "unknown")
        events += 1
        sources.add(res["daily"], res["models"], res["totals"], day,
                    f"aider/{model}", int(pin or 0), int(pout or 0), 0)
    res["meta"] = {"lines_scanned": scanned, "events_used": events,
                   "skipped_old": skipped_old, "malformed": malformed}
    if events == 0:
        res["available"] = False
        res["error"] = "no message_send events in window"
    return res
