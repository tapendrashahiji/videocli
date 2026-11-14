"""EDL operations on the spine, with anchor maintenance (phase 2).

Every op mutates the EDL in place and returns a before/after summary. Items anchored to a
spine clip (overlays, graphics, anchored audio) are re-anchored to whichever clip now plays
their source moment; if none does they keep their anchor and ``edl lint`` reports them.
"""

from __future__ import annotations

from typing import Any

from videocli.core.errors import InputError, UsageError
from videocli.core.timeutil import intersect, ms, normalize, subtract
from videocli.edl.model import EDL, Clip, CutRecord

EPS = 1e-3


# --------------------------------------------------------------------------- helpers


def summary(edl: EDL) -> dict[str, Any]:
    return {"clips": len(edl.tracks.main), "output_duration": ms(edl.output_duration())}


def pick_source(edl: EDL, source: str | None) -> str:
    if source:
        if source in edl.sources:
            return source
        matches = [sid for sid, s in edl.sources.items() if s.path.endswith(source) or sid.startswith(source)]
        if len(matches) == 1:
            return matches[0]
        raise UsageError("BAD_ARGUMENT", f"Unknown source {source!r} in EDL {edl.name}.", hint=f"Sources: {', '.join(edl.sources)}")
    used = list(dict.fromkeys(c.source for c in edl.tracks.main)) or list(edl.sources)
    if len(used) == 1:
        return used[0]
    raise UsageError("BAD_ARGUMENT", "This EDL has several sources; say which with --source ID.", hint=f"Sources: {', '.join(used)}")


def get_clip(edl: EDL, clip_id: str) -> Clip:
    c = edl.clip(clip_id)
    if c is None:
        raise InputError("ITEM_NOT_FOUND", f"No clip {clip_id!r} in EDL {edl.name}.", hint=f"Run: videocli edl show {edl.name} --clips")
    return c


def _capture_anchors(edl: EDL) -> list[tuple[Any, str, float, str]]:
    pts = []
    for it in edl.anchored():
        c = edl.clip(it.anchor)
        if c is None:
            continue
        pts.append((it, c.source, c.start + it.offset * c.speed, c.id))
    return pts


def _reanchor(edl: EDL, pts: list[tuple[Any, str, float, str]]) -> list[dict[str, Any]]:
    changes = []
    for it, source, t, old in pts:
        target = next((c for c in edl.tracks.main if c.source == source and c.start - EPS <= t < c.end - EPS), None)
        if target is None:
            prev = edl.clip(old)
            if prev is not None:
                it.offset = ms((t - prev.start) / prev.speed)
            changes.append({"item": it.id, "anchor": old, "status": "orphan" if prev is None else "out_of_range"})
            continue
        new_offset = ms((t - target.start) / target.speed)
        if target.id != it.anchor or abs(new_offset - it.offset) > 1e-6:
            changes.append({"item": it.id, "from": it.anchor, "to": target.id, "offset": new_offset})
        it.anchor = target.id
        it.offset = new_offset
    return changes


def _record_cut(edl: EDL, source: str, a: float, b: float, reason: str) -> None:
    others = [r for r in edl.cuts if r.source != source]
    mine = [r for r in edl.cuts if r.source == source]
    pieces: list[CutRecord] = []
    for r in mine:  # the new reason wins where they overlap
        for x, y in subtract([(r.start, r.end)], [(a, b)]):
            pieces.append(CutRecord(source=source, start=ms(x), end=ms(y), reason=r.reason))
    pieces.append(CutRecord(source=source, start=ms(a), end=ms(b), reason=reason))
    pieces.sort(key=lambda r: r.start)
    edl.cuts = others + pieces


def _unrecord(edl: EDL, source: str, a: float, b: float) -> None:
    out = []
    for r in edl.cuts:
        if r.source != source:
            out.append(r)
            continue
        for x, y in subtract([(r.start, r.end)], [(a, b)]):
            out.append(CutRecord(source=source, start=ms(x), end=ms(y), reason=r.reason))
    edl.cuts = out


def merge_adjacent(edl: EDL) -> list[str]:
    """Join neighbouring clips that are contiguous in the same source."""
    after_transition = {t.after for t in edl.transitions}
    merged: list[str] = []
    out: list[Clip] = []
    for c in edl.tracks.main:
        if out:
            p = out[-1]
            if (
                p.source == c.source and abs(p.end - c.start) < EPS and p.speed == c.speed and p.grade == c.grade
                and p.audio == c.audio and not p.locked and not c.locked and p.id not in after_transition
            ):
                p.end = c.end
                merged.append(c.id)
                continue
        out.append(c)
    edl.tracks.main = out
    return merged


