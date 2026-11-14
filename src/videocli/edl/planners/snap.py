"""Move a cut boundary that falls inside a word to the nearest word edge."""

from __future__ import annotations

from bisect import bisect_right
from typing import Any

from videocli.core.timeutil import ms
from videocli.edl.planners.cuts import Cut, merge_cuts

EPS = 0.005


def _inside(words: list[dict[str, Any]], starts: list[float], t: float) -> dict[str, Any] | None:
    i = bisect_right(starts, t) - 1
    if i >= 0:
        w = words[i]
        if w["start"] + EPS < t < w["end"] - EPS:
            return w
    return None


def snap_time(words: list[dict[str, Any]], starts: list[float], t: float) -> float:
    w = _inside(words, starts, t)
    if w is None:
        return t
    return w["start"] if t - w["start"] <= w["end"] - t else w["end"]


def apply(cuts: list[Cut], words: list[dict[str, Any]], duration: float, locked: list[tuple[float, float]] | None = None) -> tuple[list[Cut], int]:
    words = sorted(words, key=lambda w: w["start"])
    starts = [w["start"] for w in words]
    locked = locked or []
    moved = 0
    out = []
    for c in cuts:
        a = snap_time(words, starts, c.start)
        b = snap_time(words, starts, c.end)
        # Never move a boundary into a locked range: keep the original boundary there.
        if any(la < a < lb for la, lb in locked):
            a = c.start
        if any(la < b < lb for la, lb in locked):
            b = c.end
        if (a, b) != (c.start, c.end):
            moved += 1
        if b > a:
            out.append(Cut(ms(max(0.0, a)), ms(min(duration, b)), c.reason))
    return merge_cuts(out), moved
