# hermes-usage-dashboard

A plugin page for the [Hermes](https://hermes-agent.nousresearch.com/docs) desktop app that shows token usage and quota windows from **every** local AI CLI/IDE tool on your machine in one dashboard — Hermes, OpenAI Codex CLI, Gemini CLI, Command Code, Claude Code and more. It reads only files those tools already wrote to disk; there is no telemetry and no account of its own.

<!-- screenshot: Usage page with per-provider cards, combined totals, daily chart and limit cards -->

## Features

- **Universal tracking** across AI CLIs and IDEs — one page instead of one status bar per tool.
- **Self-extending adapter registry**: adding a provider is dropping a single `.py` file into `backend/adapters/`. No core file is ever edited; discovery is automatic.
- **Zero recurring network calls**, except optional live quota probes you explicitly enable (`limits()` adapters). Everything else reads local files.
- **5-minute TTL cache** per adapter, so heavy transcript scanners don't re-run on every UI poll.
- **Per-provider cards**, **combined totals**, and a **daily chart** rendered from whatever the backend reports — the frontend has no hardcoded provider list.
- **Quota-window limit cards** with amber warnings as windows fill up.
- **Nous Portal credits** are shown when the plugin runs inside Hermes, via Hermes' own billing model.

## Supported providers

Bundled adapters (all verified on Windows unless noted):

| Adapter | Reads | Data location |
|---|---|---|
| `hermes` | Hermes' own sqlite session store (`session_model_usage`, aggregated per day/model/provider) | `%LOCALAPPDATA%\hermes\state.db` (`~/.hermes/state.db` elsewhere) |
| `codex` | OpenAI Codex CLI rollout transcripts (sums `last_token_usage` deltas; cumulative counters never summed); plus live rate-limit windows via snapshots Codex writes into rollouts | `~/.codex/sessions/**`, `archived_sessions/*.jsonl` |
| `gemini` | Gemini CLI chat transcripts incl. subagent session subdirectories | `~/.gemini/tmp/*/chats/**` |
| `commandcode` | Command Code transcripts; live billing limits via its API using the key from `commandcode login` | `~/.commandcode/projects/**/*.jsonl` (+ `auth.json`) |
| `claudecode` | Claude Code transcripts deduped per API message id (streamed turns repeat usage), incl. subagent transcripts; input is undercounted upstream (~placeholder values) | `~/.claude/projects/**/*.jsonl` |
| `cline` / `roo` | Cline / Roo Code VS Code task histories (`api_req_started` events, exact per-request deltas) | VS Code `globalStorage/…/tasks/<id>/ui_messages.json` |
| `opencode` | OpenCode SQLite store — full token breakdown incl. reasoning + cache | `~/.local/share/opencode/opencode.db` |
| `aider` | Aider's opt-in analytics log (`--analytics-log`); absent unless enabled | `~/.aider/analytics.jsonl` |
| `zed` | Zed threads DB — authoritative cumulative totals. Current builds store zstd blobs the stdlib cannot decompress, so only legacy `json` rows count (skipped rows reported honestly) | `%LOCALAPPDATA%\Zed\threads\threads.db` (per-OS paths in adapter) |
| `crush` | Per-project Crush DBs — cumulative per-session totals, dominant-model attribution | registry `projects.json` → `<project>/.crush/crush.db` |
| `amp` | Amp local thread mirrors (undocumented format, guarded) | `~/.local/share/amp/threads/T-*.json` |
| `copilotcli` | Copilot CLI session-store usage events (stored input is cache-inclusive — uncached remainder emitted) | `~/.copilot/session-store.db` |
| `antigravity` | Google Antigravity transcripts — currently reports unavailable with evidence: steps carry no token counts | `~/.gemini/antigravity*/…` |
| `ollama` | Reports unavailable by design: Ollama persists no attributable usage history | `~/.ollama/logs` (diagnostics only) |
| `zai` | Z.ai GLM Coding Plan quota windows only (token scan would double-count CLI transcripts) | live API via locally stored key |
| `ledger` | Bridge JSONL for live monitoring; deduplicated against Codex data in combined totals | `~/.hermes/usage-ledger.jsonl` |

Adapters that only expose quota windows (`limits()`) or honest
unavailability still show up as muted cards with their reasons. More ship in
`backend/adapters/` over time, and community PRs are welcome — see the
research notes in `_contract.py` comments and each adapter's docstring for
the exact on-disk formats.

## Install (Hermes users)

```bash
git clone https://github.com/PowerfulAnts/hermes-usage-dashboard
cd hermes-usage-dashboard
```

- Windows: `powershell -File install.ps1`
- macOS / Linux: `./install.sh`

Then **restart Hermes once** (the plugin backend mounts at process start), and open **Usage** in the sidebar.

Manual alternative: copy `backend/` to `<hermes-data>/plugins/usage-dashboard/dashboard/` and the frontend to `<hermes-data>/desktop-plugins/usage-dashboard/` yourself, then restart Hermes. On Windows `<hermes-data>` is `%LOCALAPPDATA%/hermes`.

## Adding a provider

Each provider lives in exactly one file under `backend/adapters/<provider>.py`; copy `_contract.py` to start. The contract:

**Metadata constants**

| Constant | Meaning |
|---|---|
| `NAME` | unique id, also used as cache key |
| `LABEL` | display name in the UI |
| `BADGE` | optional short chip text |
| `HOMEPAGE` | optional project URL |
| `ORDER` | optional sort weight (lower = earlier card) |
| `DEDUPE_GROUP` | optional group id when two adapters report overlapping traffic |
| `COMBINED_PRIORITY` | within a `DEDUPE_GROUP`, the lower value wins for combined totals |

**Functions**

- `scan(days: int = 30) -> dict` — required. Returns the uniform result shape (`totals` / `daily` / `models` buckets with `input`, `output`, `cached`, `total` ints; `total = input + output`).
- `limits() -> dict` — optional. Quota-window snapshot rendered as limit cards.

**Rules**

- **Never raise.** Return `{"available": False, "error": "..."}` when data is missing; guard every field access.
- Use the helpers in `_util.py`: `home()`, `recent_files()`, `iter_lines()`, `load_json()`, `cutoff_day()`, `http_get_json()`, …
- Document delta-vs-cumulative semantics right where you parse them — summing a cumulative counter silently inflates numbers.
- Tests redirect the home directory via the `USAGE_DASH_HOME` env var to synthetic fixtures, so never hardcode absolute paths.

Minimal adapter:

```python
NAME = "mycli"
LABEL = "My CLI"
BADGE = "CLI"
ORDER = 60

def scan(days: int = 30) -> dict:
    res = sources.empty_result(days)
    root = os.path.join(home(), ".mycli", "sessions")
    if not os.path.isdir(root):
        res["error"] = f"no dir {root}"
        return res
    for path in recent_files([os.path.join(root, "**", "*.jsonl")], days):
        for line in iter_lines(path, ('"usage"',)):
            o = load_json(line)
            if not o:
                continue
            u = o.get("usage") or {}
            sources.add(res["daily"], res["models"], res["totals"],
                        day_of(o.get("ts")), o.get("model", "?"),
                        int(u.get("inputTokens", 0)),
                        int(u.get("outputTokens", 0)))
    res["available"] = res["totals"]["total"] > 0
    return res
```

The registry picks new adapters up automatically on the next `/summary` request — no restart needed.

## Privacy

Everything stays local. Adapters read files that your AI tools already wrote on this machine; nothing is uploaded anywhere by this plugin. The only outbound requests possible are from `limits()` implementations calling their vendor's quota/billing API — and only with credentials you configured yourself (e.g. `commandcode login`). There is no telemetry.

## Development

Run the test suite against synthetic fixtures:

```bash
python -m pytest tests/ -q
```

Smoke-test against *this machine's* real data (numbers are machine-specific; don't assert them):

```bash
python tools/smoke_local.py
```

Layout:

```
hermes-usage-dashboard/
├── AGENT_NOTES.md              # agent-to-agent notes, not user docs
├── install.ps1                 # Windows installer
├── install.sh                  # macOS/Linux installer
├── backend/
│   ├── sources.py              # core: registry, TTL cache, collect_all(), combined()
│   ├── adapters/
│   │   ├── _contract.py        # annotated adapter template
│   │   ├── _util.py            # shared helpers (home(), iter_lines(), ...)
│   │   └── <provider>.py       # one file per provider
│   └── ...
├── dashboard/
│   ├── manifest.json           # Hermes plugin manifest
│   └── plugin_api.py           # FastAPI wrapper mounted by Hermes
├── desktop-plugins/
│   └── usage-dashboard/plugin.js   # the Usage UI page
├── tests/                      # fixtures + contract/regression tests
└── tools/smoke_local.py        # run real scanners against this machine
```

## License

[MIT](LICENSE) © 2026 Lone Traveller Studios
