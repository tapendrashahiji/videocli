"""Merge away cuts shorter than ``min_cut``; drop clips shorter than ``min_clip``."""

from __future__ import annotations

from typing import Any

from videocli.core.timeutil import complement
from videocli.edl.planners.cuts import Cut, merge_cuts


def apply(cuts: list[Cut], duration: float, *, min_cut: float, min_clip: float) -> tuple[list[Cut], dict[str, Any]]:
    kept_cuts = [c for c in cuts if c.duration >= min_cut or c.reason in ("manual", "keep-only")]
    dropped_cuts = len(cuts) - len(kept_cuts)
    keep = complement([(c.start, c.end) for c in kept_cuts], 0.0, duration)
    tiny = [(a, b) for a, b in keep if b - a < min_clip]
    new_cuts = kept_cuts + [Cut(a, b, "smooth") for a, b in tiny]
    return merge_cuts(new_cuts), {"merged_cuts": dropped_cuts, "dropped_clips": len(tiny)}
