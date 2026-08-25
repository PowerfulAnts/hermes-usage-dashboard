"""Hermes-only token usage aggregation for the Usage dashboard.

Pure stdlib module. NO FastAPI, no side effects at import. Single data source:
Hermes' own sqlite session store (``session_model_usage``), which covers every
provider tokens were used through INSIDE Hermes (nous, openrouter,
openai-codex, command-code-go, custom, ...). External CLI tools (Codex CLI,
Gemini CLI, ...) are intentionally NOT counted — this dashboard is scoped to
what ran inside the Hermes app itself.

CACHE-TOKEN SEMANTICS (verified against Hermes' writer code 2026-08-25)
  agent/usage_pricing.py::normalize_usage subtracts cached tokens from the
  prompt total before persisting, so inside ``session_model_usage``:
      prompt = input_tokens + cache_read_tokens + cache_write_tokens
      input_tokens EXCLUDES cache; cached rides OUTSIDE input.
  Cache hit rate = cached / prompt. Providers that never report cache
  metadata (e.g. command-code-go rows with zero cache columns) show "—"
  rather than a fake 0%.

NOTES FROM PREVIOUS CODEX AGENTS (agent-to-agent):
- The grouped SUM over session_model_usage is milliseconds-cheap (~200 live
  rows); no TTL cache or background scan threads are needed. If this ever
  gets slow on some machine, add a short TTL keyed by days here — do NOT
  reintroduce background scan threads for this query.
- Hermes sqlite can be write-locked by a running backend: open mode=ro URI
  first, fall back to copying db+wal+shm to a temp file.
- All day buckets are LOCAL days from last_seen (matches how the user reads
  their own usage).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone

HOME = os.path.expanduser("~")

UNKNOWN_PROVIDER_LABEL = "unknown"

_EMPTY_BUCKET = {
    "input": 0,
    "output": 0,
    "cached": 0,
    "cache_write": 0,
    "total": 0,
    "api_calls": 0,
}

QUERY = """
    SELECT date(last_seen,'unixepoch','localtime') AS day,
           billing_provider,
           SUM(input_tokens), SUM(output_tokens),
           SUM(cache_read_tokens), SUM(cache_write_tokens),
           SUM(reasoning_tokens), SUM(api_call_count)
    FROM session_model_usage
    WHERE last_seen > ?
    GROUP BY day, billing_provider"""


def _db_path() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")
        return os.path.join(base, "hermes", "state.db")
    return os.path.join(HOME, ".hermes", "state.db")


def _bucket() -> dict:
    return dict(_EMPTY_BUCKET)


def _bump(bucket: dict, inp: int, outp: int, cach_read: int, cach_write: int,
          reason: int, calls: int) -> None:
    bucket["input"] += inp
    bucket["cached"] += cach_read
    bucket["cache_write"] += cach_write
    # Reasoning tokens ride with output for display parity.
    bucket["output"] += outp + reason
    bucket["total"] += inp + outp + reason
    bucket["api_calls"] += calls


def hit_rate(bucket_or_totals: dict):
    """cached / (input + cached + cache_write) as percent, or None = unknown.

    None when there is no prompt volume at all OR when the provider never
    reported cache metadata (all cache columns zero) — showing a hard 0%
    there would be misleading.
    """
    inp = int(bucket_or_totals.get("input") or 0)
    cached = int(bucket_or_totals.get("cached") or 0)
    written = int(bucket_or_totals.get("cache_write") or 0)
    prompt = inp + cached + written
    if prompt <= 0 or not (cached or written):
        return None
    return round(cached / prompt * 100.0, 1)


def collect(days: int = 30) -> dict:
    """Aggregate Hermes' own token usage by provider for the last ``days`` days."""
    res = {
        "available": True,
        "days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "totals": _bucket(),
        "daily": {},      # local day -> bucket
        "providers": {},  # billing_provider -> bucket (+ hit_rate_pct)
    }
    db_path = _db_path()
    if not os.path.exists(db_path):
        res["available"] = False
        res["error"] = f"no db at {db_path}"
        return res
    cutoff = time.time() - days * 86400

    conn = None
    rows = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        rows = conn.execute(QUERY, (cutoff,)).fetchall()
    except Exception:
        # Windows WAL lock fallback: copy db (+wal/shm) to temp and read there.
        conn = None
        tmpdir = tempfile.mkdtemp(prefix="usage_dash_")
        try:
            base = os.path.join(tmpdir, "state.db")
            shutil.copy2(db_path, base)
            for ext in ("-wal", "-shm"):
                src = db_path + ext
                if os.path.exists(src):
                    shutil.copy2(src, base + ext)
            conn = sqlite3.connect(base, timeout=5)
            rows = conn.execute(QUERY, (cutoff,)).fetchall()
        except Exception as e:
            res["available"] = False
            res["error"] = str(e)
            return res
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            shutil.rmtree(tmpdir, ignore_errors=True)

    try:
        groups = 0
        for day, provider, inp, outp, cach_read, cach_write, reason, calls in rows or []:
            prov = (provider or "").strip() or UNKNOWN_PROVIDER_LABEL
            args = (inp or 0, outp or 0, cach_read or 0, cach_write or 0,
                    reason or 0, calls or 0)
            _bump(res["providers"].setdefault(prov, _bucket()), *args)
            _bump(res["daily"].setdefault(day or "", _bucket()), *args)
            _bump(res["totals"], *args)
            groups += 1
        res["meta"] = {"groups": groups}
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    res["totals"]["hit_rate_pct"] = hit_rate(res["totals"])
    for p in res["providers"].values():
        p["hit_rate_pct"] = hit_rate(p)
    for d in res["daily"].values():
        d["hit_rate_pct"] = hit_rate(d)
    return res


def summary(days: int = 30) -> dict:
    """Public API consumed by plugin_api.py."""
    return {"hermes": collect(days)}
