# Test fixtures & the USAGE_DASH_HOME mechanism

Agent-to-agent notes (written by coding agents for future coding agents —
not human instructions), so new adapter tests follow the same conventions.

## How test isolation works

`backend/adapters/_util.py::home()` returns `$USAGE_DASH_HOME` when the env
var is set, else the real user home. Every adapter resolves its data paths
through `home()` **at scan time** (never at import time). Tests redirect the
whole synthetic tree with one env var — nothing ever reads real machine data.

`tests/conftest.py` provides:

- `isolated_home` (autouse) — sets `USAGE_DASH_HOME` to a fresh `tmp_path`
  for EVERY test and clears `sources._cache` + `sources.REGISTRY_ERRORS`
  before and after. You get this for free; do not set the env var yourself
  unless you need a second root inside one test (use `monkeypatch.setenv`).
- `make_home` — factory rooted at the test's tmp_path:
  `make_home()` → the USAGE_DASH_HOME dir itself,
  `make_home('.codex', 'sessions')` → nested dir, created on demand.

## Rules for adapter tests

1. Build the synthetic home INSIDE the fixture, then `monkeypatch.setenv`
   (or rely on the autouse fixture's tmp_path) and clear `sources._cache`
   so no cached scan from a previous test leaks in.
2. Timestamps must be RELATIVE TO NOW (`time.time() - days_ago*86400`,
   ISO strings via `strftime('%Y-%m-%dT%H:%M:%SZ')`). Never hardcode dates —
   a fixed 2026 date silently falls outside the scan window later.
3. Assert EXACT bucket math (totals/daily/models with input/output/cached/
   total, `total == input + output`) — that is where delta-vs-cumulative
   regressions and cache-folding mistakes show up.
4. Cover the standard matrix per adapter:
   - exact bucket math incl. cache folding (each adapter documents where
     cache tokens ride — read its docstring first),
   - window filtering (one in-window + one out-of-window event),
   - model attribution (`<adapter>/<model>` key convention),
   - malformed lines/rows tolerated (never raise),
   - missing dir/file → `available:false` with a non-empty `error`.
5. Never assert real-machine numbers; `tools/smoke_local.py` is for that.

## Layout conventions

```
tests/
├── conftest.py                     # autouse isolation + make_home factory
├── test_<provider>_adapter.py      # one file per provider (or a small
│                                   #   family sharing one format, e.g. cline+roo)
├── test_registry.py                # discovery: broken files, duplicates, ORDER
├── test_contract_all_adapters.py   # shape invariants over EVERY discovered adapter
├── test_combined_dedupe.py         # DEDUPE_GROUP / COMBINED_PRIORITY semantics
└── test_api_shape.py               # /summary contract incl. embedded meta
```

Synthetic fixture trees are built in-code inside fixtures (see
`test_crush_amp_copilot_adapters.py` for JSON/SQLite examples) rather than
checked-in binary blobs — relative timestamps can't be stored in files.

## Running

```bash
python -m pytest tests/ -q          # whole suite (must stay green)
python -m pytest tests/test_<provider>_adapter.py -q   # one provider
python tools/smoke_local.py         # real-machine diagnostic, not a test
```
