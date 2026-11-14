"""Selects, stringouts and assembly in editor stages: radio edit, picture, music (phase 9)."""

from __future__ import annotations

from typing import Any

from videocli.core.cache import read_artifact
from videocli.core.errors import InputError
from videocli.core.timeutil import ms
from videocli.core.workspace import Workspace
from videocli.editorial.brief import Brief, Select, Story
from videocli.edl.model import EDL, AudioItem, Clip, Marker

FILLERS = {"um", "uh", "erm", "er", "ah", "hmm", "like"}


def candidates(ws: Workspace) -> list[dict[str, Any]]:
    """Every spoken passage (transcript segment) across registered sources, with a delivery score."""
    from videocli.index.search import norm_token
    from videocli.judge.suggest import retakes

    out = []
    for sid, rec in ws.data.get("sources", {}).items():
        tr = read_artifact(ws.source_dir(sid) / "transcript.json")
        if not tr:
            continue
        dropped = {(r["drop"]["start"], r["drop"]["end"]) for r in retakes(tr)}
        shots = (read_artifact(ws.source_dir(sid) / "shots.json") or {}).get("shots", [])
        for seg in tr.get("segments", []):
            words = seg.get("words", [])
            if len(words) < 3:
                continue
            low = sum(1 for w in words if (w.get("p") or 1) < 0.5) / len(words)
            fill = sum(1 for w in words if norm_token(w["word"]) in FILLERS) / len(words)
            retake = (seg["start"], seg["end"]) in dropped
            delivery = max(0.0, 1 - low - 2 * fill - (0.6 if retake else 0))
            usable = [s.get("usable") for s in shots if s["start"] < seg["end"] and s["end"] > seg["start"]]
            usability = 0.0 if any(u is False for u in usable) else 1.0
            out.append({"source": sid, "path": rec["path"], "start": seg["start"], "end": seg["end"], "text": seg["text"],
                        "delivery": round(delivery, 3), "usability": usability, "retake": retake})
    return out


def selects_order(ws: Workspace, story: Story, cands: list[dict[str, Any]], beat_ids: list[str] | None) -> dict[str, Any]:
    from videocli.providers import orders as om

    items = []
    listing = [f"[{i}] ({c['start']:.1f}-{c['end']:.1f}s) {c['text']}" for i, c in enumerate(cands)]
    for b in story.beats:
        if beat_ids and b.id not in beat_ids:
            continue
        items.append({"id": f"beat:{b.id}", "images": [],
                      "question": f"Beat '{b.id}' (about {b.target_seconds:.0f}s): {b.intent or 'see logline'}. "
                                  "Pick the passages that best serve this beat, best first, by their [index]. Say why in one sentence.",
                      "answer_schema": {"picks": ["number"], "why": "string"}})
    return om.create(ws, "beat_match", items, meta={"candidates": len(cands)},
                     brief=f"Logline: {story.logline}\nCandidates:\n" + "\n".join(listing))


def rank(story: Story, cands: list[dict[str, Any]], answers: dict[str, Any], per_beat: int) -> Story:
    for b in story.beats:
        a = answers.get(f"beat:{b.id}")
        picks = [int(p) for p in (a["answer"]["picks"] if a else []) if 0 <= int(p) < len(cands)]
        why = a["answer"].get("why", "") if a else ""
        scored = []
        for rank_i, idx in enumerate(picks):
            c = cands[idx]
            content = 1.0 / (1 + rank_i)
            score = round(0.5 * content + 0.4 * c["delivery"] + 0.1 * c["usability"], 3)
            reason = f"content #{rank_i + 1} for '{b.id}' ({why}); delivery {c['delivery']:.2f}" + ("; a retake" if c["retake"] else "")
            scored.append(Select(source=c["source"], start=c["start"], end=c["end"], score=score, why=reason, text=c["text"]))
        scored.sort(key=lambda s: -s.score)
        b.selects = scored[:per_beat]
        b.chosen = [0] if b.selects else []
    return story


def coverage_report(story: Story, cands: list[dict[str, Any]]) -> dict[str, Any]:
    used = {(s.source, s.start) for b in story.beats for s in b.selects}
    thin = [b.id for b in story.beats if len(b.selects) < 2]
    unused = [c for c in cands if (c["source"], c["start"]) not in used]
    return {"beats_with_few_selects": thin, "unselected_passages": len(unused), "unselected_seconds": ms(sum(c["end"] - c["start"] for c in unused))}


