"""Fit a music track to the edit length by rearranging at downbeats, with cross-fades."""

from __future__ import annotations

from typing import Any

from videocli.core.cache import read_artifact
from videocli.core.timeutil import ms
from videocli.core.workspace import Workspace


def fit(ws: Workspace, src: Any, length: float, xfade: float = 0.5) -> dict[str, Any]:
    """Parts to place: the opening up to a downbeat, then an ending that starts on a downbeat.

    Too long: keep the intro and the real ending, joined at downbeats (the cut is reported).
    Too short: loop whole bars from the last section start.
    """
    from videocli.analyze import base

    ctx = base.RunContext(ws=ws, settings=__import__("videocli.core.config", fromlist=["load"]).load(None))
    base.run_analyzers(src, ["music"], ctx, explicit={"music"})
    mu = read_artifact(ws.source_dir(src.id) / "music.json") or {}
    dur = float(ctx.probe.duration if ctx.probe else 0.0)
    downs = [d for d in mu.get("downbeats", []) if 0 < d < dur]
    compromises = []
    if dur <= 0:
        return {"parts": [], "compromises": ["music has no duration"]}
    if abs(dur - length) < 0.25 or not downs:
        if not downs and abs(dur - length) >= 0.25:
            compromises.append("no downbeats found: trimmed with a fade")
        return {"parts": [{"start": 0.0, "end": ms(min(dur, length)), "at": 0.0, "fade": 1.0}], "compromises": compromises, "tempo": mu.get("tempo")}
    if dur > length:
        # choose head cut h and tail start t (both downbeats) with h + (dur - t) ≈ length, preferring a long head
        best = None
        for h in downs:
            for t in downs:
                if t <= h:
                    continue
                total = h + (dur - t)
                err = abs(total - length)
                if total <= length + 0.05 and (best is None or err < best[0] - 1e-6 or (abs(err - best[0]) < 1e-6 and h > best[1])):
                    best = (err, h, t)
        if best is None:
            return {"parts": [{"start": 0.0, "end": ms(length), "at": 0.0, "fade": 1.5}], "compromises": ["edit shorter than one bar pair: faded out"]}
        err, h, t = best
        if err > 0.3:
            compromises.append(f"music ends {err:.2f}s before the edit; faded")
        compromises.append(f"music cut from {h:.2f}s to {t:.2f}s at downbeats")
        return {"parts": [{"start": 0.0, "end": ms(h + xfade / 2), "at": 0.0, "fade": xfade},
                          {"start": ms(t - xfade / 2), "end": ms(dur), "at": ms(h - xfade / 2), "fade": xfade}],
                "compromises": compromises, "tempo": mu.get("tempo")}
    # too short: loop from the last section (or the last 8 bars) at downbeats
    secs = mu.get("sections") or []
    loop_from = secs[-1]["start"] if secs else (downs[-8] if len(downs) >= 8 else downs[0])
    parts = [{"start": 0.0, "end": ms(dur), "at": 0.0, "fade": xfade}]
    at = dur - xfade
    while at < length - 0.5:
        seg = dur - loop_from
        parts.append({"start": ms(loop_from), "end": ms(min(dur, loop_from + (length - at) + xfade)), "at": ms(at), "fade": xfade})
        at += seg - xfade
    compromises.append(f"looped from {loop_from:.2f}s to cover the edit")
    return {"parts": parts, "compromises": compromises, "tempo": mu.get("tempo")}
