"""Style profiles: taste as numbers measured from finished reference edits (SPEC §8.7).

The same metric functions measure an EDL (``style compare``, ``audit``), so a profile and an
edit are always compared like for like.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from videocli.core.fsutil import read_json, write_json
from videocli.core.timeutil import ms
from videocli.core.workspace import Workspace

SCHEMA = "videocli.style/v1"


def pace_metrics(shot_lengths: list[float], shot_static: list[bool] | None = None, duration: float | None = None) -> dict[str, Any]:
    if not shot_lengths:
        return {}
    L = np.asarray(shot_lengths, dtype=np.float64)
    duration = duration or float(L.sum())
    starts = np.concatenate([[0.0], np.cumsum(L)[:-1]])
    curve = []
    for k in range(5):
        a, b = duration * k / 5, duration * (k + 1) / 5
        cuts = int(((starts > a) & (starts <= b)).sum())
        curve.append(round(cuts / max((b - a) / 60, 1e-6), 1))
    static = [x for x, s in zip(L, shot_static or [True] * len(L), strict=False) if s]
    return {
        "asl": round(float(L.mean()), 2), "median_shot": round(float(np.median(L)), 2), "p90_shot": round(float(np.percentile(L, 90)), 2),
        "max_static_hold": round(float(max(static) if static else 0.0), 2), "cuts_per_min": round((len(L) - 1) / max(duration / 60, 1e-6), 1),
        "cuts_per_min_curve": curve,
    }


def cut_on_beat_rate(cuts: list[float], beats: list[float], tol: float = 0.08) -> float | None:
    if not cuts or not beats:
        return None
    b = np.asarray(beats)
    hits = sum(1 for c in cuts if np.min(np.abs(b - c)) <= tol)
    return round(hits / len(cuts), 3)


def measure_source(ws: Workspace, src: Any, settings: Any) -> dict[str, Any]:
    """Run the analyzers a profile needs on one reference video and turn them into metrics."""
    from videocli.analyze import base
    from videocli.analyze.base import load_artifact
    from videocli.color import scopes as sc
    from videocli.media.frames import extract_many

    names = ["scenes", "motion", "loudness", "events", "music"]
    if base.get("transcript").available(base.RunContext(ws=ws, settings=settings, options={})).ok:
        names.append("transcript")
    ctx = base.RunContext(ws=ws, settings=settings, options={})
    base.run_analyzers(src, names, ctx, explicit=set())
    scenes = (load_artifact(ws, src, "scenes", required=False) or {}).get("scenes", [])
    motion = {s["id"]: s for s in (load_artifact(ws, src, "motion", required=False) or {}).get("shots", [])}
    dur = float(ctx.probe.duration if ctx.probe else (scenes[-1]["end"] if scenes else 0))
    lengths = [s["end"] - s["start"] for s in scenes]
    static = [motion.get(s["id"], {}).get("camera") in (None, "static") for s in scenes]
    out: dict[str, Any] = {"duration": ms(dur), "pace": pace_metrics(lengths, static, dur)}
    music = load_artifact(ws, src, "music", required=False) or {}
    events = (load_artifact(ws, src, "events", required=False) or {}).get("events", [])
    presence = sum(e["end"] - e["start"] for e in events if e["kind"] == "music_present") / max(dur, 1e-6)
    cuts = [s["start"] for s in scenes[1:]]
    out["music"] = {"presence": round(min(1.0, presence), 3), "cut_on_beat_rate": cut_on_beat_rate(cuts, music.get("beats", [])),
                    "tempo": music.get("tempo")}
    tr = load_artifact(ws, src, "transcript", required=False)
    if tr:
        words = [w for s in tr.get("segments", []) for w in s.get("words", [])]
        pauses = [b["start"] - a["end"] for a, b in zip(words, words[1:], strict=False) if b["start"] - a["end"] > 0.1]
        out["speech"] = {"wpm": round(len(words) / max(dur / 60, 1e-6), 1), "kept_pause_p50": round(float(np.median(pauses)), 2) if pauses else None}
    lo = load_artifact(ws, src, "loudness", required=False) or {}
    out["sound"] = {"integrated_lufs": lo.get("integrated_lufs")}
    times = [round(dur * (k + 0.5) / 8, 2) for k in range(8)] if dur else []
    if ctx.probe and ctx.probe.video is not None and times:
        frames = extract_many(src.path, times, ws.source_dir(src.id) / "frames", width=160)
        rgb = np.concatenate([sc.load_rgb(p) for p in frames])
        m = sc.measure(rgb)
        out["color"] = {"contrast": m["contrast"], "saturation": m["saturation"], "warmth": m["warmth"]}
    out["grammar"] = {"dissolve_rate": None, "broll_coverage": None, "split_edit_rate": None, "size_transitions": {}}
    return out


def merge(refs: list[dict[str, Any]], name: str, paths: list[str]) -> dict[str, Any]:
    """Average the references; keep the spread so planners know how strict each number is."""
    prof: dict[str, Any] = {"schema": SCHEMA, "name": name, "from": paths, "spread": {}}
    for group in ("pace", "music", "speech", "color", "sound", "grammar"):
        keys = {k for r in refs for k in (r.get(group) or {})}
        prof[group] = {}
        for k in sorted(keys):
            vals = [r[group][k] for r in refs if isinstance((r.get(group) or {}).get(k), (int, float))]
            lists = [r[group][k] for r in refs if isinstance((r.get(group) or {}).get(k), list)]
            if vals:
                prof[group][k] = round(float(np.mean(vals)), 3)
                prof["spread"][f"{group}.{k}"] = round(float(np.std(vals)), 3) if len(vals) > 1 else None
            elif lists:
                n = min(len(x) for x in lists)
                prof[group][k] = [round(float(np.mean([x[i] for x in lists])), 1) for i in range(n)]
            else:
                prof[group][k] = None
    return prof


def path(ws: Workspace, name: str) -> Path:
    return ws.dir("project/styles") / f"{name}.style.json"


def load(ws: Workspace, name: str) -> dict[str, Any]:
    from videocli.core.errors import InputError

    p = path(ws, name)
    if not p.is_file():
        raise InputError("ARTIFACT_MISSING", f"No style profile named {name!r}.", hint=f"Run: videocli style learn refs/*.mp4 -o {name}")
    return read_json(p)


def save(ws: Workspace, prof: dict[str, Any]) -> Path:
    p = path(ws, prof["name"])
    write_json(p, prof)
    return p


# --------------------------------------------------------------------------- EDL metrics


def edl_metrics(ws: Workspace, edl: Any) -> dict[str, Any]:
    """The profile's metrics measured on an EDL: shots are spine clips split by scene changes inside them."""
    from videocli.core.cache import read_artifact

    tm = edl.timemap()
    shots: list[tuple[float, bool, str, float, float]] = []  # (length, static, source, src_start, src_end)
    overlay_time = 0.0
    for sp in tm.spans:
        sc = (read_artifact(ws.source_dir(sp.source) / "scenes.json") or {}).get("scenes", [])
        mo = {s["id"]: s for s in (read_artifact(ws.source_dir(sp.source) / "motion.json") or {}).get("shots", [])}
        bounds = sorted({sp.start, sp.end, *[s["start"] for s in sc if sp.start < s["start"] < sp.end]})
        for a, b in zip(bounds, bounds[1:], strict=False):
            sid = next((s["id"] for s in sc if s["start"] <= a < s["end"]), None)
            static = mo.get(sid, {}).get("camera", "static") == "static"
            shots.append(((b - a) / sp.speed, static, sp.source, a, b))
    for ov in edl.tracks.overlay:
        overlay_time += ov.end - ov.start
    # overlays cut the picture too: split the shots they cover (approximation: count each overlay as one shot)
    lengths = [s[0] for s in shots]
    dur = tm.duration
    pace = pace_metrics(lengths + [ov.end - ov.start for ov in edl.tracks.overlay], [s[1] for s in shots] + [False] * len(edl.tracks.overlay), dur)
    cuts = [sp.out_start for sp in tm.spans[1:]]
    beats: list[float] = []
    for it in edl.tracks.audio:
        if it.role == "music":
            mu = read_artifact(ws.source_dir(it.source) / "music.json") or {}
            beats += [(it.at or 0) + b - it.start for b in mu.get("beats", [])]
    dissolves = sum(1 for t in edl.transitions if t.type == "dissolve")
    return {
        "duration": ms(dur),
        "pace": pace,
        "grammar": {"dissolve_rate": round(dissolves / max(1, len(cuts)), 3), "broll_coverage": round(overlay_time / max(dur, 1e-6), 3),
                    "split_edit_rate": round(sum(1 for c in edl.tracks.main if c.audio.lead or c.audio.lag) / max(1, len(cuts)), 3)},
        "music": {"presence": round(min(1.0, sum(((it.end or 0) - it.start) if not it.fit else dur for it in edl.tracks.audio if it.role == "music") / max(dur, 1e-6)), 3),
                  "cut_on_beat_rate": cut_on_beat_rate(cuts, beats)},
        "_shots": shots,
    }


