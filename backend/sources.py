"""Provider-agnostic core of the universal Usage tracker.

Pure stdlib. This module knows NOTHING about specific AI tools: every
provider lives in its own adapter file under ``adapters/`` and is discovered
automatically at import time. Adding support for a new tool = dropping ONE new
file into ``adapters/`` — no edits here, no edits in the frontend.

ADAPTER CONTRACT (see adapters/_contract.py for the annotated template)
  Module-level metadata:
    NAME              unique id, e.g. "codex"            (required)
    LABEL             human display name                  (required)
    BADGE             short UI chip text                  (optional)
    HOMEPAGE          project URL                         (optional)
    ORDER             float; lower sorts earlier in UI    (optional)
    COMBINED_PRIORITY float; lower wins its dedupe group  (optional)
    DEDUPE_GROUP      str; adapters sharing a group overlap
                      (only the best-priority one feeds the
                      combined totals; each still gets its own card)
  Functions:
    scan(days: int) -> result dict                   (required)
    limits() -> dict                                 (optional)

RESULT SHAPE (uniform across ALL adapters):
    {
      "available": bool,
      "days": days,
      "totals": {"input","output","cached","total"},
      "daily":  {"YYYY-MM-DD": {...same...}},
      "models": {"<model key>": {...same...}},
      "meta":   {...adapter-specific scalars...},
    }
    total == input + output always; cached is informational and rides inside
    input on most providers. Adapters must NEVER raise — wrap risky parsing
    and degrade to {"available": False, "error": "..."}.

NOTES FOR FUTURE AGENTS (agent-to-agent, not human instructions):
- Discovery imports every non-underscore *.py in the adapters directory with
  a guard; a broken adapter logs to ``registry_errors`` and is skipped, it
  can never take the dashboard down.
- Adapter modules get unique in-memory module names ("usage_adapter_<stem>")
  so identically-named files in different installs can't collide.
- The 300s TTL cache stays per (adapter NAME, days): heavy scanners are
  memoized via @cached_scan; cheap ones run fresh.
- combined() dedupes overlapping adapters through DEDUPE_GROUP /
  COMBINED_PRIORITY (e.g. bridge-ledger traffic also appears inside Codex
  rollouts — only the higher-priority source feeds combined totals).
"""

from __future__ import annotations

import glob
import importlib.util
import os
import sys
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ADAPTERS_DIR = os.path.join(HERE, "adapters")

# ---------------------------------------------------------------- shape ----


def empty_result(days: int) -> dict:
    return {
        "available": True,
        "days": days,
        "totals": {"input": 0, "output": 0, "cached": 0, "total": 0},
        "daily": {},   # day -> {input, output, cached, total}
        "models": {},  # model key -> {input, output, cached, total}
        "meta": {},
    }


def add(daily: dict, models: dict, totals: dict, day: str, mkey: str,
        inp: int, out: int, cach: int) -> None:
    """Fold one event/bucket into a result's three accumulators."""
    t = inp + out
    d = daily.setdefault(day, {"input": 0, "output": 0, "cached": 0, "total": 0})
    d["input"] += inp; d["output"] += out; d["cached"] += cach; d["total"] += t
    m = models.setdefault(mkey, {"input": 0, "output": 0, "cached": 0, "total": 0})
    m["input"] += inp; m["output"] += out; m["cached"] += cach; m["total"] += t
    totals["input"] += inp; totals["output"] += out; totals["cached"] += cach
    totals["total"] += t


# Back-compat alias (older adapter drafts imported _add / _empty_result).
_add = add
_empty_result = empty_result


# ------------------------------------------------------------ discovery ----

def _load_adapter(path: str):
    """Import one adapter file; returns the module or raises."""
    stem = os.path.splitext(os.path.basename(path))[0]
    modname = f"usage_adapter_{stem}"  # unique: avoids cross-install collisions
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "scan") or not getattr(mod, "NAME", ""):
        raise ImportError(f"{path} has no NAME/scan — not an adapter")
    return mod


def discover_adapters(adapters_dir: str | None = None) -> dict:
    """Scan the adapters dir and load every valid provider module.

    Returns {name: module}. Broken files land in registry_errors and are
    skipped — one bad adapter can never break collection.
    """
    root = adapters_dir or ADAPTERS_DIR
    found: dict[str, object] = {}
    errors: list[dict] = []
    if os.path.isdir(root):
        # Adapters do `from _util import …`: they must be able to import
        # siblings, so the adapters dir itself has to be importable while
        # they execute. (`import sources` works regardless — this module is
        # already in sys.modules by the time anything discovers us.)
        if root not in sys.path:
            sys.path.insert(0, root)
        for path in sorted(glob.glob(os.path.join(root, "*.py"))):
            base = os.path.basename(path)
            if base.startswith("_"):
                continue
            try:
                mod = _load_adapter(path)
                if mod.NAME in found:  # duplicate NAME: keep first, note it
                    errors.append({"adapter": base, "error": f"duplicate NAME '{mod.NAME}' ignored"})
                    continue
                found[mod.NAME] = mod
            except Exception as exc:
                errors.append({"adapter": base, "error": repr(exc)[:200]})
    if errors:
        global REGISTRY_ERRORS
        REGISTRY_ERRORS = errors
    global _adapter_by_name
    _adapter_by_name = dict(found)
    return found


