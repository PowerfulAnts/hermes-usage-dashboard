"""Usage Dashboard — backend API routes.

Mounted at /api/plugins/usage-dashboard/ by Hermes' dashboard-plugin system.

GET /summary?days=N returns:
  - Hermes-only token totals (in/out/cached/cache-write/hit rates)
  - provider limits / credits / funds (independent account-status sources)
  - the full Nous Portal credit model for its dedicated funds view

NOTES FOR FUTURE AGENTS (agent-to-agent, not human instructions):
- Token scope is deliberately Hermes-only. Do not add external transcript token
  scanners. Limits/credits/funds are independent and must remain visible.
- Token SQL is fresh each request; network-backed account status is cached 5m.
- Do NOT add plugin.yaml; this API mounts through dashboard/manifest.json.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Any

from fastapi import APIRouter, Query

log = logging.getLogger(__name__)
router = APIRouter()

_DASH_DIR = os.path.dirname(os.path.abspath(__file__))
if _DASH_DIR not in sys.path:
    sys.path.insert(0, _DASH_DIR)

_PORTAL_TTL_SECONDS = 300.0
_portal_cache: tuple[float, dict[str, Any]] | None = None


def _portal_model() -> dict[str, Any]:
    """Hermes' live Nous Portal credit model, fail-open and cached for 5m."""
    global _portal_cache
    if _portal_cache and time.time() - _portal_cache[0] < _PORTAL_TTL_SECONDS:
        return _portal_cache[1]
    try:
        from agent.billing_usage import build_usage_model
        from tui_gateway.server import _serialize_usage_model

        result = _serialize_usage_model(build_usage_model())
    except Exception:
        log.debug("usage-dashboard ▸ portal fetch unavailable", exc_info=True)
        result = {"ok": True, "available": False}
    _portal_cache = (time.time(), result)
    return result


def _nous_limit(portal: dict[str, Any]) -> dict[str, Any]:
    bar = portal.get("plan_bar") or {}
    windows = []
    if bar.get("total_display") or bar.get("pct_used") is not None:
        windows.append({
            "name": "Monthly",
            "used_pct": bar.get("pct_used"),
            "spent_display": bar.get("spent_display"),
            "total_display": bar.get("total_display"),
            "remaining_display": bar.get("remaining_display"),
            "resets_display": portal.get("renews_display"),
        })
    return {
        "available": bool(portal.get("available")),
        "label": "Nous Portal",
        "badge": portal.get("plan_name") or "allowance",
        "windows": windows,
        "status": portal.get("status"),
        "total_spendable_display": portal.get("total_spendable_display"),
        "renews_display": portal.get("renews_display"),
    }


@router.get("/summary")
async def summary(days: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    """Hermes-only token usage plus independently tracked account status."""
    import sources

    # These sources are independent. Run them concurrently off the FastAPI
    # event loop so provider outages do not stack their timeout budgets.
    data, limits, portal = await asyncio.gather(
        asyncio.to_thread(sources.collect, days),
        asyncio.to_thread(sources.collect_limits),
        asyncio.to_thread(_portal_model),
    )
    if portal.get("available"):
        limits["providers"]["nous"] = _nous_limit(portal)
    data["limits"] = limits
    data["portal"] = portal
    return data


@router.get("/portal")
async def portal_only() -> dict[str, Any]:
    return await asyncio.to_thread(_portal_model)
