"""The shared cut type and reason-preserving union."""

from __future__ import annotations

from dataclasses import dataclass

from videocli.core.timeutil import ms

PRIORITY = {"manual": 5, "keep-only": 4, "filler": 3, "silence": 2, "smooth": 1}


@dataclass
class Cut:
    start: float
    end: float
    reason: str

    @property
    def duration(self) -> float:
        return self.end - self.start


def merge_cuts(cuts: list[Cut]) -> list[Cut]:
    """Union of cuts; a merged cut keeps the highest-priority reason among its parts."""
    items = sorted((c for c in cuts if c.end > c.start), key=lambda c: (c.start, c.end))
    out: list[Cut] = []
    for c in items:
        if out and c.start <= out[-1].end + 1e-9:
            last = out[-1]
            last.end = max(last.end, c.end)
            if PRIORITY.get(c.reason, 0) > PRIORITY.get(last.reason, 0):
                last.reason = c.reason
        else:
            out.append(Cut(c.start, c.end, c.reason))
    return [Cut(ms(c.start), ms(c.end), c.reason) for c in out]
