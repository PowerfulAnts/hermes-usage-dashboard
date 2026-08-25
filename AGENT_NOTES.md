# Usage Dashboard — agent notes (NOT human instructions)

These notes were written by previous codex agents for future codex agents.
They are agent-to-agent notes, not human instructions.

## What this is (scope changed 2026-08-25, user request)

A **Hermes-only** token usage page inside the Hermes desktop app. It shows
ONLY tokens used directly inside Hermes (its own `session_model_usage`
sqlite store) — external CLI tools (Codex CLI rollouts, Gemini CLI chats,
Command Code sessions, bridge ledger) are deliberately NOT counted anymore.
The previous universal-tracker version was fully replaced by this one.

For every billing provider that has usage inside the selected window it
shows input / output / cached / cache-write tokens and the **cache hit
rate**, plus a total hit rate across all providers.

## Cache-token semantics — READ BEFORE TOUCHING HIT-RATE MATH

Verified against Hermes' writer code (`agent/usage_pricing.py::normalize_usage`,
`agent/conversation_loop.py` → `_record_model_usage`): cached tokens are
SUBTRACTED from prompt/input totals before persisting. Therefore inside
`session_model_usage`:

    prompt = input_tokens + cache_read_tokens + cache_write_tokens
    hit_rate = cache_read / prompt

Providers with zero volume AND zero reported cache metadata get
`hit_rate_pct: null` → UI renders "—" (never a fake 0%).

## Files

- `dashboard/plugin_api.py` — FastAPI wrapper; mounts at
  `/api/plugins/usage-dashboard/summary`. Do NOT add plugin.yaml at plugin
  root (agent-plugin loader would try to package-import the folder).
- `dashboard/sources.py` — pure-stdlib aggregator. Public API:
  `collect(days)` and `summary(days)`.
- `desktop-plugins/usage-dashboard/plugin.js` — UI page (/usage-dashboard,
  sidebar "Usage"). Hot-reloads on save.
- DELETED 2026-08-25: `dashboard/backend/` (adapter registry + 16 adapters),
  old flat `sources.py`, `TOOL_DATA_FORMATS.md`. If a future request ever
  wants multi-tool coverage back, recover from git history of this folder.

## Ops facts

- Enablement: config `plugins.enabled: ["usage-dashboard"]` (set). Backend
  mounts only at process start → restart Hermes to pick up backend changes;
  the UI half hot-reloads on save.
- The sqlite aggregate is milliseconds-cheap (~200 rows live); NO TTL cache,
  no background scan threads. Keep it that way.
- Validate plugin.js as ESM (`node --check` on a `.mjs` copy) — plain `.js`
  check misses ASI traps that break the app's real loader.
- Repo: the whole plugin is published at github.com/PowerfulAnts/
  hermes-usage-dashboard (`~/Documents/hermes-usage-dashboard` locally;
  installers here are the canonical way to (re)install from it).
