"""Ollama — local model runner (http://127.0.0.1:11434).

VERDICT (verified 2026-08-23 against public evidence; see docstring notes at
the bottom): Ollama keeps NO durable, attributable usage history. Its server
log carries token counts only as bare llama.cpp perf lines that have NO
timestamp and NO model identity, and the log itself is truncated/redirected
across restarts ("semi-persistent"). We therefore CANNOT fill the uniform
result shape (per-day buckets, per-model buckets, N-day window) honestly.

Per the dashboard's anti-fabrication rule this adapter ships with scan()
returning available=False + a meta note explaining WHY, so the UI shows a
muted card instead of invented numbers. If Ollama ever adds a persisted usage
store or timestamps its runner output, revisit this file.

What we verified about the data sources:
- REST API (/api/ps, /api/generate, /api/chat) reports only LIVE state;
  final_usage/prompt_eval_count/eval_count exist per-response but nothing is
  persisted server-side between requests.
- Server log locations: ~/.ollama/logs/server.log (macOS/Linux app),
  %LOCALAPPDATA%\\Ollama\\server.log on Windows (= <home>/AppData/Local/…).
- The log DOES contain, at default log level:
    a) gin request lines WITH timestamps but NO counts and NO model name:
       `[GIN] 2025/10/06 - 13:34:13 | 200 | 1.67s | 127.0.0.1 | POST "/api/generate"`
    b) llama.cpp runner perf blocks WITH counts but NO timestamp and NO model:
       `prompt eval count:   62 token(s)` / `eval count:  4792 token(s)`
  (a)+(b) can only be paired heuristically by adjacency, model attribution is
  impossible, and the file does not survive restarts as history.
"""

import os
import re

import sources
from _util import home

NAME = "ollama"
LABEL = "Ollama (local models)"
BADGE = "local"
HOMEPAGE = "https://ollama.com"
ORDER = 90

_GIN_RE = re.compile(r"\[GIN\]\s+\d{4}/\d{2}/\d{2}")
_PROMPT_EVAL_RE = re.compile(r"prompt eval count:\s*(\d+)\s*token")
_EVAL_COUNT_RE = re.compile(r"(?<!prompt )eval count:\s*(\d+)\s*token")

NOTE_NO_HISTORY = (
    "no persisted history: Ollama stores no usage records server-side; its "
    "log mixes timestamped request lines (no token counts) with token-count "
    "perf lines (no timestamp, no model) and is truncated across restarts, "
    "so tokens cannot be attributed to days or models"
)


def _candidate_logs():
    h = home()
    local_app_data = os.environ.get("LOCALAPPDATA") or os.path.join(h, "AppData", "Local")
    return [
        os.path.join(h, ".ollama", "logs", "server.log"),
        os.path.join(local_app_data, "Ollama", "server.log"),
        os.path.join(h, ".ollama", "logs", "app.log"),
        os.path.join(local_app_data, "Ollama", "app.log"),
    ]


def _inspect(path: str) -> tuple[int, int]:
    """(gin request lines, runner perf blocks) found in one log file."""
    gin_lines = prompt_evals = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if _GIN_RE.search(line):
                    gin_lines += 1
                elif _PROMPT_EVAL_RE.search(line):
                    prompt_evals += 1
    except OSError:
        return 0, 0
    return gin_lines, prompt_evals


def scan(days: int = 30) -> dict:
    """Always reports unavailable — see module docstring for the evidence.

    We still locate and inspect the log so meta carries real diagnostic
    numbers (requests seen, inference blocks seen) instead of a bare error.
    """
    res = sources.empty_result(days)
    present = [p for p in _candidate_logs() if os.path.isfile(p)]
    requests_seen = blocks_seen = 0
    for path in present:
        g, b = _inspect(path)
        requests_seen += g
        blocks_seen += b
    res["meta"] = {
        "note": NOTE_NO_HISTORY,
        "log_files_found": len(present),
        "requests_logged": requests_seen,
        "inference_blocks_seen": blocks_seen,
    }
    if not present:
        res["error"] = "Ollama not detected (no server log found)"
    else:
        res["error"] = NOTE_NO_HISTORY
    res["available"] = False
    return res
