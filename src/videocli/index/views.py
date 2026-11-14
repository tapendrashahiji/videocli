"""Text views of a transcript: compact, segments, words, SRT, VTT."""

from __future__ import annotations

from typing import Any

from videocli.core.timeutil import format_time, ms


def in_window(a: float, b: float, frm: float | None, to: float | None) -> bool:
    """True when [a, b) overlaps the window [frm, to)."""
    return (frm is None or b > frm) and (to is None or a < to)


def select_segments(transcript: dict[str, Any], frm: float | None, to: float | None) -> list[dict[str, Any]]:
    return [s for s in transcript.get("segments", []) if in_window(s["start"], s["end"], frm, to)]


def compact_lines(segments: list[dict[str, Any]], *, min_gap: float = 1.0, width: int = 4) -> list[str]:
    """One line per segment; gaps of at least ``min_gap`` seconds appear inline as silences."""
    lines: list[str] = []
    prev_end: float | None = None
    for s in segments:
        if prev_end is not None and s["start"] - prev_end >= min_gap:
            lines.append(f"[{format_time(prev_end, width=width)}-{format_time(s['start'], width=width)}] (silence {s['start'] - prev_end:.2f})")
        lines.append(f"[{format_time(s['start'], width=width)}-{format_time(s['end'], width=width)}] {s['text']}")
        prev_end = s["end"]
    return lines


def words_list(segments: list[dict[str, Any]], frm: float | None, to: float | None) -> list[dict[str, Any]]:
    out = []
    for s in segments:
        for w in s.get("words", []):
            if in_window(w["start"], w["end"], frm, to):
                out.append({"start": w["start"], "end": w["end"], "word": w["word"], "p": w.get("p"), "segment": s["id"]})
    return out


def _ts(t: float, sep: str) -> str:
    t = max(0.0, t)
    h, rem = divmod(int(round(t * 1000)), 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, msec = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{msec:03d}"


def srt(cues: list[tuple[float, float, str]]) -> str:
    blocks = []
    for i, (a, b, text) in enumerate(cues, 1):
        blocks.append(f"{i}\n{_ts(a, ',')} --> {_ts(b, ',')}\n{text}\n")
    return "\n".join(blocks)


def vtt(cues: list[tuple[float, float, str]]) -> str:
    out = ["WEBVTT", ""]
    for a, b, text in cues:
        out += [f"{_ts(a, '.')} --> {_ts(b, '.')}", text, ""]
    return "\n".join(out)


def segment_cues(segments: list[dict[str, Any]]) -> list[tuple[float, float, str]]:
    return [(ms(s["start"]), ms(s["end"]), s["text"]) for s in segments if s["text"]]
