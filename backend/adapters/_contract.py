"""ADAPTER CONTRACT — annotated template (copy me when adding a provider).

This file is documentation-as-code: it is skipped by discovery (leading
underscore). To support a new AI tool, copy this template to
``adapters/<provider>.py``, fill in the metadata, implement ``scan()``
(and ``limits()`` if the tool exposes quota windows), and restart nothing —
the registry picks it up on the next /summary request.

Rules:
- NEVER raise. Return {"available": False, "error": "..."} when data is
  missing; guard every JSON field access.
- Keep the result shape exactly as below; totals/daily/models buckets use
  add() from sources.py.
- Document delta-vs-cumulative semantics right where you parse them.
"""

# from . import sources  # sibling imports work because sources.py inserts
# this directory on sys.path before discovery; use plain `import sources`
# style if you prefer.

NAME = "example"              # unique id (also used as cache key)
LABEL = "Example CLI"         # human display name in the UI
BADGE = "CLI"                 # optional short chip text
HOMEPAGE = "https://…"        # optional project URL
ORDER = 100                   # optional sort weight (lower = earlier)
COMBINED_PRIORITY = 100       # optional: only for DEDUPE_GROUP members
DEDUPE_GROUP = None           # optional: str group id, see sources.combined()


def scan(days: int = 30) -> dict:
    res = {
        "available": False,
        "days": days,
        "totals": {"input": 0, "output": 0, "cached": 0, "total": 0},
        "daily": {},
        "models": {},
        "meta": {},
    }
    # ... locate files under the user's home dir, parse, fold via sources.add()
    return res


def limits() -> dict:
    """Optional: predictable-window quota snapshot (rendered as a limit card).

    Shape: {"available": True, "label": "...", "badge": "...",
            "windows": [{"name": "5-hour", "used_pct": 42.0,
                         "used": 1234, "cap": 5000,
                         "resets_at": <unix s>, "exceeded": false}],
            **extra scalars}
    """
    return {"available": False}
