"""Shared helpers for adapter files (import as: from _util import …).

Pure stdlib. These exist so each provider adapter stays small and readable.
"""

from __future__ import annotations

import glob
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser("~")


def home() -> str:
    """User home dir for THIS run.

    Adapters must resolve every path through this (at scan time, not import
    time): tests redirect it via the USAGE_DASH_HOME env var to a synthetic
    fixture tree, so real machine data is never touched by the suite.
    """
    return os.environ.get("USAGE_DASH_HOME") or HOME


DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")


def day_of(ts: str | None) -> str:
    """'2026-08-23T09:11:02Z' -> '2026-08-23' ('' when missing)."""
    return (ts or "")[:10]


def cutoff_day(days: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))


def recent_files(patterns: list[str], days: int, recursive: bool = False,
                 name_date_slack_days: int = 2) -> list[str]:
    """Expand glob patterns to files plausibly inside the day window.

    Uses filename date (YYYY-MM-DD) when present, else mtime. Missing dirs /
    unreadable entries are silently skipped — adapters stay non-raising.
    """
    min_name = (datetime.now(timezone.utc) - timedelta(days=days + max(0, name_date_slack_days))).strftime("%Y-%m-%d")
    min_mtime = time.time() - (days + 1) * 86400
    out: list[str] = []
    for pat in patterns:
        try:
            paths = glob.glob(pat, recursive=recursive)
        except Exception:
            continue
        for p in paths:
            m = DATE_IN_NAME.search(os.path.basename(p))
            if m:
                if m.group(1) >= min_name:
                    out.append(p)
                continue
            try:
                if os.path.getmtime(p) >= min_mtime:
                    out.append(p)
            except OSError:
                continue
    return sorted(set(out))


def iter_lines(path: str, must_contain: tuple[str, ...] = ()):
    """Yield raw lines of a JSONL file; optionally substring-prefiltered.

    The prefilter keeps json.loads off the overwhelming majority of lines in
    huge transcripts (Codex rollouts can be GBs).
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if must_contain and not any(s in line for s in must_contain):
                    continue
                yield line
    except OSError:
        return


def load_json(line: str):
    """json.loads that returns None instead of raising."""
    try:
        return json.loads(line)
    except Exception:
        return None


def read_json_file(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def http_get_json(url: str, headers: dict | None = None, timeout: int = 15):
    """Small GET helper returning parsed JSON or None (never raises)."""
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "hermes-usage-dashboard/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def tail_text(path: str, max_bytes: int = 400_000) -> str:
    """Last max_bytes of a file as text (for 'newest snapshot wins' scans)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            return fh.read()
    except OSError:
        return ""
