"""Clean-up accuracy metrics (phase 4): boundary precision/recall and keep/cut agreement."""

from __future__ import annotations

from collections.abc import Sequence

Range = tuple[float, float]


def boundaries(keep: Sequence[Range], duration: float, eps: float = 1e-6) -> list[float]:
    """Cut boundaries: every keep-range edge except the file start and end."""
    out = []
    for a, b in sorted(keep):
        if a > eps:
            out.append(a)
        if b < duration - eps:
            out.append(b)
    return sorted(set(round(x, 6) for x in out))


def match(tool: Sequence[float], human: Sequence[float], tol: float) -> int:
    """Greedy one-to-one matching of boundaries within ``tol`` seconds."""
    used = [False] * len(human)
    hits = 0
    for t in sorted(tool):
        best, best_d = None, None
        for i, h in enumerate(human):
            if used[i]:
                continue
            d = abs(t - h)
            if d <= tol and (best_d is None or d < best_d):
                best, best_d = i, d
        if best is not None:
            used[best] = True
            hits += 1
    return hits


def precision_recall(tool_keep: Sequence[Range], human_keep: Sequence[Range], duration: float, tol: float = 0.25) -> dict[str, float]:
    tb = boundaries(tool_keep, duration)
    hb = boundaries(human_keep, duration)
    hits = match(tb, hb, tol)
    precision = hits / len(tb) if tb else (1.0 if not hb else 0.0)
    recall = hits / len(hb) if hb else (1.0 if not tb else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
            "tool_boundaries": len(tb), "human_boundaries": len(hb), "matched": hits}


def _covered(ranges: Sequence[Range], t: float) -> bool:
    return any(a <= t < b for a, b in ranges)


def agreement(tool_keep: Sequence[Range], human_keep: Sequence[Range], duration: float) -> float:
    """Share of the source duration where tool and human agree on keep versus cut (exact, by intervals)."""
    if duration <= 0:
        return 1.0
    points = sorted({0.0, duration, *[x for r in tool_keep for x in r], *[x for r in human_keep for x in r]})
    agree = 0.0
    for a, b in zip(points, points[1:], strict=False):
        if b <= a or a >= duration:
            continue
        mid = (a + b) / 2
        if _covered(tool_keep, mid) == _covered(human_keep, mid):
            agree += min(b, duration) - a
    return round(agree / duration, 4)
