"""Hermes-only token usage + provider account status for Usage dashboard.

TOKEN SCOPE (user-directed, do not broaden)
  The ONLY token source is Hermes' own sqlite ``session_model_usage`` table.
  External CLI histories (Codex rollouts, Gemini chats, Command Code sessions,
  etc.) are NEVER scanned for token totals.

ACCOUNT STATUS (independent from token scope)
  Trackable limits / credits / funds are intentionally restored:
  - OpenAI Codex subscription windows: newest local rate-limit snapshot only
  - Command Code Go: live billing credits + 5-hour/weekly windows
  - OpenRouter: live key spend / key credit limit via GET /api/v1/key
  - Nous Portal: supplied by plugin_api via Hermes' own billing model
  Network-backed status is cached for five minutes so the UI's 20-second token
  refresh does not repeatedly hit provider APIs.

CACHE-TOKEN SEMANTICS (verified against Hermes' writer code 2026-08-25)
  ``agent/usage_pricing.py::normalize_usage`` subtracts cached tokens before
  persistence, so:
      prompt = input_tokens + cache_read_tokens + cache_write_tokens
      hit_rate = cache_read_tokens / prompt
  Providers that never report cache metadata show null / "—", not fake 0%.

NOTES FROM PREVIOUS CODEX AGENTS (agent-to-agent, not human instructions):
- The grouped sqlite SUM is milliseconds-cheap; do not cache token totals.
- Windows sqlite can be write-locked: open mode=ro first, then copy db+wal+shm.
- Day buckets are local days from last_seen.
- A limit collector failing must never break token usage or another provider.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import sqlite3
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
UNKNOWN_PROVIDER_LABEL = "unknown"
LIMITS_TTL_SECONDS = 300.0

_EMPTY_BUCKET = {
    "input": 0,
    "output": 0,
    "cached": 0,
    "cache_write": 0,
    "total": 0,
    "api_calls": 0,
    "cost_usd": 0.0,
}

QUERY = """
    SELECT date(last_seen,'unixepoch','localtime') AS day,
           billing_provider,
           SUM(input_tokens), SUM(output_tokens),
           SUM(cache_read_tokens), SUM(cache_write_tokens),
           SUM(reasoning_tokens), SUM(api_call_count),
           SUM(estimated_cost_usd), SUM(actual_cost_usd)
    FROM session_model_usage
    WHERE last_seen > ?
    GROUP BY day, billing_provider"""

_limits_cache: tuple[float, dict] | None = None


def _db_path() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")
        return os.path.join(base, "hermes", "state.db")
    return os.path.join(HOME, ".hermes", "state.db")


def _bucket() -> dict:
    return dict(_EMPTY_BUCKET)


def _bump(bucket: dict, inp: int, outp: int, cach_read: int, cach_write: int,
          reason: int, calls: int, est_cost: float = 0.0,
          act_cost: float = 0.0) -> None:
    bucket["input"] += inp
    bucket["cached"] += cach_read
    bucket["cache_write"] += cach_write
    # Reasoning tokens ride with output for display parity.
    bucket["output"] += outp + reason
    bucket["total"] += inp + outp + reason
    bucket["api_calls"] += calls
    # Hermes prices every request itself (actual when the provider reports a
    # cost, estimated from its price table otherwise). Prefer actual spend.
    bucket["cost_usd"] += round((act_cost if act_cost else est_cost) or 0.0, 4)


def hit_rate(bucket_or_totals: dict):
    """cached / (input + cached + cache_write) as percent.

    A used provider with no cached tokens is a real 0.0% hit rate. ``None`` is
    reserved for a period with no prompt tokens at all.
    """
    inp = int(bucket_or_totals.get("input") or 0)
    cached = int(bucket_or_totals.get("cached") or 0)
    written = int(bucket_or_totals.get("cache_write") or 0)
    prompt = inp + cached + written
    if prompt <= 0:
        return None
    return round(cached / prompt * 100.0, 1)


def collect(days: int = 30) -> dict:
    """Aggregate ONLY Hermes' own token usage, grouped by billing provider."""
    res = {
        "available": True,
        "days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "totals": _bucket(),
        "daily": {},
        "providers": {},
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
        except Exception as exc:
            res["available"] = False
            res["error"] = str(exc)
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
        for day, provider, inp, outp, cach_read, cach_write, reason, calls, est, act in rows or []:
            prov = (provider or "").strip() or UNKNOWN_PROVIDER_LABEL
            args = (inp or 0, outp or 0, cach_read or 0, cach_write or 0,
                    reason or 0, calls or 0, est or 0.0, act or 0.0)
            _bump(res["providers"].setdefault(prov, _bucket()), *args)
            _bump(res["daily"].setdefault(day or "", _bucket()), *args)
            _bump(res["totals"], *args)
            groups += 1
        res["meta"] = {"groups": groups, "scope": "hermes-only"}
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    res["totals"]["hit_rate_pct"] = hit_rate(res["totals"])
    for item in res["providers"].values():
        item["hit_rate_pct"] = hit_rate(item)
    for item in res["daily"].values():
        item["hit_rate_pct"] = hit_rate(item)
    return res


# --------------------------------------------------------- account status ----


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def _configured_secret(name: str):
    """Return one named secret from process env or Hermes .env; never log it."""
    direct = os.environ.get(name)
    if direct:
        return direct
    if os.name == "nt":
        env_path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", ".env")
    else:
        env_path = os.path.join(HOME, ".hermes", ".env")
    try:
        with open(env_path, encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() == name:
                    return value.strip().strip("\"'") or None
    except OSError:
        pass
    return None


def _get_json(url: str, headers: dict, timeout: int = 8):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _tail_text(path: str, max_bytes: int = 400_000) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            return handle.read()
    except OSError:
        return ""


def codex_limits(max_files: int = 40) -> dict:
    """Latest Codex subscription snapshot; tails metadata only, no token scan."""
    codex_dir = os.path.join(HOME, ".codex")
    sessions = os.path.join(codex_dir, "sessions")
    if not os.path.isdir(sessions):
        return {"available": False}
    try:
        files = sorted(
            glob.glob(os.path.join(sessions, "**", "*.jsonl"), recursive=True)
            + glob.glob(os.path.join(codex_dir, "archived_sessions", "*.jsonl")),
            key=os.path.getmtime,
            reverse=True,
        )[:max_files]
    except Exception:
        return {"available": False}

    best = None
    for path in files:
        for line in _tail_text(path).split("\n"):
            if '"rate_limits"' not in line or '"token_count"' not in line:
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            limits = (event.get("payload") or {}).get("rate_limits")
            if isinstance(limits, dict) and isinstance(limits.get("primary"), dict):
                stamp = event.get("timestamp") or ""
                if best is None or stamp > best[0]:
                    best = (stamp, limits)
        if best:
            break
    if not best:
        return {"available": False}

    stamp, limits = best
    windows = []
    for index, window in enumerate((limits.get("primary"), limits.get("secondary"))):
        if not isinstance(window, dict):
            continue
        minutes = int(window.get("window_minutes") or 0)
        if index == 0:
            name = "Weekly" if minutes >= 10_000 else "5-hour"
        else:
            name = "Secondary"
        windows.append({
            "name": name,
            "used_pct": window.get("used_percent"),
            "window_minutes": minutes or None,
            "resets_at": window.get("resets_at"),
        })
    credits = limits.get("credits") or {}
    balance = credits.get("balance")
    try:
        balance = float(balance) if balance is not None else None
    except (TypeError, ValueError):
        balance = None
    return {
        "available": bool(windows) or balance is not None,
        "label": "OpenAI Codex",
        "badge": limits.get("plan_type") or "subscription",
        "observed_at": stamp,
        "windows": windows,
        "credit_balance": balance,
    }


def parse_commandcode_limits(data: dict) -> dict:
    windows = []
    raw_windows = data.get("windowLimits") or {}
    for key, label in (("fiveHour", "5-hour"), ("weekly", "Weekly")):
        item = raw_windows.get(key) or {}
        used, cap = item.get("used"), item.get("cap")
        if used is None or not cap:
            continue
        reset_ms = item.get("resetAt") or 0
        windows.append({
            "name": label,
            "used": used,
            "cap": cap,
            "used_pct": round(float(used) / float(cap) * 100.0, 1),
            "resets_at": int(reset_ms / 1000) if reset_ms else None,
            "exceeded": bool(item.get("exceeded")),
        })
    credits = data.get("credits") or {}
    return {
        "available": bool(windows) or bool(credits),
        "label": "Command Code Go",
        "badge": "Go plan",
        "windows": windows,
        "monthly_credits_remaining": credits.get("monthlyCredits"),
        "purchased_credits": credits.get("purchasedCredits"),
        "free_credits": credits.get("freeCredits"),
    }


def commandcode_limits() -> dict:
    auth = _read_json(os.path.join(HOME, ".commandcode", "auth.json")) or {}
    key = auth.get("apiKey")
    if not key:
        return {"available": False}
    try:
        data = _get_json(
            "https://api.commandcode.ai/alpha/billing/credits",
            {
                "Authorization": "Bearer " + key,
                "x-api-key": key,
                "User-Agent": "command-code/1.32.1",
                "x-cli-version": "1.32.1",
                "x-cli-environment": "production",
            },
        )
        return parse_commandcode_limits(data if isinstance(data, dict) else {})
    except Exception as exc:
        return {"available": False, "error": type(exc).__name__}


def parse_openrouter_limits(data: dict) -> dict:
    item = data.get("data") if isinstance(data.get("data"), dict) else data
    limit = item.get("limit")
    remaining = item.get("limit_remaining")
    windows = []
    if limit not in (None, 0) and remaining is not None:
        used = max(0.0, float(limit) - float(remaining))
        windows.append({
            "name": "Key credit limit",
            "used": used,
            "cap": limit,
            "used_pct": round(used / float(limit) * 100.0, 1),
            "resets_display": item.get("limit_reset") or "Never",
        })
    available = any(key in item for key in (
        "limit", "limit_remaining", "usage", "usage_daily", "usage_weekly", "usage_monthly"
    ))
    return {
        "available": available,
        "label": "OpenRouter",
        "badge": "free tier" if item.get("is_free_tier") else "API key",
        "windows": windows,
        "credit_limit": limit,
        "credit_remaining": remaining,
        "usage_all_time": item.get("usage"),
        "usage_daily": item.get("usage_daily"),
        "usage_weekly": item.get("usage_weekly"),
        "usage_monthly": item.get("usage_monthly"),
    }


def openrouter_limits() -> dict:
    key = _configured_secret("OPENROUTER_API_KEY")
    if not key:
        return {"available": False}
    try:
        data = _get_json(
            "https://openrouter.ai/api/v1/key",
            {"Authorization": "Bearer " + key, "User-Agent": "hermes-usage-dashboard/3.1"},
        )
        return parse_openrouter_limits(data if isinstance(data, dict) else {})
    except Exception as exc:
        return {"available": False, "error": type(exc).__name__}


def collect_limits(force: bool = False) -> dict:
    """Provider quotas / funds, cached 5m; failures are isolated per provider."""
    global _limits_cache
    if not force and _limits_cache and time.time() - _limits_cache[0] < LIMITS_TTL_SECONDS:
        return _limits_cache[1]

    collectors = {
        "openai-codex": codex_limits,
        "command-code-go": commandcode_limits,
        "openrouter": openrouter_limits,
    }
    providers = {}
    with ThreadPoolExecutor(max_workers=len(collectors)) as pool:
        futures = {name: pool.submit(fn) for name, fn in collectors.items()}
        for name, future in futures.items():
            try:
                providers[name] = future.result()
            except Exception as exc:
                providers[name] = {"available": False, "error": type(exc).__name__}
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_ttl_seconds": int(LIMITS_TTL_SECONDS),
        "providers": providers,
    }
    _limits_cache = (time.time(), out)
    return out