def _edl_from_ranges(ws: Workspace, name: str, ranges: list[tuple[str, float, float, str, bool]]) -> EDL:
    from videocli.core.source import lookup
    from videocli.edl.build import source_ref, timeline_from_probe

    if not ranges:
        raise InputError("EDL_INVALID", "Nothing to assemble: no selects.", hint="Run: videocli selects")
    first = None
    sources = {}
    for sid, *_ in ranges:
        if sid not in sources:
            src = lookup(ws, sid)
            ref, pr = source_ref(ws, src)
            sources[sid] = ref
            first = first or pr
    rate, res = timeline_from_probe(first)
    edl = EDL(name=name, rate=rate, resolution=res, sources=sources)
    edl.tracks.main = [Clip(id=f"c{i + 1:04d}", source=sid, start=ms(a), end=ms(b), reason=reason, locked=locked)
                       for i, (sid, a, b, reason, locked) in enumerate(ranges)]
    return edl


def stringout(ws: Workspace, story: Story, name: str, beat: str | None) -> EDL:
    ranges = [(s.source, s.start, s.end, f"select:{b.id}", False) for b in story.beats if not beat or b.id == beat for s in b.selects]
    return _edl_from_ranges(ws, name, ranges)


def radio(ws: Workspace, story: Story, brief: Brief, name: str) -> tuple[EDL, dict[str, Any]]:
    """Choose and order spoken selects to fit the target duration; must-include locked, must-avoid removed."""
    from videocli.editorial.brief import resolve_phrases

    picked: list[tuple[str, float, float, str, bool, float]] = []
    for b in story.beats:
        for i in (b.chosen or ([0] if b.selects else [])):
            if i < len(b.selects):
                s = b.selects[i]
                picked.append((s.source, s.start, s.end, f"beat:{b.id}", False, s.score))
    must = resolve_phrases(ws, brief.must_include) if brief.must_include else {}
    for phrase, hits in must.items():
        if not hits:
            continue
        h = hits[0]
        if not any(p[0] == h["source"] and p[1] <= h["start"] and h["end"] <= p[2] for p in picked):
            tr = read_artifact(ws.source_dir(h["source"]) / "transcript.json") or {}
            seg = next((s for s in tr.get("segments", []) if s["start"] <= h["start"] < s["end"]), None)
            a, b2 = (seg["start"], seg["end"]) if seg else (h["start"], h["end"])
            picked.append((h["source"], a, b2, f"must-include:{phrase}", True, 9.9))
        else:
            picked = [(p[0], p[1], p[2], p[3], True, p[5]) if (p[0] == h["source"] and p[1] <= h["start"] < p[2]) else p for p in picked]
    dropped = []
    target = brief.duration
    total = sum(p[2] - p[1] for p in picked)
    if target:
        # trim tails first: drop trailing sentence fragments beyond 85% of each clip when over budget
        while total > target * 1.05:
            cand = [p for p in picked if not p[4]]
            if not cand:
                break
            worst = min(cand, key=lambda p: p[5])
            picked.remove(worst)
            dropped.append({"source": worst[0], "start": worst[1], "end": worst[2], "reason": worst[3], "score": worst[5]})
            total = sum(p[2] - p[1] for p in picked)
    edl = _edl_from_ranges(ws, name, [p[:5] for p in picked])
    if brief.must_avoid:
        from videocli.edl import ops

        for phrase, hits in resolve_phrases(ws, brief.must_avoid).items():
            for h in hits:
                if any(c.source == h["source"] and c.start < h["end"] and c.end > h["start"] for c in edl.tracks.main):
                    ops.cut(edl, h["start"], h["end"], source=h["source"], reason=f"must-avoid:{phrase}")
    if brief.aspect in ("9:16", "1:1", "4:5"):
        from videocli.edl import ops

        ops.reframe(edl, brief.aspect)
    return edl, {"clips": len(edl.tracks.main), "duration": ms(edl.output_duration()), "target": target, "dropped": dropped}


def picture(ws: Workspace, edl: EDL, max_hold: float) -> dict[str, Any]:
    """Mark long talking-head stretches that need covering (the coverage pass fills them)."""
    tm = edl.timemap()
    marks = []
    for sp in tm.spans:
        if sp.out_duration > max_hold:
            t = ms(sp.out_start + max_hold * 0.6)
            edl.markers.append(Marker(at=t, kind="needs-coverage", text=f"{sp.id}: {sp.out_duration:.1f}s hold"))
            marks.append({"clip": sp.id, "at": t})
    return {"needs_coverage": marks}


def music_stage(ws: Workspace, edl: EDL, music_path: str) -> dict[str, Any]:
    from videocli.core.source import lookup
    from videocli.edl import ops
    from videocli.edl.build import source_ref
    from videocli.sound.musicfit import fit

    src = lookup(ws, music_path)
    ref, _ = source_ref(ws, src)
    plan = fit(ws, src, edl.output_duration())
    ops.add_source(edl, src.id, ref)
    for _k, part in enumerate(plan["parts"]):
        edl.tracks.audio.append(AudioItem(id=edl.next_id("m"), source=src.id, role="music", start=part["start"], end=part["end"],
                                          at=part["at"], gain_db=-14.0, duck_db=-10.0, fade=part["fade"]))
    return plan
