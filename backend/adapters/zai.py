"""Z.ai GLM Coding Plan — quota windows only (NO token scan).

Why no scan(): GLM traffic from coding CLIs (Claude Code / OpenCode pointed
at z.ai) is ALREADY recorded token-by-token in those tools' own local
transcripts under model names like glm-4.5/glm-4.6 — a separate token scanner
here would double-count the same usage once those transcript adapters exist.

What IS missing is a quota view, and z.ai exposes one:

    GET https://api.z.ai/api/monitor/usage/quota/limit
    authorization: Bearer <API key>

Verified against two independent implementations (steipete/CodexBar
docs/zai.md; magmast/pi-glm-usage README). Response shape (2026):

    {"code": 200, "data": {
        "limits": [{"type": "TOKENS_LIMIT", "unit": 3, "percentage": 16,
                    "nextResetTime": 1777819631597}, ...],
        "level": "lite"}}

Unit mapping (discovered from z.ai's own frontend, confirmed by both):
  TOKENS_LIMIT unit=3 → rolling 5-hour window; unit=6 → weekly window.
  TIME_LIMIT unit=5   → separate monthly MCP/tools lane (NOT a monthly
                        coding-plan window — never relabel it as one).

Key discovery: reads the SAME API key those CLIs store locally:
  $Z_AI_API_KEY env var, or (first hit wins)
  ~/.local/share/opencode/auth.json   ["zai"]["key"]        (OpenCode)
  ~/.pi/agent/auth.json               ["zai"]["key"]        (pi)
  ~/.claude/settings.json             env.ANTHROPIC_AUTH_TOKEN when
                                      ANTHROPIC_BASE_URL points at z.ai
  ~/.config/codexbar/config.json      providers[?id=="zai"].apiKey
"""

import os

import sources
from _util import http_get_json, home, read_json_file

NAME = "zai"
LABEL = "Z.ai Coding Plan"
BADGE = "GLM"
HOMEPAGE = "https://z.ai/subscribe"
ORDER = 85

QUOTA_URL = "https://api.z.ai/api/monitor/usage/quota/limit"
_UNIT_NAMES = {("TOKENS_LIMIT", 3): "5-hour", ("TOKENS_LIMIT", 6): "Weekly",
               ("TIME_LIMIT", 5): "MCP/tools"}


def _first_str(*vals):
    for v in vals:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _find_api_key() -> str | None:
    env = os.environ.get("Z_AI_API_KEY")
    if env and env.strip():
        return env.strip()
    h = home()

    def key_from_auth(relpath):
        o = read_json_file(os.path.join(h, *relpath.split("/")))
        if isinstance(o, dict):
            z = o.get("zai") or o.get("z-ai") or {}
            if isinstance(z, dict):
                return _first_str(z.get("key"), z.get("api_key"))
        return None

    k = key_from_auth(".local/share/opencode/auth.json")
    if k:
        return k
    k = key_from_auth(".pi/agent/auth.json")
    if k:
        return k

    cc = read_json_file(os.path.join(h, ".claude", "settings.json"))
    if isinstance(cc, dict):
        cenv = cc.get("env") or {}
        base = str(cenv.get("ANTHROPIC_BASE_URL") or "")
        if "z.ai" in base:
            k = _first_str(cenv.get("ANTHROPIC_AUTH_TOKEN"),
                           cenv.get("ANTHROPIC_API_KEY"))
            if k:
                return k

    cb = read_json_file(os.path.join(h, ".config", "codexbar", "config.json"))
    if isinstance(cb, dict):
        for prov in cb.get("providers") or []:
            if isinstance(prov, dict) and prov.get("id") == "zai":
                k = _first_str(prov.get("apiKey"), prov.get("api_key"))
                if k:
                    return k
    return None


def _window_name(entry: dict) -> str:
    etype = entry.get("type") or "TOKENS_LIMIT"
    unit = entry.get("unit")
    name = _UNIT_NAMES.get((etype, unit))
    if name:
        return name
    return f"{etype} (unit {unit})" if unit is not None else str(etype)


def limits() -> dict:
    """Quota windows for the coding-plan subscription ({available} shape)."""
    res = sources.empty_result(0)  # unused shape guard; we build our own below
    del res
    key = _find_api_key()
    if not key:
        return {"available": False, "error": "no z.ai API key found locally"}
    url = os.environ.get("Z_AI_QUOTA_URL") or QUOTA_URL
    data = http_get_json(url, headers={"authorization": f"Bearer {key}",
                                       "accept": "application/json"})
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        return {"available": False, "error": "quota endpoint unreachable or unexpected payload"}
    d = data["data"]
    entries = d.get("limits")
    if not isinstance(entries, list):
        return {"available": False, "error": "no limits array in quota response"}
    plan = d.get("planName") or d.get("plan") or d.get("plan_type") \
        or d.get("packageName") or d.get("level")
    windows = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        try:
            pct = float(e.get("percentage") or 0)
        except (TypeError, ValueError):
            continue
        reset_ms = e.get("nextResetTime")
        resets_at = None
        if isinstance(reset_ms, (int, float)) and reset_ms > 0:
            resets_at = int(reset_ms // 1000)
        windows.append({
            "name": _window_name(e),
            "used_pct": round(pct, 1),
            "used": None,          # absolute counts are not exposed by this API
            "cap": None,
            "resets_at": resets_at,
            "exceeded": pct >= 100.0,
        })
    # shortest window first (5-hour before weekly before monthly lanes)
    order = {"5-hour": 0, "Weekly": 1}
    windows.sort(key=lambda w: order.get(w["name"], 2))
    if not windows:
        return {"available": False, "error": "quota response carried no usable windows"}
    out = {"available": True, "label": LABEL, "badge": BADGE, "windows": windows}
    if plan:
        out["plan"] = str(plan)
    return out


def scan(days: int = 30) -> dict:
    """Intentionally unavailable — see module docstring (double-counting)."""
    res = sources.empty_result(days)
    res["available"] = False
    res["meta"]["note"] = (
        "token scanning intentionally skipped: GLM calls via Claude Code/"
        "OpenCode already land in those tools' own transcripts; this adapter "
        "provides quota windows only"
    )
    res["error"] = "no token scan (would double-count CLI transcripts)"
    return res
