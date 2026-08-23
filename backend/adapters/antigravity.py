"""Google Antigravity (agentic IDE + CLI) — conversation transcripts.

Data locations (all under the user's home, Windows %USERPROFILE% included):

- IDE:  ~/.gemini/antigravity/brain/<conv-id>/.system_generated/logs/transcript*.jsonl
        ~/.gemini/antigravity/conversations/*            (protobuf blobs, opaque)
- CLI:  ~/.gemini/antigravity-cli/brain/<conv-id>/...     (same subtree layout;
        the CLI can also import IDE conversations)

WHY THIS ADAPTER USUALLY REPORTS available=False (semantics, verified):
Each transcript line is ONE agent STEP like
  {"step_index":N,"source":"MODEL","type":"PLANNER_RESPONSE",
   "status":"DONE","created_at":"2026-05-24T12:14:37Z","content":"...", ...}
Steps carry content, timestamps and tool calls — but NO per-request token
counts and NO model name. This was verified against a real install and
matches community reports (Google AI Dev Forum "How to check Token usage in
Antigravity Hooks", June 2026: token info is "not available" in
transcript.jsonl). Model + live context-window tokens appear ONLY in the
statusline stdout pipe (docs: "Status Line Customization"), which nothing
persists. The sibling conversations/*.pb files are protocol buffers without
a stable public schema, and the VS Code-style state.vscdb under
%APPDATA%/Antigravity holds UI state plus protobuf credit sentinels — no
per-request usage there either.

So scan() below is a DEFENSIVE BEST-EFFORT pass: if some current/future
build embeds usage-shaped records in the JSONL (we recognise several known
shapes — see _usage_of()), each such record is treated as exactly one
request EVENT (deltas, safe to sum) and folded into the daily/model/totals
buckets; otherwise the adapter degrades to available=False WITH CONCRETE
EVIDENCE (which directories existed, how many transcript files/steps were
seen). No numbers are ever invented.
"""

import os

import sources
from _util import home, recent_files, iter_lines, load_json

NAME = "antigravity"
LABEL = "Google Antigravity"
BADGE = "Antigravity"
HOMEPAGE = "https://antigravity.google/"
ORDER = 25

# (path parts under home, human tag) for every place data may live
_ROOTS = (
    ((".gemini", "antigravity"), "IDE"),
    ((".gemini", "antigravity-cli"), "CLI"),
)


def _int(v) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _pick(d: dict, *keys):
    """First present numeric value among aliases, else None."""
    for k in keys:
        n = _int(d.get(k))
        if n is not None:
            return n
    return None


def _usage_of(o: dict):
    """Extract (input, output, cached, model) from a step, or None.

    Known usage shapes folded here (each = ONE request's DELTA):
      usage: {input_tokens, output_tokens, cached_input_tokens}
      usageMetadata (Gemini API style): {promptTokenCount,
          candidatesTokenCount, cachedContentTokenCount}
      context_window.current_usage (statusline payload shape):
          {input_tokens, output_tokens, cache_read_input_tokens}
      tokens (Gemini CLI style): {input, output, cached}
    """
    cands = []
    for key in ("usage", "usageMetadata", "tokens"):
        v = o.get(key)
        if isinstance(v, dict):
            cands.append(v)
    cw = o.get("context_window")
    if isinstance(cw, dict) and isinstance(cw.get("current_usage"), dict):
        cands.append(cw["current_usage"])
    for d in cands:
        inp = _pick(d, "input_tokens", "promptTokenCount", "prompt_tokens", "input")
        out = _pick(d, "output_tokens", "candidatesTokenCount", "completion_tokens", "output")
        cach = _pick(d, "cached_input_tokens", "cache_read_input_tokens",
                     "cachedContentTokenCount", "cached")
        if inp is None and out is None:
            continue  # a dict without usable counters is not a usage record
        model = o.get("model")
        if isinstance(model, dict):
            model = model.get("display_name") or model.get("id")
        return (inp or 0, out or 0, cach or 0, str(model) if model else None)
    return None


def scan(days: int = 30) -> dict:
    res = sources.empty_result(days)
    base = home()
    patterns: list[str] = []      # transcript jsonl globs
    conv_globs: list[str] = []    # opaque .pb conversation globs (evidence only)
    for parts, _tag in _ROOTS:
        root = os.path.join(base, *parts)
        patterns.append(os.path.join(
            root, "brain", "**", ".system_generated", "logs", "*.jsonl"))
        conv_globs.append(os.path.join(root, "conversations", "*"))

    try:
        files = recent_files(patterns, days)
    except Exception:
        files = []

    # ---- evidence about what physically exists (for the unavailable case)
    from glob import glob as _glob
    existing_roots = [os.path.join(base, *parts) for parts, _t in _ROOTS
                      if os.path.isdir(os.path.join(base, *parts))]
    pb_count = sum(len(_glob(g)) for g in conv_globs)

    scanned = steps = events = 0
    for path in files:
        scanned += 1
        # 'oken' prefilters every known counter spelling (token/TokenCount)
        # so json.loads stays off the bulk of huge transcripts.
        for line in iter_lines(path, ("oken",)):
            o = load_json(line.strip())
            if not isinstance(o, dict):
                continue
            steps += 1
            u = _usage_of(o)
            if u is None:
                continue
            inp, out, cach, model = u
            # created_at is the documented step timestamp; timestamp kept as
            # a fallback alias in case the field is renamed.
            day = (o.get("created_at") or o.get("timestamp") or "")[:10]
            if not day:
                continue
            events += 1
            sources.add(res["daily"], res["models"], res["totals"], day,
                        f"antigravity/{model or 'unknown'}", inp, out, cach)

    res["meta"] = {
        "files_scanned": scanned,
        "steps_seen": steps,
        "events_used": events,
        "data_dirs_found": existing_roots,
        "protobuf_conversations": pb_count,
    }
    if not existing_roots:
        res["available"] = False
        res["error"] = (
            f"no antigravity data dir under {base} "
            "(checked .gemini/antigravity, .gemini/antigravity-cli)")
    elif scanned == 0:
        res["available"] = False
        res["error"] = (
            "antigravity dirs exist but hold no transcript files in window "
            f"(conversation storage: {pb_count} opaque protobuf blob(s), "
            "no token usage readable)")
    elif events == 0:
        res["available"] = False
        res["error"] = (
            f"{scanned} transcript file(s) seen but they carry no per-request "
            "token counts (Antigravity persists steps/content/timestamps only; "
            "usage lives server-side)")
    return res
