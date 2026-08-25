#!/usr/bin/env python3
"""smoke_local.py — run the usage-dashboard backend against this machine's data.

Standalone diagnostic: imports dashboard/sources.py, aggregates Hermes' own
token usage by provider for the last N days, and prints a compact aligned
table with cache hit rates. Exits 0 on success.

Usage (from repo root):
    python tools/smoke_local.py [--days 30]
"""

import argparse
import os
import sys

# Make dashboard/ importable regardless of the caller's cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "dashboard"))

import sources  # noqa: E402  (dashboard/sources.py, path set up above)


def fmt_int(n) -> str:
    """1234567 -> '1,234,567'; None/blank -> '-'."""
    try:
        return f"{int(n or 0):,}"
    except (TypeError, ValueError):
        return "-"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=30, help="lookback window in days (default 30)")
    args = ap.parse_args()

    res = sources.collect(days=args.days)
    if not res.get("available"):
        print(f"UNAVAILABLE: {res.get('error')}", file=sys.stderr)
        return 1

    t = res["totals"]
    hit = t["hit_rate_pct"]
    print(f"Hermes token usage, last {args.days}d (generated {res['generated_at']})")
    print(f"  TOTAL   {fmt_int(t['total']):>15} tokens | in {fmt_int(t['input']):>13}"
          f" | out {fmt_int(t['output']):>11} | cached {fmt_int(t['cached']):>14}"
          f" | hit {'—' if hit is None else f'{hit}%'}")
    print()
    print(f"  {'provider':<20}{'total':>15}{'in':>15}{'out':>12}{'cached':>16}{'calls':>9}{'hit rate':>10}")
    for name, p in sorted(res["providers"].items(), key=lambda kv: -kv[1]["total"]):
        ph = p["hit_rate_pct"]
        print(f"  {name:<20}{fmt_int(p['total']):>15}{fmt_int(p['input']):>15}{fmt_int(p['output']):>12}"
              f"{fmt_int(p['cached']):>16}{fmt_int(p['api_calls']):>9}{'—' if ph is None else f'{ph}%':>10}")
    print(f"\n  day buckets: {len(res['daily'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
