"""Usage Dashboard — backend API routes.

Mounted at /api/plugins/usage-dashboard/ by Hermes' dashboard-plugin system.
Thin FastAPI wrapper: all aggregation lives in sources.py + adapters/.

Endpoints:
  GET /summary?days=N -> { generated_at, sources:{name:{meta,totals,…}},
                           combined:{…}, limits:{providers:{…}},
                           portal:{…} }   (portal only when run inside Hermes)

NOTES FOR FUTURE AGENTS (agent-to-agent, not human instructions):
- The Portal credit model comes from Hermes' own billing code and only exists
  inside the app; everywhere else we degrade to {"available": false} so the
  OSS repo runs standalone (tests, other hosts).
- Do NOT add a plugin.yaml at the plugin root: the agent-plugin loader would
  try to package-import this folder. The dashboard API mount only needs
  dashboard/manifest.json + plugins.enabled.
- /summary embeds each adapter's display meta (label/badge/order/homepage)
  into sources[name].meta — the frontend renders dynamically from that and
  must not hardcode provider lists.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from fastapi import APIRouter, Query

log = logging.getLogger(__name__)

router = APIRouter()

_DASH_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.join(_DASH_DIR, "backend")
for _p in (_DASH_DIR, _BACKEND_DIR):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


def _portal_model() -> dict[str, Any]:
    """Live Portal credits via the host app's billing model. Fail-open."""
    try:
        from agent.billing_usage import build_usage_model
        from tui_gateway.server import _serialize_usage_model

        return _serialize_usage_model(build_usage_model())
    except Exception:
        log.debug("usage-dashboard ▸ portal fetch unavailable", exc_info=True)
        return {"ok": True, "available": False}


@router.get("/summary")
async def summary(days: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    """Universal usage: every discovered provider source, combined + limits."""
    import sources

    data = sources.collect_all(days=days)
    portal_model = _portal_model()
    data["portal"] = portal_model
    limits = sources.collect_limits(portal_model)
    # Nous Portal allowance rides alongside adapter-provided limits when the
    # host exposes it — same card shape, injected here rather than in an
    # adapter because it needs Hermes' own session, not files on disk.
    if portal_model.get("available"):
        bar = portal_model.get("plan_bar") or {}
        if bar.get("total_display") or bar.get("pct_used") is not None:
            limits["providers"]["nous"] = {
                "available": bool(bar.get("total_display")),
                "label": "Nous Portal",
                "badge": "allowance",
                "windows": [{
                    "name": "Monthly",
                    "used_pct": bar.get("pct_used"),
                    "spent_display": bar.get("spent_display"),
                    "total_display": bar.get("total_display"),
                    "remaining_display": bar.get("remaining_display"),
                    "resets_display": portal_model.get("renews_display"),
                }],
            }
    data["limits"] = limits
    return data


@router.get("/portal")
async def portal_only() -> dict[str, Any]:
    return _portal_model()