def _wrap(edl: EDL, fn, *args, **kwargs) -> dict[str, Any]:
    before = summary(edl)
    before_ids = [c.id for c in edl.tracks.main]
    pts = _capture_anchors(edl)
    info = fn(edl, *args, **kwargs) or {}
    merged = merge_adjacent(edl)
    anchors = _reanchor(edl, pts)
    after_ids = [c.id for c in edl.tracks.main]
    edl.transitions = [t for t in edl.transitions if t.after in set(after_ids)]
    out = {
        "before": before,
        "after": summary(edl),
        "added": [i for i in after_ids if i not in before_ids],
        "removed": [i for i in before_ids if i not in after_ids],
        **info,
    }
    if merged:
        out["merged"] = merged
    if anchors:
        out["anchors"] = anchors
    return out


# --------------------------------------------------------------------------- ops


def _cut(edl: EDL, source: str, a: float, b: float, reason: str) -> dict[str, Any]:
    new_main: list[Clip] = []
    touched_locked = []
    for c in edl.tracks.main:
        if c.source != source or c.end <= a or c.start >= b:
            new_main.append(c)
            continue
        if c.locked:
            touched_locked.append(c.id)
        pieces = subtract([(c.start, c.end)], [(a, b)])
        for i, (x, y) in enumerate(pieces):
            if y - x < EPS:
                continue
            if i == 0:
                c.start, c.end = ms(x), ms(y)
                new_main.append(c)
            else:
                nc = c.model_copy(deep=True)
                nc.id = _fresh_id(edl, new_main)
                nc.start, nc.end = ms(x), ms(y)
                new_main.append(nc)
    edl.tracks.main = new_main
    _record_cut(edl, source, a, b, reason)
    return {"locked_touched": touched_locked} if touched_locked else {}


def _fresh_id(edl: EDL, pending: list[Clip]) -> str:
    used = edl.all_ids() | {c.id for c in pending}
    nums = [int(i[1:]) for i in used if i.startswith("c") and i[1:].isdigit()]
    return f"c{max(nums, default=0) + 1:04d}"


def cut(edl: EDL, a: float, b: float, *, source: str | None = None, reason: str = "manual") -> dict[str, Any]:
    if b <= a:
        raise UsageError("BAD_ARGUMENT", "--to must be after --from.")
    sid = pick_source(edl, source)
    return _wrap(edl, _cut, sid, a, min(b, edl.sources[sid].duration or b), reason)


def _keep(edl: EDL, source: str, a: float, b: float, reason: str) -> dict[str, Any]:
    dur = edl.sources[source].duration or b
    a, b = max(0.0, a), min(b, dur)
    covered = [(c.start, c.end) for c in edl.tracks.main if c.source == source]
    missing = subtract([(a, b)], covered)
    added = []
    for x, y in missing:
        if y - x < EPS:
            continue
        nc = Clip(id=_fresh_id(edl, edl.tracks.main), source=source, start=ms(x), end=ms(y), reason=reason)
        main = edl.tracks.main
        pos = None
        for i, c in enumerate(main):
            if c.source == source and c.end <= x + EPS:
                pos = i + 1
        if pos is None:
            pos = next((i for i, c in enumerate(main) if c.source == source and c.start >= y - EPS), len(main))
        main.insert(pos, nc)
        added.append(nc.id)
    _unrecord(edl, source, a, b)
    return {"restored_seconds": ms(sum(y - x for x, y in missing))}


def keep(edl: EDL, a: float, b: float, *, source: str | None = None, reason: str = "manual") -> dict[str, Any]:
    if b <= a:
        raise UsageError("BAD_ARGUMENT", "--to must be after --from.")
    sid = pick_source(edl, source)
    return _wrap(edl, _keep, sid, a, b, reason)


def restore(edl: EDL, a: float, b: float, *, source: str | None = None) -> dict[str, Any]:
    return keep(edl, a, b, source=source, reason="restored")


