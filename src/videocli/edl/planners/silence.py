"""Remove silences of at least ``min_len``, leaving ``pad`` seconds of air on each side."""

from __future__ import annotations

from videocli.core.timeutil import Range
from videocli.edl.planners.cuts import Cut


def plan(silences: list[Range], *, min_len: float, pad: float) -> list[Cut]:
    out = []
    for a, b in silences:
        if b - a < min_len:
            continue
        s, e = a + pad, b - pad
        if e - s > 1e-6:  # a pad larger than half the silence leaves nothing to cut
            out.append(Cut(s, e, "silence"))
    return out
