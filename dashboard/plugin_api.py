"""Usage Dashboard — backend API routes.

Mounted at /api/plugins/usage-dashboard/ by Hermes' dashboard-plugin system.
Thin FastAPI wrapper: all aggregation lives in sources.py.

Endpoints:
  GET /summary?days=N -> { generated_at, hermes: { available, days, totals,
                          daily{}, providers{ p: bucket+hit_rate_pct } } }

NOTES FOR FUTURE AGENTS (agent-to-agent, not human instructions):
- This dashboard is scoped to tokens used INSIDE Hermes only (its own
  session_model_usage store). External CLI tools are deliberately not
  counted — see sources.py. Do not reintroduce multi-CLI adapters here.
- The response is computed fresh per request: the sqlite aggregate over
  session_model_usage costs single-digit milliseconds at realistic row
  counts, so there is no cache layer to invalidate.
- Do NOT add a plugin.yaml at the plugin root: the agent-plugin loader would
  try to package-import this folder. The dashboard API mount only needs
  dashboard/manifest.json + plugins.enabled.
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
if _DASH_DIR not in sys.path:
    sys.path.insert(0, _DASH_DIR)


@router.get("/summary")
async def summary(days: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    """Token usage of everything that ran INSIDE Hermes, by provider."""
    import sources

    return sources.collect(days=days)