def _trim(edl: EDL, clip_id: str, start: float | None, end: float | None) -> dict[str, Any]:
    c = get_clip(edl, clip_id)
    ns = c.start if start is None else start
    ne = c.end if end is None else end
    dur = edl.sources[c.source].duration
    if ns < 0 or (dur and ne > dur + 0.05):
        raise UsageError("BAD_ARGUMENT", f"Trim would leave clip {clip_id} outside its source (0-{dur}).")
    if ne - ns < EPS:
        raise UsageError("BAD_ARGUMENT", f"Trim would leave clip {clip_id} with no duration.")
    old = (c.start, c.end)
    c.start, c.end = ms(ns), ms(min(ne, dur) if dur else ne)
    if c.start < old[0]:
        _unrecord(edl, c.source, c.start, old[0])
    if c.end > old[1]:
        _unrecord(edl, c.source, old[1], c.end)
    if c.start > old[0]:
        _record_cut(edl, c.source, old[0], c.start, "trim")
    if c.end < old[1]:
        _record_cut(edl, c.source, c.end, old[1], "trim")
    return {"clip": clip_id, "from": list(old), "to": [c.start, c.end]}


def trim(edl: EDL, clip_id: str, *, start: float | None = None, end: float | None = None) -> dict[str, Any]:
    if start is None and end is None:
        raise UsageError("BAD_ARGUMENT", "Give --start and/or --end (source times).")
    return _wrap(edl, _trim, clip_id, start, end)


def _split(edl: EDL, clip_id: str, at: float) -> dict[str, Any]:
    c = get_clip(edl, clip_id)
    if not (c.start + EPS < at < c.end - EPS):
        raise UsageError("BAD_ARGUMENT", f"--at {at} must fall inside clip {clip_id} ({c.start}-{c.end}, source time).")
    i = edl.tracks.main.index(c)
    nc = c.model_copy(deep=True)
    nc.id = _fresh_id(edl, edl.tracks.main)
    nc.start = ms(at)
    c.end = ms(at)
    edl.tracks.main.insert(i + 1, nc)
    for t in edl.transitions:  # a transition after the clip now follows its second half
        if t.after == c.id:
            t.after = nc.id
    return {"clips": [c.id, nc.id]}


def split(edl: EDL, clip_id: str, at: float) -> dict[str, Any]:
    # A split must not be undone by merge_adjacent, so bypass the merge step.
    before = summary(edl)
    pts = _capture_anchors(edl)
    info = _split(edl, clip_id, at)
    anchors = _reanchor(edl, pts)
    out = {"before": before, "after": summary(edl), "added": [info["clips"][1]], "removed": [], **info}
    if anchors:
        out["anchors"] = anchors
    return out


def _move(edl: EDL, clip_id: str, before: str | None, after: str | None) -> dict[str, Any]:
    c = get_clip(edl, clip_id)
    if (before is None) == (after is None):
        raise UsageError("BAD_ARGUMENT", "Give exactly one of --before CLIP_ID or --after CLIP_ID.")
    ref_id = before or after
    if ref_id == clip_id:
        raise UsageError("BAD_ARGUMENT", "A clip cannot move relative to itself.")
    if ref_id not in ("start", "end"):
        get_clip(edl, ref_id)  # type: ignore[arg-type]
    main = [x for x in edl.tracks.main if x.id != clip_id]
    if ref_id == "start":
        idx = 0
    elif ref_id == "end":
        idx = len(main)
    else:
        idx = next(i for i, x in enumerate(main) if x.id == ref_id) + (1 if after else 0)
    main.insert(idx, c)
    edl.tracks.main = main
    return {"order": [x.id for x in main][:50]}


def move(edl: EDL, clip_id: str, *, before: str | None = None, after: str | None = None) -> dict[str, Any]:
    before_s = summary(edl)
    pts = _capture_anchors(edl)
    info = _move(edl, clip_id, before, after)
    anchors = _reanchor(edl, pts)
    out = {"before": before_s, "after": summary(edl), "added": [], "removed": [], **info}
    if anchors:
        out["anchors"] = anchors
    return out


def lock(edl: EDL, item_id: str, locked: bool = True) -> dict[str, Any]:
    it = edl.item(item_id)
    if it is None:
        raise InputError("ITEM_NOT_FOUND", f"No item {item_id!r} in EDL {edl.name}.")
    it.locked = locked
    return {"item": item_id, "locked": locked}


# --------------------------------------------------------------------------- reading


