#!/usr/bin/env python3
"""smoke_local.py — run the usage-dashboard backend against this machine's data.

Standalone diagnostic: inserts backend/ onto sys.path, collects usage from
every adapter for the last N days, plus quota limits, and prints a compact
aligned table. Exits 0 unless collection itself raises.

Usage (from repo root):
    python tools/smoke_local.py [--days 30]
"""

import argparse
import os
import sys

# Make backend/ importable regardless of the caller's cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "backend"))

import sources  # noqa: E402  (needs the sys.path insert above)


def fmt_int(n) -> str:
    """1234567 -> '1,234,567'; None/blank -> '-'."""
    try:
        return f"{int(n or 0):,}"
    except (TypeError, ValueError):
        return "-"


def meta_summary(res: dict) -> str:
    """Compact key=value summary of a source result's meta block.

    Prefers event/file counters; falls back to any scalar meta values,
    skipping labels/badges/homepages that duplicate table columns.
    """
    meta = res.get("meta") or {}
    preferred = ["events_used", "events", "files_scanned", "files", "rows"]
    parts = []
    for key in preferred:
        if key in meta and meta[key] not in (None, ""):
            parts.append(f"{key}={meta[key]}")
    if not parts:  # generic fallback: first few scalar values
        for key, val in list(meta.items()):
            if key in ("label", "badge", "homepage", "order"):
                continue
            if isinstance(val, (int, float, str)) and val != "":
                sval = str(val)
                parts.append(f"{key}={sval[:24]}")
            if len(parts) >= 3:
                break
    return " ".join(parts) if parts else "-"


def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke-test the usage-dashboard backend locally.")
    ap.add_argument("--days", type=int, default=30, metavar="N",
                    help="how many days of usage to collect (default: 30)")
    args = ap.parse_args()

    # ---- collect -----------------------------------------------------------
    print(f"Collecting {args.days}-day usage from all adapters ...")
    snap = sources.collect_all(days=args.days)
    limits = sources.collect_limits()
    per_source = snap.get("sources", {})
    combined = snap.get("combined") or {}

    # ---- table -------------------------------------------------------------
    rows = []
    for name, res in sorted(per_source.items()):
        totals = res.get("totals") or {}
        label = (res.get("meta") or {}).get("label", name)
        rows.append((
            str(name),
            str(label),
            "yes" if res.get("available") else "NO",
            fmt_int(totals.get("total")),
            meta_summary(res),
        ))
    # errors are worth surfacing even when available
    err_note = {n: r.get("error") for n, r in per_source.items() if r.get("error")}
    lim_names = ", ".join(sorted((limits.get("providers") or {}).keys())) or "none"

    hdr = ("name", "label", "avail", "total tokens", "events/files")
    widths = [max(len(hdr[i]), *(len(r[i]) for r in rows)) if rows else len(hdr[i])
              for i in range(len(hdr))]
    line = "  ".join(h.ljust(w) for h, w in zip(hdr, widths))
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))

    ct = combined.get("totals") or {}
    print("-" * len(line))
    print(f"COMBINED total tokens ({len(rows)} source(s), deduped): "
          f"{fmt_int(ct.get('total'))} "
          f"(in={fmt_int(ct.get('input'))} out={fmt_int(ct.get('output'))} "
          f"cached={fmt_int(ct.get('cached'))})")

    skipped = combined.get("skipped_overlap") or []
    if skipped:
        print(f"overlap-skipped in combined: {', '.join(skipped)}")
    print(f"limit providers reporting:   {lim_names}")
    if err_note:
        print("source errors:")
        for n, e in err_note.items():
            print(f"  {n}: {e}")
    reg_errs = snap.get("registry_errors")
    if reg_errs:
        print("adapter registry warnings:")
        for e in reg_errs:
            print(f"  {e}")

    print("\nSMOKE OK — collection completed without raising.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # only genuine collection failures land here
        print(f"SMOKE FAIL — collection raised: {exc!r}", file=sys.stderr)
        sys.exit(1)