TOLERANCE = {"pace.asl": 0.3, "pace.median_shot": 0.35, "pace.p90_shot": 0.4, "pace.max_static_hold": 0.3, "pace.cuts_per_min": 0.3,
             "grammar.broll_coverage": 0.1, "grammar.dissolve_rate": 0.05, "music.cut_on_beat_rate": 0.1, "music.presence": 0.2}
ABSOLUTE = {"grammar.broll_coverage", "grammar.dissolve_rate", "music.cut_on_beat_rate", "music.presence"}
SLOW_IS_BAD = {"pace.asl": 1, "pace.median_shot": 1, "pace.p90_shot": 1, "pace.max_static_hold": 1, "pace.cuts_per_min": -1}


def compare(profile: dict[str, Any], metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Each metric beside the profile, with deviation and a status. Too slow fails at 1x tolerance, too fast at 2x."""
    out = []
    for key, tol_rel in TOLERANCE.items():
        g, k = key.split(".")
        target = (profile.get(g) or {}).get(k)
        value = (metrics.get(g) or {}).get(k)
        if not isinstance(target, (int, float)) or not isinstance(value, (int, float)):
            continue
        spread = (profile.get("spread") or {}).get(key)
        tol = tol_rel if key in ABSOLUTE else max(tol_rel * abs(target), spread or 0.0)
        dev = value - target
        direction = SLOW_IS_BAD.get(key, 0)
        slow = direction and dev * direction > 0
        limit = tol if (slow or not direction) else 2 * tol
        status = "pass" if abs(dev) <= limit else "fail"
        out.append({"name": f"{k}" if g == "pace" else key.replace(".", "_"), "metric": key, "value": round(float(value), 3),
                    "target": round(float(target), 3), "tolerance": round(float(tol), 3), "deviation": round(float(dev), 3),
                    "direction": ("too slow" if slow else "too fast") if direction and status == "fail" else None, "status": status})
    return out