def removed_ranges(edl: EDL) -> list[dict[str, Any]]:
    """Source ranges not in the spine, with the recorded reason (or 'manual')."""
    out = []
    for sid, s in edl.sources.items():
        covered = [(c.start, c.end) for c in edl.tracks.main if c.source == sid]
        if not covered and not any(r.source == sid for r in edl.cuts):
            continue  # a source used only by other tracks
        gaps = subtract([(0.0, s.duration)], covered)
        recs = [(r.start, r.end, r.reason) for r in edl.cuts if r.source == sid]
        for a, b in gaps:
            if b - a < EPS:
                continue
            parts = []
            for x, y, reason in recs:
                for p, q in intersect([(a, b)], [(x, y)]):
                    parts.append((p, q, reason))
            covered_by_rec = normalize([(p, q) for p, q, _ in parts])
            for p, q in subtract([(a, b)], covered_by_rec):
                parts.append((p, q, "manual"))
            for p, q, reason in sorted(parts):
                if q - p >= EPS:
                    out.append({"source": sid, "from": ms(p), "to": ms(q), "duration": ms(q - p), "reason": reason})
    return out


def show(edl: EDL, *, with_clips: bool = False, limit: int = 50) -> dict[str, Any]:
    src_total = sum(s.duration for sid, s in edl.sources.items() if any(c.source == sid for c in edl.tracks.main))
    out_dur = edl.output_duration()
    removed = removed_ranges(edl)
    removed_secs = sum(r["duration"] for r in removed)
    by_reason: dict[str, float] = {}
    for r in removed:
        by_reason[r["reason"]] = ms(by_reason.get(r["reason"], 0.0) + r["duration"])
    longest = max(removed, key=lambda r: r["duration"], default=None)
    data: dict[str, Any] = {
        "name": edl.name,
        "rate": edl.rate,
        "resolution": list(edl.resolution),
        "tracks": {"main": len(edl.tracks.main), "overlay": len(edl.tracks.overlay), "graphics": len(edl.tracks.graphics), "audio": len(edl.tracks.audio)},
        "transitions": len(edl.transitions),
        "source_duration": ms(src_total),
        "output_duration": ms(out_dur),
        "removed_seconds": ms(removed_secs),
        "removed_percent": round(100 * removed_secs / src_total, 1) if src_total else 0.0,
        "shortest_clip": ms(min((c.out_duration for c in edl.tracks.main), default=0.0)),
        "longest_cut": ({"from": longest["from"], "to": longest["to"], "reason": longest["reason"]} if longest else None),
        "by_reason": by_reason,
        "revision": len(edl.history),
    }
    if with_clips:
        tm = edl.timemap()
        clips = []
        for c, sp in zip(edl.tracks.main, tm.spans, strict=False):
            item = {"id": c.id, "source": c.source, "start": c.start, "end": c.end, "out_start": ms(sp.out_start), "out_end": ms(sp.out_end), "reason": c.reason}
            if c.speed != 1.0:
                item["speed"] = c.speed
            if c.locked:
                item["locked"] = True
            clips.append(item)
        data["total"] = len(clips)
        data["truncated"] = len(clips) > limit
        data["clips"] = clips[:limit]
    return data


def diff(a: EDL, b: EDL) -> dict[str, Any]:
    ca = {c.id: c for c in a.tracks.main}
    cb = {c.id: c for c in b.tracks.main}
    changed = []
    for cid in ca.keys() & cb.keys():
        x, y = ca[cid].model_dump(), cb[cid].model_dump()
        fields = {k: {"from": x[k], "to": y[k]} for k in x if x[k] != y[k]}
        if fields:
            changed.append({"id": cid, "fields": fields})
    order_a = [i for i in ca if i in cb]
    order_b = [i for i in cb if i in ca]
    return {
        "a": a.name,
        "b": b.name,
        "added": [i for i in cb if i not in ca],
        "removed": [i for i in ca if i not in cb],
        "changed": sorted(changed, key=lambda c: c["id"]),
        "reordered": order_a != order_b,
        "output_duration": {"a": ms(a.output_duration()), "b": ms(b.output_duration()), "delta": ms(b.output_duration() - a.output_duration())},
        "other_tracks": {
            t: {"a": len(getattr(a.tracks, t)), "b": len(getattr(b.tracks, t))} for t in ("overlay", "graphics", "audio")
        },
    }
