# hermes-usage-dashboard

A plugin page for the [Hermes](https://hermes-agent.nousresearch.com/docs) desktop app that shows the **token usage of everything that ran inside Hermes itself** — input, output, cached and cache-write tokens per billing provider (Nous, OpenRouter, OpenAI Codex, Command Code, custom endpoints, …), each provider's **cache hit rate**, and a **total cache hit rate** across all of them. Daily and per-provider usage use visual bars, while independently trackable **usage limits, credits and funds** remain visible.

Token totals read only Hermes' own request log (`state.db` → `session_model_usage`, which Hermes writes anyway). There is no telemetry or separate account; optional account-status calls use the provider credentials already configured locally and are cached for five minutes.

> **Scope note (v3):** token totals are scoped to requests made directly inside Hermes. The dashboard may read provider account-status metadata (Codex windows, Command Code credits, OpenRouter key limits, Nous Portal funds), but those sources never contribute tokens.

<!-- screenshot: Usage page with totals, daily bars, provider cards, limits and funds -->

## Features

- **In / Out / Cached / Cache-write tokens per provider** for any window (7 / 30 / 90 days).
- **Cost per provider and in total**, using the spend Hermes itself prices into `session_model_usage` (provider-reported when available, token-price estimates otherwise). Works for every provider automatically — including ones this plugin has never heard of, because grouping is by whatever `billing_provider` Hermes recorded.
- **Cache hit rate per provider** = `cached ÷ (in + cached + cache-written)`.
- **Total cache hit rate** across all providers in the same window.
- **Daily activity bars** and rich per-provider cards with usage-share, input/output mix and cache-hit bars.
- **Usage limits, credits and funds** for OpenAI Codex, Command Code Go, OpenRouter and Nous Portal when those providers expose them.
- Every provider with prompt tokens shows a numeric cache hit rate; no hits is `0.0%` with an empty bar.
- Token totals are fresh on every open: the sqlite aggregate is milliseconds-cheap, so there is no token cache layer or background scanning.
- Network-backed account status is cached for five minutes; the 20-second token refresh does not repeatedly hit provider APIs.
- Renders whatever providers actually have usage in the window; nothing is hardcoded in the UI.

## How cache math works

Hermes normalizes API usage before persisting it: cached tokens are subtracted from prompt/input totals, so inside `session_model_usage`

```
prompt tokens = input_tokens + cache_read_tokens + cache_write_tokens
cache hit rate = cache_read_tokens ÷ prompt tokens
```

The backend computes hit rates; the UI only renders them (`hit_rate_pct: null` → `—`).

## Install (Hermes users)

```bash
git clone https://github.com/PowerfulAnts/hermes-usage-dashboard
cd hermes-usage-dashboard
```

- Windows: `powershell -File install.ps1`
- macOS / Linux: `./install.sh`

Then **restart Hermes once** (the plugin backend mounts at process start), and open **Usage** in the sidebar.

Manual alternative: copy `dashboard/plugin_api.py`, `dashboard/manifest.json` and `dashboard/sources.py` to `<hermes-data>/plugins/usage-dashboard/dashboard/` and the frontend to `<hermes-data>/desktop-plugins/usage-dashboard/` yourself, then restart Hermes. On Windows `<hermes-data>` is `%LOCALAPPDATA%/hermes`; elsewhere `~/.local/share/hermes` or `~/Library/Application Support/hermes`.

Upgrading from v2.x? The installer removes the old `backend/` adapter tree automatically; just run it again and restart Hermes.

## Uninstall

- Windows: `powershell -File uninstall.ps1`
- macOS / Linux: `./uninstall.sh`

## Repository layout

| Path | What it is |
|---|---|
| `dashboard/plugin_api.py` | FastAPI router mounted at `/api/plugins/usage-dashboard/*` |
| `dashboard/sources.py` | Pure-stdlib aggregation over `session_model_usage` (single source of all numbers) |
| `dashboard/manifest.json` | Dashboard-plugin manifest consumed by Hermes |
| `desktop-plugins/usage-dashboard/plugin.js` | The UI page (sidebar "Usage", route `/usage-dashboard`) |
| `install.ps1` / `install.sh` | Installers (auto-detect the Hermes dir; `-DryRun` supported) |
| `uninstall.ps1` / `uninstall.sh` | Safe uninstalls (only remove folders whose manifest says `usage-dashboard`) |
| `tools/smoke_local.py` | Print your own usage table from the terminal |
| `tools/verify_install.ps1` | Post-install verification |

## API contract

`GET /api/plugins/usage-dashboard/summary?days=N` (N: 1–365) →

```jsonc
{
  "available": true,
  "days": 30,
  "generated_at": "2026-08-25T06:16:59+00:00",
  "totals": {                       // same bucket shape as every provider
    "input": 88644315,              // excludes cached tokens
    "output": 2998132,              // includes reasoning tokens
    "cached": 840256960,            // cache READ hits
    "cache_write": 0,
    "total": 91642447,              // input + output (+ reasoning already inside output)
    "api_calls": 6223,
    "hit_rate_pct": 90.5            // TOTAL cache hit rate; null = unknown
  },
  "daily":   { "2026-08-25": { "...same bucket...": {} } },
  "providers": {
    "nous":        { "...bucket...", "hit_rate_pct": 95.3 },
    "openrouter":  { "...bucket...", "hit_rate_pct": 96.8 },
    "command-code-go": { "...bucket...", "hit_rate_pct": null }   // never reports cache → "—"
  }
}
```

## Development notes

- The backend is deliberately one pure-stdlib module with no cache layer; the grouped SUM over `session_model_usage` costs single-digit milliseconds at realistic sizes.
- `AGENT_NOTES.md` carries agent-to-agent engineering notes (cache-token semantics, ops facts); it is not user documentation.
- Windows quirk: a running Hermes backend can hold the sqlite write-lock — `sources.py` opens read-only first and falls back to copying `state.db` (+WAL) to a temp file.

## License

MIT — see [LICENSE](LICENSE).
