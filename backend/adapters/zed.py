"""Zed AI assistant — threads.db conversation store.

Per-platform DB location (first existing wins; probing ALL standard paths
keeps the adapter honest on every OS and lets tests seed any variant):
  Windows: %LOCALAPPDATA%\\Zed\\threads\\threads.db
  Linux:   ~/.local/share/zed/threads/threads.db
  macOS:   ~/Library/Application Support/Zed/threads/threads.db

Table ``threads``(id, summary, updated_at, data_type, data BLOB) where
``data_type`` is ``"zstd"`` (current builds) or ``"json"`` (legacy rows).

KNOWN LIMITATION (deliberate, do NOT "fix" with a dependency): Python's
stdlib has NO zstd decompressor (zlib/gzip/bz2/lzma only — none are zstd).
We therefore parse ONLY ``data_type == 'json'`` rows and count zstd rows
in ``meta.skipped_zstd``. On current Zed installs that means most or all
threads are skipped and the adapter reports available:false with honest
meta counters — a visible partial number would be worse than none.

Decompressed thread JSON:
    {"model": {"provider": ..., "model": ...},
     "request_token_usage": {<msg-id>: {input_tokens, output_tokens,
        cache_creation_input_tokens, cache_read_input_tokens}},
     "cumulative_token_usage": {same shape — whole-thread AUTHORITATIVE}}

SEMANTICS:
- ``cumulative_token_usage`` is the AUTHORITATIVE whole-thread total; the
  per-request map is INCOMPLETE (~3x gap observed upstream). We emit ONE
  event per thread from the cumulative block and skip request_token_usage
  entirely.
- Anthropic token semantics → fold caches into the contract's input bucket
  like claudecode.py: input = input_tokens + creation + read;
  output = output_tokens; cached = creation + read.
- Day comes from thread-level ``updated_at`` (no per-request timestamps).
"""

import json
import os
import sqlite3
import time

import sources
from _util import home, cutoff_day

NAME = "zed"
LABEL = "Zed"
BADGE = "Editor"
HOMEPAGE = "https://zed.dev"
ORDER = 25


def _db_candidates() -> list[str]:
    """All standard per-platform threads.db locations."""
    h = home()
    # %LOCALAPPDATA% normally resolves to <home>/AppData/Local; when the
    # suite redirects home via USAGE_DASH_HOME we keep everything inside
    # the synthetic tree instead of touching the real machine's AppData.
    if os.environ.get("USAGE_DASH_HOME"):
        localappdata = os.path.join(h, "AppData", "Local")
    else:
        localappdata = os.environ.get("LOCALAPPDATA") or \
            os.path.join(h, "AppData", "Local")
    return [
        os.path.join(localappdata, "Zed", "threads", "threads.db"),          # Windows
        os.path.join(h, ".local", "share", "zed", "threads", "threads.db"),  # Linux
        os.path.join(h, "Library", "Application Support", "Zed",
                     "threads", "threads.db"),                               # macOS
    ]


def _usage_of(data_bytes) -> tuple | None:
    """Parse a json-type thread payload.

    Returns (inp, out, cach, model) from cumulative_token_usage, or None.
    """
    try:
        o = json.loads(data_bytes)
    except Exception:
        return None
    if not isinstance(o, dict):
        return None
    cum = o.get("cumulative_token_usage")
    if not isinstance(cum, dict):
        return None
    inp = int(cum.get("input_tokens") or 0)
    out = int(cum.get("output_tokens") or 0)
    ccreate = int(cum.get("cache_creation_input_tokens") or 0)
    cread = int(cum.get("cache_read_input_tokens") or 0)
    model = "unknown"
    m = o.get("model")
    if isinstance(m, dict) and m.get("model"):
        model = str(m["model"])
    cach = ccreate + cread
    return inp + cach, out, cach, model


def _day_of_updated(v) -> str:
    """updated_at -> 'YYYY-MM-DD' ('' when unusable).

    Defensive: schema has carried both unix seconds/milliseconds numbers
    and ISO-8601 strings across builds.
    """
    if isinstance(v, (int, float)):
        sec = float(v)
        if sec > 1e12:
            sec /= 1000.0
        return time.strftime("%Y-%m-%d", time.gmtime(sec))
    s = str(v or "")
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    try:
        sec = float(s)
        if sec > 1e12:
            sec /= 1000.0
        return time.strftime("%Y-%m-%d", time.gmtime(sec))
    except ValueError:
        return ""


def scan(days: int = 30) -> dict:
    res = sources.empty_result(days)
    db = next((p for p in _db_candidates() if os.path.isfile(p)), None)
    if db is None:
        res["available"] = False
        res["error"] = "no Zed threads.db found on this platform"
        return res
    try:
        con = sqlite3.connect(db)
        cur = con.execute(
            "SELECT id, summary, updated_at, data_type, data FROM threads")
        rows = cur.fetchall()
        con.close()
    except Exception as exc:  # corrupt/unexpected schema — never raise
        res["available"] = False
        res["error"] = f"cannot read threads.db: {exc}"
        return res

    min_day = cutoff_day(days)
    scanned = events = skipped_zstd = skipped_other = skipped_old = 0
    for _tid, _summary, updated_at, data_type, data in rows:
        scanned += 1
        if data_type == "zstd":
            # stdlib cannot decompress zstd — counted honestly in meta
            skipped_zstd += 1
            continue
        if data_type != "json":
            skipped_other += 1
            continue
        u = _usage_of(data)
        if u is None:
            continue
        day = _day_of_updated(updated_at)
        if not day or day < min_day:
            skipped_old += 1
            continue
        events += 1
        sources.add(res["daily"], res["models"], res["totals"], day,
                    f"zed/{u[3]}", u[0], u[1], u[2])
    res["meta"] = {"threads_scanned": scanned, "events_used": events,
                   "skipped_zstd": skipped_zstd,
                   "skipped_other": skipped_other, "skipped_old": skipped_old}
    if events == 0:
        res["available"] = False
        reason = (f"{skipped_zstd} zstd-compressed threads skipped "
                  "(stdlib has no zstd)" ) if skipped_zstd else \
                 "no parsable json threads in window"
        res["error"] = reason
    return res
