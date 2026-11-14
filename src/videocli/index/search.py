"""Transcript search: case-insensitive phrases across word sequences, or regex over text."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from videocli.core.errors import UsageError
from videocli.core.timeutil import ms

_PUNCT = re.compile(r"[^\w']+", re.UNICODE)


def norm_token(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).casefold()
    t = t.replace("’", "'")
    return _PUNCT.sub("", t).strip("'")


def tokens(text: str) -> list[str]:
    return [t for t in (norm_token(x) for x in text.split()) if t]


def flat_words(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []):
            out.append({**w, "segment": seg["id"], "norm": norm_token(w["word"])})
    return [w for w in out if w["norm"]]


def _snippet(segments: list[dict[str, Any]], seg_id: int) -> str:
    seg = next((s for s in segments if s["id"] == seg_id), None)
    return f"…{seg['text']}" if seg else ""


def _context(segments: list[dict[str, Any]], first: int, last: int, n: int) -> list[dict[str, Any]]:
    if n <= 0:
        return []
    ids = {s["id"] for s in segments}
    lo, hi = first - n, last + n
    return [{"id": s["id"], "start": s["start"], "end": s["end"], "text": s["text"]} for s in segments if lo <= s["id"] <= hi and s["id"] in ids]


def find_phrase(transcript: dict[str, Any], query: str, *, context: int = 0, frm: float | None = None,
                to: float | None = None) -> list[dict[str, Any]]:
    q = tokens(query)
    if not q:
        raise UsageError("BAD_ARGUMENT", "The query has no words to search for.")
    words = flat_words(transcript)
    segments = transcript.get("segments", [])
    out = []
    n = len(q)
    i = 0
    while i <= len(words) - n:
        if all(words[i + k]["norm"] == q[k] for k in range(n)):
            a, b = words[i]["start"], words[i + n - 1]["end"]
            if (frm is None or a >= frm) and (to is None or b <= to):
                hit = {
                    "start": ms(a), "end": ms(b), "segment": words[i]["segment"],
                    "text": _snippet(segments, words[i]["segment"]),
                    "words": " ".join(w["word"] for w in words[i:i + n]),
                }
                if context:
                    hit["context"] = _context(segments, words[i]["segment"], words[i + n - 1]["segment"], context)
                out.append(hit)
            i += n
        else:
            i += 1
    return out


def find_regex(transcript: dict[str, Any], pattern: str, *, context: int = 0, frm: float | None = None,
               to: float | None = None) -> list[dict[str, Any]]:
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise UsageError("BAD_ARGUMENT", f"Invalid regular expression: {exc}") from exc
    segments = transcript.get("segments", [])
    out = []
    for seg in segments:
        words = seg.get("words", [])
        if words:
            text, spans, pos = "", [], 0
            for w in words:
                if text:
                    text += " "
                    pos += 1
                spans.append((pos, pos + len(w["word"]), w))
                text += w["word"]
                pos += len(w["word"])
        else:
            text, spans = seg["text"], []
        for m in rx.finditer(text):
            if m.end() == m.start():
                continue
            hit_words = [w for a, b, w in spans if a < m.end() and b > m.start()]
            a = hit_words[0]["start"] if hit_words else seg["start"]
            b = hit_words[-1]["end"] if hit_words else seg["end"]
            if (frm is not None and a < frm) or (to is not None and b > to):
                continue
            hit = {"start": ms(a), "end": ms(b), "segment": seg["id"], "text": f"…{seg['text']}", "match": m.group(0)}
            if context:
                hit["context"] = _context(segments, seg["id"], seg["id"], context)
            out.append(hit)
    return out
