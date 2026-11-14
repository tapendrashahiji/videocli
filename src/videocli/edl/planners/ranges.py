"""``--cut-range`` and ``--keep-only``."""

from __future__ import annotations

from videocli.core.timeutil import Range, complement, normalize
from videocli.edl.planners.cuts import Cut


def plan(duration: float, cut_ranges: list[Range], keep_only: list[Range]) -> list[Cut]:
    cuts = [Cut(max(0.0, a), min(duration, b), "manual") for a, b in normalize(cut_ranges) if a < duration]
    if keep_only:
        cuts += [Cut(a, b, "keep-only") for a, b in complement(keep_only, 0.0, duration)]
    return cuts
