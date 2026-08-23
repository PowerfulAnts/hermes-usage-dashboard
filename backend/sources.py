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
    return found


REGISTRY_ERRORS: list[dict] = []


# ---------------------------------------------------------------- cache ----

_CACHE_TTL_S = 300.0
_cache: dict[tuple, tuple[float, dict]] = {}


def cached_scan(name: str, days: int, fn, *args):
    """Memoize heavy scans for 5 min (files are append-only → safe window)."""
    key = (name, days)
    hit = _cache.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL_S:
        return hit[1]
    result = fn(days, *args) if args else fn(days)
    _cache[key] = (time.time(), result)
    return result


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


def collect_all(days: int = 30, only: list[str] | None = None) -> dict:
    mods = discover_adapters()
    wanted = set(only) if only else set(mods.keys())
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "sources": {}}
    for name, mod in sorted(mods.items(), key=lambda kv: (getattr(kv[1], "ORDER", 100), kv[0])):
        if name not in wanted:
            continue
        try:
            res = cached_scan(name, days, mod.scan)
        except Exception as exc:  # belt & braces: adapters shouldn't raise
            res = {"available": False, "days": days,
                   "totals": {}, "daily": {}, "models": {}, "meta": {},
                   "error": repr(exc)[:200]}
        res.setdefault("days", days)
        res.setdefault("meta", {})
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
