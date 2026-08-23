"""command-code-go bridge ledger — live monitoring view.

Data: ~/.hermes/usage-ledger.jsonl — one JSON line per completed response,
written fire-and-forget by the local bridge (~/.codex/command-code-*-go-bridge.mjs).

OVERLAP WARNING: bridge traffic ALSO appears inside Codex rollouts (Codex
writes its own transcript), so this adapter shares DEDUPE_GROUP "codex" with
lower COMBINED_PRIORITY: the combined view counts it only when no Codex data
exists, while its card always shows for live monitoring.
"""

import json
import os

import sources
from _util import home, cutoff_day

NAME = "ledger"
LABEL = "Bridge ledger"
BADGE = "live"
ORDER = 50
# Overlap: bridge traffic also appears inside Codex rollouts. Same
# DEDUPE_GROUP as the codex adapter, HIGHER COMBINED_PRIORITY → its card
# still renders, but combined totals count this traffic only when no Codex
# data exists. See sources.combined().
DEDUPE_GROUP = "codex"
COMBINED_PRIORITY = 200


def fingerprint(days: int = 30):
    """(mtime, size) of the ledger file — cheap; it grows line by line."""
    path = os.path.join(home(), ".hermes", "usage-ledger.jsonl")
    try:
        st = os.stat(path)
        return (st.st_mtime, st.st_size)
    except OSError:
        return None


def scan(days: int = 30) -> dict:
    res = sources.empty_result(days)
    res["meta"]["overlap_note"] = ("bridge traffic also appears in codex "
                                   "sessions; ledger shown separately for live monitoring")
    path = os.path.join(home(), ".hermes", "usage-ledger.jsonl")
    if not os.path.exists(path):
        res["available"] = False
        res["error"] = "ledger not created yet (bridge writes on next use)"
        return res
    min_day = cutoff_day(days)
    n = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                day = (e.get("ts") or "")[:10]
                if not day or day < min_day:
                    continue
                n += 1
                sources.add(res["daily"], res["models"], res["totals"], day,
                            f"bridge/{e.get('model') or 'unknown'}",
                            int(e.get("input_tokens") or 0),
                            int(e.get("output_tokens") or 0),
                            int(e.get("cached_tokens") or 0))
    except OSError as exc:
        res["available"] = False
        res["error"] = str(exc)
        return res
    res["meta"]["entries"] = n
    if n == 0:
        res["available"] = False
        res["error"] = "no entries in window"
    return res