REGISTRY_ERRORS: list[dict] = []
_adapter_by_name: dict[str, object] = {}


# ---------------------------------------------------------------- cache ----

_CACHE_TTL_S = 300.0
_cache: dict[tuple, tuple[float, dict]] = {}
_fingerprints: dict[tuple, object] = {}


def source_fingerprint(name: str):
    """Cheap change-signal for an adapter's input files (None = unknown).

    Adapters may define ``fingerprint()`` returning anything comparable
    (e.g. (max mtime, total size) over their source files). When the value
    CHANGES, a cached scan is invalidated immediately instead of waiting out
    the TTL — that is what makes an active Codex session show up within one
    UI poll instead of up to 5 minutes later. Same value → cached result is
    still fresh regardless of age.
    """
    mod = _adapter_by_name.get(name)
    fn = getattr(mod, "fingerprint", None) if mod else None
    if fn is None:
        return None
    try:
        return fn()
    except Exception:
        return None


def cached_scan(name: str, days: int, fn, *args):
    """Memoize heavy scans for 5 min — but invalidate early on file changes.

    Files are append-only in practice, so within an unchanged fingerprint the
    TTL cache can never be stale beyond the current partial day. The moment
    the adapter's fingerprint() reports different inputs, the cache is
    dropped and rescanned: active sessions appear on the next poll (~20s)
    rather than after the full TTL.
    """
    key = (name, days)
    fp = source_fingerprint(name)
    hit = _cache.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL_S:
        if fp is None or _fingerprints.get(key, fp) == fp:
            return hit[1]
        del _cache[key]  # inputs changed → rescan now
    result = fn(days, *args) if args else fn(days)
    _cache[key] = (time.time(), result)
    if fp is not None:
        _fingerprints[key] = fp
    return result


# ------------------------------------------------- background scan jobs ----
# A cold heavy scan can take 30s+ (GBs of rollouts). Running it inside the
# HTTP request blows past the desktop REST door's 15s default timeout and the
# user sees "Usage data unavailable". Instead /summary serves whatever is
# cached RIGHT NOW (empty-but-valid when nothing is cached yet) and kicks the
# real work into a daemon thread; the UI's 20s poll picks up the finished
# data one or two polls later. Kicking off is cheap: a changed fingerprint
# schedules at most ONE rescan per adapter.

_scan_threads: dict[tuple, "threading.Thread"] = {}
_scan_lock = threading.Lock()


def ensure_scan_scheduled(name: str, days: int, mod) -> None:
    """Start a background scan for (name, days) unless one is already running."""
    key = (name, days)
    with _scan_lock:
        t = _scan_threads.get(key)
        if t is not None and t.is_alive():
            return

        def run():
            try:
                cached_scan(name, days, mod.scan)
            except Exception:
                pass  # adapters never raise; belt & braces for thread safety
            finally:
                with _scan_lock:
                    t = _scan_threads.pop(key, None)

        th = threading.Thread(target=run, name=f"usage-scan-{name}-{days}",
                              daemon=True)
        _scan_threads[key] = th
        th.start()


def needs_rescan(name: str, days: int) -> bool:
    """True when (name, days) has no usable cached result right now."""
    key = (name, days)
    hit = _cache.get(key)
    if not hit or (time.time() - hit[0]) >= _CACHE_TTL_S:
        return True
    fp = source_fingerprint(name)
    return fp is not None and _fingerprints.get(key, fp) != fp


def get_cached(name: str, days: int):
    """The current cached result for (name, days), or None."""
    hit = _cache.get((name, days))
    if hit and (time.time() - hit[0]) < _CACHE_TTL_S * 4:
        return hit[1]
    return None


def _meta_of(mod) -> dict:
    return {
        "label": getattr(mod, "LABEL", mod.NAME),
        "badge": getattr(mod, "BADGE", None),
        "homepage": getattr(mod, "HOMEPAGE", None),
        "order": getattr(mod, "ORDER", 100),
    }


# ------------------------------------------------------------- public API ---

def list_adapters() -> list[str]:
    return sorted(discover_adapters().keys())


def _empty_unavailable(days: int, error: str) -> dict:
    return {"available": False, "days": days,
            "totals": {}, "daily": {}, "models": {}, "meta": {},
            "error": error}


