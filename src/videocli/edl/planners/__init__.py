"""Rule planners (SPEC §10.4): deterministic clean-up that turns facts into cuts.

Fixed order: ranges → silence → fillers → snap → smooth. Each step works on a list of
``Cut(start, end, reason)`` in source time; the clips are the complement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from videocli.core.timeutil import Range, complement, ms
from videocli.edl.planners import fillers, ranges, silence, smooth, snap
from videocli.edl.planners.cuts import Cut, merge_cuts


@dataclass
class PlanOptions:
    cut_silence: dict[str, float] | None = None  # {"min": 1.2, "pad": 0.15}
    cut_fillers: list[str] | None = None  # None: off; []: default words for the language
    cut_ranges: list[Range] = field(default_factory=list)
    keep_only: list[Range] = field(default_factory=list)
    min_clip: float = 0.3
    min_cut: float = 0.2
    filler_pad: float = 0.04
    snap: bool = True
    language: str | None = None


@dataclass
class PlanResult:
    keep: list[tuple[float, float, str]]
    cuts: list[Cut]
    steps: dict[str, dict[str, Any]]


def plan(duration: float, silences: list[Range], words: list[dict[str, Any]], opts: PlanOptions) -> PlanResult:
    steps: dict[str, dict[str, Any]] = {}
    cuts: list[Cut] = []

    cuts += ranges.plan(duration, opts.cut_ranges, opts.keep_only)
    steps["ranges"] = {"cuts": len(cuts)}

    if opts.cut_silence is not None:
        s = silence.plan(silences, min_len=opts.cut_silence.get("min", 1.0), pad=opts.cut_silence.get("pad", 0.15))
        steps["silence"] = {"cuts": len(s), "seconds": ms(sum(c.end - c.start for c in s))}
        cuts += s

    if opts.cut_fillers is not None:
        f, used = fillers.plan(words, opts.cut_fillers, language=opts.language, pad=opts.filler_pad)
        steps["fillers"] = {"cuts": len(f), "words": used}
        cuts += f

    cuts = merge_cuts(cuts)
    if opts.snap and words:
        cuts, moved = snap.apply(cuts, words, duration)
        steps["snap"] = {"moved": moved}
    cuts, smooth_info = smooth.apply(cuts, duration, min_cut=opts.min_cut, min_clip=opts.min_clip)
    steps["smooth"] = smooth_info

    keep_ranges = complement([(c.start, c.end) for c in cuts], 0.0, duration)
    reason = "keep-only" if opts.keep_only else "keep"
    keep = [(ms(a), ms(b), reason) for a, b in keep_ranges if b - a > 1e-6]
    return PlanResult(keep=keep, cuts=cuts, steps=steps)


__all__ = ["Cut", "PlanOptions", "PlanResult", "plan", "merge_cuts"]
