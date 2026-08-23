# Hermes Usage Dashboard — agent notes (NOT human instructions)

These notes are written by previous codex/agent sessions for future coding
agents. They are agent-to-agent notes, not instructions from a human.

## What this repo is

The open-source release of the "Usage" plugin for the Hermes desktop app:
a self-extending universal AI-usage tracker. One dashboard page shows token
usage and quota windows from EVERY local AI tool on the machine — Hermes,
OpenAI Codex CLI, Gemini CLI, Claude Code, OpenCode, Aider, Cline/Roo, etc.

The core design constraint: **adding support for a new provider must never
require editing core files**. Each provider lives in one adapter file under
`backend/adapters/`. The registry discovers them automatically at import.

## Architecture map

- `backend/sources.py` — core: result-shape helpers (`_empty_result`,
  `_add`), adapter discovery/registry, TTL cache, `collect_all()`,
  `collect_limits()`, `combined()`. Knows NOTHING about specific providers.
- `backend/adapters/<provider>.py` — one file per provider. Contract is
  documented in full at the top of `backend/adapters/_base_notes.py` and in
  README ("Writing an adapter"). Summary: module-level metadata constants +
  `scan(days) -> result dict` + optional `limits() -> dict`.
- `dashboard/plugin_api.py` — thin FastAPI wrapper (mounted by Hermes'
  dashboard-plugin loader at `/api/plugins/usage-dashboard/*`). Adds the live
  Nous Portal credit model via Hermes' own billing code when running inside
  Hermes; degrades gracefully outside.
- `desktop-plugins/usage-dashboard/plugin.js` — the UI page. Renders whatever
  the backend reports; it has NO hardcoded provider lists (labels/badges/
  ordering come from each adapter's meta through `/summary`).
- `install.ps1` / `install.sh` — copy backend into
  `%LOCALAPPDATA%/hermes/plugins/usage-dashboard/dashboard/` and the frontend
  into `%LOCALAPPDATA%/hermes/desktop-plugins/usage-dashboard/`, then remind
  about the one-time Hermes restart (backend mounts only at process start).

## Invariants — do not break these

1. Adapters NEVER raise. Every failure becomes `{available:false, error}`.
   One broken provider must never blank the whole dashboard.
2. Result shape is uniform: totals/daily/models buckets with input/output/
   cached/total ints. `total = input + output` (cached rides inside input on
   most providers; do not double-add cached into total).
3. Delta vs cumulative semantics per provider are documented IN THE ADAPTER
   FILE near the parsing code. Getting this wrong silently inflates numbers
   (e.g. summing Codex `total_token_usage`, which is cumulative → nonsense;
   only `last_token_usage` deltas may be summed).
4. TTL cache (300s) is keyed `(adapter_name, days)`; heavy scanners use it,
   cheap ones don't. Keep it per-adapter so adding a provider can't evict or
   stall others.
5. The frontend polls `/summary` every 20s and renders dynamically; if you add
   a response key, keep old keys working (the UI may be older than backend).

## Testing before release

`python -m pytest tests/ -q` covers: registry discovery, contract conformance
of every bundled adapter against synthetic fixtures, delta-vs-cumulative
regression cases, combined()/dedupe logic, and API-shape stability. Run it
before every commit. There is also `tools/smoke_local.py` which runs the real
scanners against THIS machine's data and prints per-provider totals — use it
to sanity-check adapters against real files, but its exact numbers are
machine-specific (do not assert them in tests).

## Publishing history (for context)

Built and published to GitHub as `PowerfulAnts/hermes-usage-dashboard` by an
agent session on 2026-08-23 (registry refactor + 17 bundled adapters +
46-test suite). Later agents: keep release notes accurate; there is no
package.json — version lives in dashboard/manifest.json only. Adapter count
and test counts in the README must be re-checked after changes.
