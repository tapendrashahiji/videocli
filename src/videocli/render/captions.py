"""Captions re-timed to the edit: words mapped through the EDL to output time, then grouped into cues."""

from __future__ import annotations

import textwrap
from typing import Any

from videocli.core.timeutil import ms
from videocli.edl.model import EDL

MAX_CUE = 6.0
MIN_CUE = 0.7
GAP_BREAK = 0.8
HOLD = 0.3


def map_words(edl: EDL, transcripts: dict[str, dict[str, Any] | None]) -> list[dict[str, Any]]:
    """Every transcript word that survives the edit, at output time (a word must be mostly kept)."""
    tm = edl.timemap()
    out = []
    for sp in tm.spans:
        tr = transcripts.get(sp.source)
        if not tr:
            continue
        for seg in tr.get("segments", []):
            if seg["end"] <= sp.start or seg["start"] >= sp.end:
                continue
            for w in seg.get("words", []):
                a, b = w["start"], w["end"]
                lo, hi = max(a, sp.start), min(b, sp.end)
                if hi - lo < 0.5 * (b - a):
                    continue
                oa = sp.out_start + (lo - sp.start) / sp.speed
                ob = sp.out_start + (hi - sp.start) / sp.speed
                out.append({"start": ms(oa), "end": ms(ob), "word": w["word"], "clip": sp.id})
    out.sort(key=lambda w: w["start"])
    return out


def group_cues(words: list[dict[str, Any]], *, max_chars: int = 42, max_lines: int = 2, duration: float | None = None) -> list[tuple[float, float, str]]:
    cues: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    limit = max_chars * max_lines
    for w in words:
        if cur:
            text_len = len(" ".join(x["word"] for x in cur)) + 1 + len(w["word"])
            prev = cur[-1]
            sentence_end = prev["word"].rstrip('"\')]').endswith((".", "?", "!"))
            if (
                text_len > limit
                or w["start"] - prev["end"] > GAP_BREAK
                or w["end"] - cur[0]["start"] > MAX_CUE
                or (sentence_end and len(" ".join(x["word"] for x in cur)) >= max_chars // 2)
                or w["clip"] != prev["clip"] and w["start"] - prev["end"] > 0.3
            ):
                cues.append(cur)
                cur = []
        cur.append(w)
    if cur:
        cues.append(cur)

    out: list[tuple[float, float, str]] = []
    for i, c in enumerate(cues):
        start = c[0]["start"]
        end = c[-1]["end"] + HOLD
        nxt = cues[i + 1][0]["start"] if i + 1 < len(cues) else None
        end = max(end, start + MIN_CUE)
        if nxt is not None:
            end = min(end, nxt - 0.001)
        if duration is not None:
            end = min(end, duration)
        if end <= start:
            continue
        text = " ".join(x["word"] for x in c)
        lines = textwrap.wrap(text, width=max_chars, break_long_words=False) or [text]
        if len(lines) > max_lines:
            lines = textwrap.wrap(text, width=max(max_chars, -(-len(text) // max_lines)), break_long_words=False)[:max_lines] or lines[:max_lines]
        out.append((ms(start), ms(end), "\n".join(lines)))
    return out


def build(edl: EDL, transcripts: dict[str, dict[str, Any] | None], *, max_chars: int | None = None, max_lines: int | None = None) -> list[tuple[float, float, str]]:
    words = map_words(edl, transcripts)
    return group_cues(
        words,
        max_chars=max_chars or edl.captions.max_chars,
        max_lines=max_lines or edl.captions.max_lines,
        duration=edl.output_duration(),
    )
