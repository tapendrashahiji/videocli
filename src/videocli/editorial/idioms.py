"""Editorial planners (idioms) and the solver behind ``refine`` (phase 9).

Each idiom looks at the EDL and the facts and proposes changes with a cost (negative is an
improvement). The solver applies them greedily in a fixed, deterministic order, never touching
locked clips, and records each change's idiom and reason in the EDL history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from videocli.core.cache import read_artifact
from videocli.core.timeutil import ms
from videocli.core.workspace import Workspace
from videocli.edl.model import EDL

PASSES = ("coverage", "split-edits", "action", "beats", "pacing", "match", "punch")


@dataclass
class Change:
    idiom: str
    kind: str  # trim | overlay | split_edit | note
    clip: str | None
    cost: float
    reason: str
    args: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"idiom": self.idiom, "kind": self.kind, "clip": self.clip, "cost": round(self.cost, 3), "reason": self.reason, "args": self.args}


@dataclass
class Facts:
    ws: Workspace
    cache: dict[str, Any] = field(default_factory=dict)

    def art(self, sid: str, name: str) -> dict[str, Any]:
        k = f"{sid}:{name}"
        if k not in self.cache:
            self.cache[k] = read_artifact(self.ws.source_dir(sid) / f"{name}.json") or {}
        return self.cache[k]

    def words(self, sid: str) -> list[dict[str, Any]]:
        return [w for s in self.art(sid, "transcript").get("segments", []) for w in s.get("words", [])]


def _inside_word(words: list[dict[str, Any]], t: float) -> bool:
    return any(w["start"] + 0.01 < t < w["end"] - 0.01 for w in words)


def jump_cuts(edl: EDL) -> list[tuple[str, str, float]]:
    """Neighbouring spine clips from the same source and shot with a small gap: the picture jumps."""
    tm = edl.timemap()
    out = []
    for (a, b), sp in zip(zip(edl.tracks.main, edl.tracks.main[1:], strict=False), tm.spans, strict=False):
        if a.source == b.source and 0 < b.start - a.end < 30:
            out.append((a.id, b.id, ms(sp.out_end)))
    return out


def _limits(style: dict[str, Any] | None) -> dict[str, float]:
    pace = (style or {}).get("pace") or {}
    return {"max_hold": float(pace.get("max_static_hold") or 8.0), "asl": float(pace.get("asl") or 5.0)}


# --------------------------------------------------------------------------- idioms


def pacing(edl: EDL, f: Facts, style: dict[str, Any] | None) -> list[Change]:
    lim = _limits(style)
    out = []
    for c in edl.tracks.main:
        if c.out_duration <= lim["max_hold"]:
            continue
        words = [w for w in f.words(c.source) if c.start <= w["start"] and w["end"] <= c.end]
        if words:
            head = words[0]["start"] - c.start
            tail = c.end - words[-1]["end"]
            if head > 0.4 or tail > 0.4:
                out.append(Change("pacing", "trim", c.id, -(head + tail), f"{c.out_duration:.1f}s hold with {head + tail:.1f}s of dead air",
                                  {"start": ms(words[0]["start"] - 0.15), "end": ms(words[-1]["end"] + 0.2)}))
                continue
        if not words:
            new_end = ms(c.start + lim["max_hold"] * c.speed)
            out.append(Change("pacing", "trim", c.id, -(c.out_duration - lim["max_hold"]), f"{c.out_duration:.1f}s silent hold over the {lim['max_hold']:.1f}s limit",
                              {"end": new_end}))
        else:
            out.append(Change("pacing", "note", c.id, 0.5, f"{c.out_duration:.1f}s talking hold; needs coverage", {"coverage": True}))
    return out


def _broll(f: Facts, edl: EDL, used: set[tuple[str, str]]) -> list[dict[str, Any]]:
    from videocli.index.search import tokens

    spine_sources = {c.source for c in edl.tracks.main}
    out = []
    for sid in f.ws.data.get("sources", {}):
        if sid in spine_sources:
            continue
        for s in f.art(sid, "shots").get("shots", []):
            if s.get("usable") is False or (sid, s["id"]) in used:
                continue
            out.append({"source": sid, "shot": s, "tokens": set(tokens(s.get("description") or ""))})
    return out


def coverage(edl: EDL, f: Facts, style: dict[str, Any] | None) -> list[Change]:
    from videocli.index.search import tokens

    lim = _limits(style)
    used = {(o.source, "") for o in edl.tracks.overlay}
    pool = _broll(f, edl, used)
    taken: set[tuple[str, str]] = set()
    covered = {o.anchor for o in edl.tracks.overlay}
    targets = [(b, "jump cut") for _a, b, _t in jump_cuts(edl)]
    targets += [(c.id, f"{c.out_duration:.1f}s talking hold") for c in edl.tracks.main if c.out_duration > lim["max_hold"]]
    out = []
    for cid, why in targets:
        if cid in covered:
            continue
        c = edl.clip(cid)
        if c is None or c.locked:
            continue
        spoken = set(tokens(" ".join(w["word"] for w in f.words(c.source) if c.start <= w["start"] < c.end)))
        best, best_s = None, 0.0
        for cand in pool:
            key = (cand["source"], cand["shot"]["id"])
            if key in taken:
                continue
            stem = {t[:5] for t in cand["tokens"]}
            overlap = len(stem & {t[:5] for t in spoken})
            s = overlap + (0.1 if cand["tokens"] else 0)
            if s > best_s:
                best, best_s = cand, s
        if best is None:
            continue
        taken.add((best["source"], best["shot"]["id"]))
        sh = best["shot"]
        dur = min(3.0, sh["end"] - sh["start"], c.out_duration * 0.6)
        if dur < 0.8:
            continue
        offset = ms(0.0 if why == "jump cut" else min(1.0, c.out_duration * 0.2))
        out.append(Change("coverage", "overlay", cid, -best_s, f"cover {why} with {sh['id']} ('{sh.get('description') or 'shot'}')",
                          {"source": best["source"], "start": sh["start"], "end": ms(sh["start"] + dur), "offset": offset}))
    return out


def split_edits(edl: EDL, f: Facts, style: dict[str, Any] | None) -> list[Change]:
    rate = float(((style or {}).get("grammar") or {}).get("split_edit_rate") or 0.3)
    out = []
    main = edl.tracks.main
    for i, (a, b) in enumerate(zip(main, main[1:], strict=False)):
        speaker_change = a.source != b.source
        dia_a, dia_b = f.art(a.source, "diarize").get("turns", []), f.art(b.source, "diarize").get("turns", [])
        if dia_a and dia_b:
            sa = next((t["speaker"] for t in dia_a if t["start"] <= a.end - 0.1 < t["end"]), None)
            sb = next((t["speaker"] for t in dia_b if t["start"] <= b.start + 0.1 < t["end"]), None)
            speaker_change = sa is not None and sb is not None and sa != sb
        if not speaker_change or a.audio.lag or b.audio.lead:
            continue
        if (i % max(1, round(1 / max(rate, 0.05)))) != 0:
            continue
        out.append(Change("split-edits", "split_edit", a.id, -0.5, "speaker change: let the voice run over the next picture (L-cut)", {"lag": 0.4}))
    return out


def _nudge_end(edl: EDL, f: Facts, targets: dict[str, list[float]], window: float, idiom: str, label: str) -> list[Change]:
    out = []
    for c in edl.tracks.main[:-1]:
        if c.locked:
            continue
        words = f.words(c.source)
        cands = [t for t in targets.get(c.source, []) if abs(t - c.end) <= window and c.start + 0.5 < t <= edl.sources[c.source].duration]
        if not cands:
            continue
        t = min(cands, key=lambda x: abs(x - c.end))
        if abs(t - c.end) < 0.02 or _inside_word(words, t):
            continue
        out.append(Change(idiom, "trim", c.id, -0.3 + abs(t - c.end), f"move the cut {t - c.end:+.2f}s onto {label}", {"end": ms(t)}))
    return out


def action(edl: EDL, f: Facts, style: dict[str, Any] | None) -> list[Change]:
    peaks = {sid: f.art(sid, "motion").get("action_peaks", []) for sid in {c.source for c in edl.tracks.main}}
    out = _nudge_end(edl, f, peaks, 0.3, "action", "a motion peak")
    faces = {sid: f.art(sid, "faces").get("faces", []) for sid in {c.source for c in edl.tracks.main}}
    for c in edl.tracks.main:
        blink = [x for x in faces.get(c.source, []) if abs(x["t"] - c.end) < 0.26 and not x.get("eyes_open", True)]
        if blink:
            out.append(Change("action", "note", c.id, 0.2, "cut lands during a blink; consider moving it", {"at": c.end}))
    return out


def beats(edl: EDL, f: Facts, style: dict[str, Any] | None) -> list[Change]:
    if style is not None and ((style.get("music") or {}).get("cut_on_beat_rate") or 0) < 0.2:
        return []
    tm = edl.timemap()
    out_beats: list[float] = []
    for it in edl.tracks.audio:
        if it.role != "music":
            continue
        for b in f.art(it.source, "music").get("beats", []):
            if b >= it.start:
                out_beats.append((it.at or 0.0) + b - it.start)
    if not out_beats:
        return []
    out = []
    arr = np.asarray(out_beats)
    for c, sp in zip(edl.tracks.main[:-1], tm.spans, strict=False):
        if c.locked:
            continue
        nearest = float(arr[np.argmin(np.abs(arr - sp.out_end))])
        d = nearest - sp.out_end
        if 0.01 < abs(d) <= 0.12:
            new_end = ms(c.end + d * c.speed)
            if _inside_word(f.words(c.source), new_end) or new_end > edl.sources[c.source].duration:
                continue
            out.append(Change("beats", "trim", c.id, -0.4, f"move the cut {d:+.3f}s onto the beat at {nearest:.2f}s", {"end": new_end}))
    return out


def match(edl: EDL, f: Facts, style: dict[str, Any] | None) -> list[Change]:
    out = []
    for a, b in zip(edl.tracks.main, edl.tracks.main[1:], strict=False):
        if a.source == b.source:
            continue
        na = f.art(a.source, "embed")
        nb = f.art(b.source, "embed")
        if not na or not nb:
            continue
        out.append(Change("match", "note", a.id, 0.0, f"possible match cut {a.id}→{b.id} (compare sheets)", {"next": b.id}))
    return out


def punch(edl: EDL, f: Facts, style: dict[str, Any] | None) -> list[Change]:
    return [Change("punch", "note", b, -0.2, f"jump cut {a}→{b}: a punch-in (tighter crop) or coverage hides it", {"at": t})
            for a, b, t in jump_cuts(edl)]


IDIOMS = {"coverage": coverage, "split-edits": split_edits, "action": action, "beats": beats, "pacing": pacing, "match": match, "punch": punch}


def propose(edl: EDL, ws: Workspace, passes: list[str], style: dict[str, Any] | None) -> list[Change]:
    from videocli.core.errors import UsageError

    f = Facts(ws)
    out: list[Change] = []
    for p in passes:
        if p not in IDIOMS:
            raise UsageError("BAD_ARGUMENT", f"Unknown pass {p!r}.", hint=f"Passes: {', '.join(PASSES)}")
        out += IDIOMS[p](edl, f, style)
    locked = {c.id for c in edl.tracks.main if c.locked}
    out = [c for c in out if c.clip not in locked]
    out.sort(key=lambda c: (PASSES.index(c.idiom), c.cost, c.clip or ""))
    return out


def apply(edl: EDL, changes: list[Change], ws: Workspace) -> list[dict[str, Any]]:
    from videocli.core.source import lookup
    from videocli.core.workspace import now_iso
    from videocli.edl import ops
    from videocli.edl.build import source_ref
    from videocli.edl.model import HistoryEntry

    applied = []
    touched: set[str] = set()
    for ch in changes:
        if ch.cost >= 0 and ch.kind != "split_edit":
            continue
        c = edl.clip(ch.clip or "")
        if c is None or c.locked:
            continue
        if ch.kind == "trim" and ch.clip not in touched:
            ops.trim(edl, c.id, start=ch.args.get("start"), end=ch.args.get("end"))
        elif ch.kind == "overlay":
            src = edl.sources.get(ch.args["source"])
            if src is None:
                src, _ = source_ref(ws, lookup(ws, ch.args["source"]))
            ops.overlay(edl, ch.args["source"], src, ch.args["start"], ch.args["end"], on=c.id, offset=ch.args["offset"])
        elif ch.kind == "split_edit":
            ops.split_edit(edl, c.id, None, ch.args["lag"])
        else:
            continue
        touched.add(c.id)
        edl.history.append(HistoryEntry(op=f"refine:{ch.idiom}", at=now_iso(), clip=c.id, reason=ch.reason))
        applied.append(ch.as_dict())
    return applied
