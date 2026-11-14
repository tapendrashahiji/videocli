"""``audit``: craft metrics against a style profile, brief requirements, and craft issues (phase 9).

Separate from ``verify`` (technical correctness). Pace checks lean toward catching edits that
are too slow, because that is the direction editors penalised.
"""

from __future__ import annotations

from typing import Any

from videocli.core.cache import read_artifact
from videocli.core.timeutil import ms
from videocli.core.workspace import Workspace
from videocli.edl.model import EDL

SCHEMA = "videocli.audit/v1"
SEVERITY = {"info": 0, "warn": 1, "error": 2}


def _brief_checks(ws: Workspace, edl: EDL, brief: Any) -> list[dict[str, Any]]:
    from videocli.render.captions import map_words

    out = []
    trs = {sid: read_artifact(ws.source_dir(sid) / "transcript.json") for sid in edl.sources}
    words = map_words(edl, trs)
    from videocli.index.search import tokens

    seq = [t for w in words for t in tokens(w["word"])]
    times = [w["start"] for w in words for _ in tokens(w["word"])]

    def find(phrase: str) -> float | None:
        q = tokens(phrase)
        for i in range(len(seq) - len(q) + 1):
            if seq[i:i + len(q)] == q:
                return times[i]
        return None

    for p in brief.must_include:
        at = find(p)
        out.append({"requirement": f"must-include: '{p}'", "status": "pass" if at is not None else "fail", "at": ms(at) if at is not None else None})
    for p in brief.must_avoid:
        at = find(p)
        out.append({"requirement": f"must-avoid: '{p}'", "status": "pass" if at is None else "fail", "at": ms(at) if at is not None else None})
    if brief.duration:
        d = edl.output_duration()
        ok = abs(d - brief.duration) <= max(5.0, 0.1 * brief.duration)
        out.append({"requirement": f"duration ≈ {brief.duration:.0f}s", "status": "pass" if ok else "fail", "value": ms(d)})
    if brief.aspect:
        w, h = edl.resolution
        from fractions import Fraction

        a, b = (int(x) for x in brief.aspect.split(":"))
        ok = abs(Fraction(w, h) - Fraction(a, b)) < Fraction(1, 50)
        out.append({"requirement": f"aspect {brief.aspect}", "status": "pass" if ok else "fail", "value": f"{w}x{h}",
                    "fix": None if ok else f"videocli edl reframe {edl.name} --aspect {brief.aspect}"})
    return out


def audit(ws: Workspace, edl: EDL, profile: dict[str, Any] | None, brief: Any) -> dict[str, Any]:
    from videocli.editorial import idioms
    from videocli.editorial.style import compare, edl_metrics

    metrics = edl_metrics(ws, edl)
    shots = metrics.pop("_shots")
    rows = compare(profile, metrics) if profile else []
    issues: list[dict[str, Any]] = []
    n = edl.name
    covered = {o.anchor for o in edl.tracks.overlay}
    for a, b, t in idioms.jump_cuts(edl):
        if b not in covered:
            issues.append({"kind": "jump_cut", "severity": "warn", "at": t, "clips": [a, b], "fix": f"videocli refine {n} --passes coverage,punch"})
    seen: dict[tuple[str, float], str] = {}
    for c in edl.tracks.main:
        key = (c.source, round(c.start, 1))
        if key in seen:
            issues.append({"kind": "repeated_shot", "severity": "warn", "clips": [seen[key], c.id]})
        seen[key] = c.id
    used_overlays: dict[tuple[str, float], str] = {}
    for o in edl.tracks.overlay:
        k = (o.source, round(o.start, 1))
        if k in used_overlays:
            issues.append({"kind": "repeated_shot", "severity": "warn", "items": [used_overlays[k], o.id]})
        used_overlays[k] = o.id
    target_diss = ((profile or {}).get("grammar") or {}).get("dissolve_rate")
    dissolves = [t for t in edl.transitions if t.type == "dissolve"]
    if dissolves and (target_diss is not None and len(dissolves) / max(1, len(edl.tracks.main) - 1) > target_diss + 0.1):
        for t in dissolves:
            issues.append({"kind": "unmotivated_dissolve", "severity": "info", "after": t.after,
                           "fix": f"videocli edl transition {n} {t.after} --type cut"})
    if edl.tracks.graphics:
        from videocli.graphics.lint import lint as gfx_lint

        g = gfx_lint(edl, ws, sample_frames=False)
        for i in g["issues"]:
            if i["rule"] in ("too_short_to_read", "simultaneous_reveals", "past_timeline_end"):
                issues.append({"kind": "graphics_timing", "severity": "warn", "item": i.get("item") or i.get("items"), "rule": i["rule"],
                               "fix": i.get("fix")})
    total = edl.output_duration()
    for it in edl.tracks.audio:
        start = it.at or 0.0
        if start >= total:
            issues.append({"kind": "orphaned_sound", "severity": "warn", "item": it.id, "at": ms(start)})
    for r in rows:
        if r["status"] == "fail":
            r["severity"] = "error" if r["direction"] == "too slow" else "warn"
            if r["metric"] == "pace.max_static_hold":
                long = max(shots, key=lambda s: s[0]) if shots else None
                r["at_clip_source"] = long[2] if long else None
    brief_rows = _brief_checks(ws, edl, brief) if brief is not None else []
    return {
        "schema": SCHEMA, "style": (profile or {}).get("name"), "edl": n,
        "brief": brief_rows, "metrics": rows, "issues": issues,
        "measured": metrics,
    }


def worst_severity(report: dict[str, Any]) -> str | None:
    sev = [r.get("severity", "error") for r in report["metrics"] if r["status"] == "fail"]
    sev += ["error" for b in report["brief"] if b["status"] == "fail"]
    sev += [i["severity"] for i in report["issues"]]
    return max(sev, key=lambda s: SEVERITY[s]) if sev else None
