"""Anthropic Claude Code CLI — session transcripts.

Data: ``~/.claude/projects/<encoded-project-path>/<session-uuid>.jsonl``
(Windows: ``%USERPROFILE%\\.claude\\projects\\...``). The project folder is
the working directory with path separators munged to dashes; one append-only
JSONL file per session, named after the session UUID. Subagent transcripts
live in a sibling ``<session-uuid>/subagents/agent-<id>.jsonl`` and are
scanned too (they are real token spend).

FORMAT FACTS (verified against claude-dev.tools JSONL reference, the
EveryInc/cctime transcript guide and the adityabawankule.io 2026 format
write-up; ~4k real files audited by that author):
- Every line: self-contained JSON with top-level ``type`` ("user" /
  "assistant" / "system" / bookkeeping types), ``uuid``, ``parentUuid``,
  ``timestamp`` (ISO 8601 UTC, e.g. "2026-07-09T15:04:11.220Z"),
  ``sessionId``, ``cwd``.
- Assistant lines nest the raw Anthropic API response under ``message``:
  ``message.model``, ``message.id`` (msg_…), ``message.content[]``,
  ``message.usage`` = {``input_tokens``, ``output_tokens``,
  ``cache_creation_input_tokens``, ``cache_read_input_tokens``}.

SEMANTICS (critical — where we parse tokens):
- usage values are PER-API-REQUEST totals for that turn, NOT deltas across
  lines and NOT cumulative across the file. BUT one API turn is written as
  SEVERAL consecutive assistant lines (one per streamed content block) and
  every line repeats the SAME usage object. Naively summing every assistant
  line overcounts by the block count (2–10x). We therefore dedupe: keep the
  LAST usage seen per unique ``message.id`` within a file (streamed counts
  finalize as the turn completes), then count each id exactly once.
- Known limitation: Claude Code writes lines while streaming, so some
  entries carry placeholder input/output counts that are never finalized
  (github.com/anthropics/claude-code/issues/28197). Last-write-wins dedupe
  keeps the most complete value available on disk; numbers may still
  undercount true API usage slightly. cache_* fields match statusbar totals
  closely per third-party audits.
- Bucket mapping: Anthropic reports cache tokens OUTSIDE ``input_tokens``
  (unlike OpenAI). We fold them into the contract's "input" bucket so
  total == input + output stays comparable across providers:
      input  = input_tokens + cache_creation_input_tokens
               + cache_read_input_tokens
      output = output_tokens
      cached = cache_creation_input_tokens + cache_read_input_tokens
  (cached is informational and rides inside input).
"""

import os

import sources
from _util import home, cutoff_day, iter_lines, load_json, recent_files

NAME = "claudecode"
LABEL = "Claude Code CLI"
BADGE = "Claude Code"
HOMEPAGE = "https://code.claude.com/docs"
ORDER = 20


def scan(days: int = 30) -> dict:
    res = sources.empty_result(days)
    root = os.path.join(home(), ".claude", "projects")
    if not os.path.isdir(root):
        res["available"] = False
        res["error"] = f"no dir {root}"
        return res
    # Filenames are session UUIDs (no date), so recent_files falls back to
    # mtime; line-level timestamps below do the authoritative windowing.
    files = recent_files([os.path.join(root, "**", "*.jsonl")], days,
                         recursive=True)
    min_day = cutoff_day(days)
    scanned = events = skipped_old = 0
    for path in files:
        scanned += 1
        # last usage per message.id — see module docstring SEMANTICS
        turns: dict[str, tuple] = {}   # key -> (day, model, inp, out, cached)
        order: list[str] = []
        i = 0
        # '"input_tokens"' only appears on usage-bearing assistant lines:
        # cheap prefilter keeps json.loads off user/tool/bookkeeping lines.
        for line in iter_lines(path, ('"input_tokens"',)):
            o = load_json(line)
            if not isinstance(o, dict):
                continue
            msg = o.get("message")
            if not isinstance(msg, dict) or o.get("type") != "assistant":
                continue
            u = msg.get("usage")
            if not isinstance(u, dict):
                continue
            ts = o.get("timestamp") or ""
            day = ts[:10]
            if len(day) != 10 or day < "2000":
                continue
            if day < min_day:
                skipped_old += 1
                continue
            i += 1
            key = msg.get("id") or o.get("uuid") or f"__line{i}"
            inp = (int(u.get("input_tokens") or 0)
                   + int(u.get("cache_creation_input_tokens") or 0)
                   + int(u.get("cache_read_input_tokens") or 0))
            out = int(u.get("output_tokens") or 0)
            cach = (int(u.get("cache_creation_input_tokens") or 0)
                    + int(u.get("cache_read_input_tokens") or 0))
            if key not in turns:
                order.append(key)
            turns[key] = (day, msg.get("model") or "unknown", inp, out, cach)
        for key in order:
            day, model, inp, out, cach = turns[key]
            events += 1
            sources.add(res["daily"], res["models"], res["totals"], day,
                        f"claude/{model}", inp, out, cach)
    res["meta"] = {"files_scanned": scanned, "events_used": events}
    if scanned == 0:
        res["available"] = False
        res["error"] = "no transcript files in window"
    return res