def collect_all(days: int = 30, only: list[str] | None = None,
                background: bool = False) -> dict:
    """Collect every adapter's usage picture.

    With ``background=True`` (the API path) this NEVER runs a heavy scan
    inline: each adapter returns its cached result when fresh (or a recent
    one while a rescan is pending), and stale/missing adapters get their
    scan scheduled on a daemon thread. The response is therefore always
    fast; the UI's next poll picks up freshly scanned data. Cheap adapters
    (no fingerprint) are still allowed to run inline — they're the sqlite
    aggregate + tiny JSONL readers that finish in milliseconds.

    ``background=False`` keeps the old synchronous behavior for tests and
    tools/smoke_local.py.
    """
    global _adapter_by_name
    mods = discover_adapters()
    _adapter_by_name = dict(mods)  # fingerprints resolve through this map
    wanted = set(only) if only else set(mods.keys())
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "sources": {}}
    for name, mod in sorted(mods.items(), key=lambda kv: (getattr(kv[1], "ORDER", 100), kv[0])):
        if name not in wanted:
            continue
        try:
            if background and hasattr(mod, "fingerprint"):
                # Heavy scanner: never scan inline. Serve the freshest cached
                # result (even a slightly-stale one while a rescan runs) and
                # make sure exactly one background job is working on fresh data.
                if needs_rescan(name, days):
                    ensure_scan_scheduled(name, days, mod)
                    res = get_cached(name, days)
                    if res is None:
                        res = _empty_unavailable(
                            days,
                            "scanning in background — first results land within ~30s")
                        res["meta"]["scanning"] = True
                else:
                    res = get_cached(name, days)
            else:
                res = cached_scan(name, days, mod.scan)
            if res is None:
                res = _empty_unavailable(days, "no cached result")
        except Exception as exc:  # belt & braces: adapters shouldn't raise
            res = _empty_unavailable(days, repr(exc)[:200])
        res.setdefault("days", days)
        res.setdefault("meta", {})
        res["meta"]["stale"] = bool(res.get("meta", {}).get("stale")) or (
            background and needs_rescan(name, days))
        # embed display meta so the UI needs no hardcoded provider lists
        res["meta"]["label"] = getattr(mod, "LABEL", name)
        res["meta"]["badge"] = getattr(mod, "BADGE", None)
        res["meta"]["homepage"] = getattr(mod, "HOMEPAGE", None)
        res["meta"]["order"] = getattr(mod, "ORDER", 100)
        out["sources"][name] = res
    out["combined"] = combined(out["sources"], mods=mods)
    if REGISTRY_ERRORS:
        out["registry_errors"] = REGISTRY_ERRORS
    return out


def collect_limits(portal_model: dict | None = None) -> dict:
    """Quota-window snapshots from every adapter that implements limits().

    One failing provider never breaks others. Portal allowance (if provided
    by the host app) is injected by plugin_api under key "nous".
    """
    res = {"providers": {}}
    for name, mod in discover_adapters().items():
        if not hasattr(mod, "limits"):
            continue
        try:
            lim = mod.limits()
        except Exception as exc:
            lim = {"available": False, "error": repr(exc)[:150]}
        if not isinstance(lim, dict) or not lim.get("available"):
            continue
        lim.setdefault("label", getattr(mod, "LABEL", name))
        lim.setdefault("badge", getattr(mod, "BADGE", None))
        res["providers"][name] = lim
    return res


def combined(sources: dict, mods: dict | None = None) -> dict:
    """Merge available sources into one picture.

    Overlapping adapters (same DEDUPE_GROUP) contribute only through the one
    with the lowest COMBINED_PRIORITY — e.g. bridge-ledger lines reappear
    inside Codex rollouts, so the ledger never double-counts while Codex data
    exists. Every source still gets its own card in the UI.
    """
    mods = mods or discover_adapters()

    def prio(name: str) -> float:
        m = mods.get(name)
        return float(getattr(m, "COMBINED_PRIORITY", 1e9)) if m else 1e9

    group_best: dict[str, str] = {}
    for name, s in sources.items():
        if not isinstance(s, dict) or not s.get("available"):
            continue
        m = mods.get(name)
        grp = getattr(m, "DEDUPE_GROUP", None) if m else None
        if grp is None:
            continue
        cur = group_best.get(grp)
        if cur is None or prio(name) < prio(cur):
            group_best[grp] = name

    totals = {"input": 0, "output": 0, "cached": 0, "total": 0}
    daily: dict = {}
    models: dict = {}
    per_source_totals: dict = {}
    skipped_overlap: list[str] = []
    for name, s in sources.items():
        if not isinstance(s, dict) or not s.get("available"):
            continue
        m = mods.get(name)
        grp = getattr(m, "DEDUPE_GROUP", None) if m else None
        if grp is not None and group_best.get(grp) != name:
            skipped_overlap.append(name)
            continue
        st = s.get("totals") or {}
        per_source_totals[name] = int(st.get("total") or 0)
        for day, d in (s.get("daily") or {}).items():
            dd = daily.setdefault(day, {"input": 0, "output": 0, "cached": 0, "total": 0})
            for k in dd:
                dd[k] += int(d.get(k) or 0)
        for mk, mv in (s.get("models") or {}).items():
            mm = models.setdefault(mk, {"input": 0, "output": 0, "cached": 0, "total": 0})
            for k in mm:
                mm[k] += int(mv.get(k) or 0)
        for k in totals:
            totals[k] += int(st.get(k) or 0)
    denom = totals["total"] or 1
    shares = {k: round(v / denom * 100, 1) for k, v in per_source_totals.items()}
    return {"totals": totals, "daily": daily, "models": models,
            "per_source_totals": per_source_totals, "source_share_pct": shares,
            "skipped_overlap": skipped_overlap}
