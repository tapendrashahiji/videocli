"""Placement from layout data: what is occupied in the output frame over an output-time range,
and the best free spot for a graphic's box."""

from __future__ import annotations

from typing import Any

from videocli.layout import geometry as geo

FACE_GROW = 1.6  # a detected face box is smaller than the head (or the webcam bubble)


def _layout_doc(ws: Any, source_id: str, cache: dict[str, Any]) -> dict[str, Any] | None:
    if source_id not in cache:
        from videocli.core.cache import read_artifact

        cache[source_id] = read_artifact(ws.source_dir(source_id) / "layout.json")
    return cache[source_id]


def has_layout(edl: Any, ws: Any) -> bool:
    cache: dict[str, Any] = {}
    return any(_layout_doc(ws, c.source, cache) for c in edl.tracks.main)


def occupied(edl: Any, ws: Any, out_start: float, out_end: float, *, step: float = 0.5,
             kinds: tuple[str, ...] = ("text", "faces", "objects"), cache: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Boxes (output-frame fractions) of text blocks, faces and objects on screen during [out_start, out_end).

    Each item: ``{"kind", "box", "text"?, "window", "source"}``. Empty when no layout data exists.
    """
    cache = {} if cache is None else cache
    tm = edl.timemap()
    out_size = tuple(edl.resolution)
    seen: set[tuple[str, str, tuple[float, ...]]] = set()
    items: list[dict[str, Any]] = []
    t = out_start
    times = []
    while t < out_end:
        times.append(t)
        t += step
    times.append(max(out_start, out_end - 1e-3))
    for ot in times:
        hit = tm.to_source(ot)
        if hit is None:
            continue
        sp, st = hit
        doc = _layout_doc(ws, sp.source, cache)
        if not doc:
            continue
        frame = doc.get("frame") or [16, 9]
        src_size = (int(frame[0]) or 16, int(frame[1]) or 9)
        cx = edl.reframe.crops.get(sp.id, 0.5) if edl.reframe is not None else 0.5
        for w in doc.get("windows", []):
            if not (w["start"] <= st < w["end"]):
                continue
            entries: list[tuple[str, list[float], str | None]] = []
            if "text" in kinds:
                entries += [("text", b["box"], b["text"]) for b in w.get("blocks", [])]
            if "faces" in kinds:
                entries += [("face", geo.expand(f["box"], FACE_GROW), None) for f in w.get("faces", [])]
            if "objects" in kinds:
                entries += [("object", o["box"], o.get("label")) for o in w.get("objects", [])]
            for kind, box, text in entries:
                mapped = geo.map_box(box, src_size, out_size, edl.reframe, cx)  # type: ignore[arg-type]
                if mapped is None or geo.area(mapped) <= 0:
                    continue
                key = (w["id"], kind, tuple(mapped))
                if key in seen:
                    continue
                seen.add(key)
                item: dict[str, Any] = {"kind": kind, "box": mapped, "window": w["id"], "source": sp.source}
                if text:
                    item["text"] = text
                items.append(item)
    return items


def overlap_report(box: list[float], occ: list[dict[str, Any]], threshold: float = 0.15) -> list[dict[str, Any]]:
    """Occupied items that ``box`` covers: more than ``threshold`` of the item's area, or more
    than 10% of the box itself (a small label sitting on a big block of text)."""
    out = []
    for it in occ:
        share = geo.covered_share(it["box"], box)
        own = geo.covered_share(box, it["box"])
        if share > threshold or own > 0.10:
            out.append({**it, "covered": round(share, 2)})
    return out


def best_spot(size: tuple[float, float], default: tuple[float, float], occ: list[dict[str, Any]], *,
              margin: float = 0.03, avoid: list[list[float]] | None = None, step: float = 0.025,
              gap: float = 0.03) -> tuple[float, float, float]:
    """Top-left (x, y) for a box of ``size`` (fractions) that covers the least of ``occ``
    (kept ``gap`` away from it, so a label does not hug the text), preferring spots near
    ``default``. Returns (x, y, covered area share of the box)."""
    w, h = size
    boxes = [[b[0] - gap, b[1] - gap, b[2] + 2 * gap, b[3] + 2 * gap] for b in (it["box"] for it in occ)] + list(avoid or [])
    best: tuple[float, float, float, float] | None = None
    nx = max(1, int((1 - 2 * margin - w) / step) + 1)
    ny = max(1, int((1 - 2 * margin - h) / step) + 1)
    for i in range(nx):
        for j in range(ny):
            x, y = margin + i * step, margin + j * step
            cand = [x, y, w, h]
            cover = sum(geo.intersection(cand, b) for b in boxes) / max(1e-9, w * h)
            dist = abs(x - default[0]) + abs(y - default[1])
            cost = cover * 10 + dist
            if best is None or cost < best[0]:
                best = (cost, x, y, cover)
    if best is None:
        return default[0], default[1], 1.0
    return round(best[1], 4), round(best[2], 4), round(best[3], 3)


def graphic_box(ws: Any, edl: Any, template: str, params_given: dict[str, Any]) -> list[float] | None:
    """The graphic's box in the output frame (fractions), from the template's real layout."""
    from videocli.graphics import templates as tp
    from videocli.graphics.titles import layout

    t = tp.get(ws, template)
    if t.baker != "titles":
        return None
    brand = tp.load_brand(ws)
    p = tp.resolve_params(t, params_given, brand)
    W, H = edl.resolution
    lay = layout(t, p, brand, W, H)
    x0, y0, x1, y1 = lay.box
    if (x1 - x0) >= W * 0.95 and (y1 - y0) >= H * 0.95:
        return None  # full-frame cards (title, end card) cover everything by design
    return [round(x0 / W, 4), round(y0 / H, 4), round((x1 - x0) / W, 4), round((y1 - y0) / H, 4)]


def auto_place(ws: Any, edl: Any, template: str, params_given: dict[str, Any], out_start: float, out_end: float) -> dict[str, Any]:
    """Decide where a graphic goes. Returns ``{"moved", "box", "params"?, "covers", "reason"}``."""
    box = graphic_box(ws, edl, template, params_given)
    if box is None:
        return {"moved": False, "reason": "full-frame or non-title graphic; not placed"}
    occ = occupied(edl, ws, out_start, out_end)
    if not occ and not has_layout(edl, ws):
        return {"moved": False, "box": box, "reason": "no layout data (run: videocli analyze FILE --only layout)"}
    covers = overlap_report(box, occ)
    if not covers:
        return {"moved": False, "box": box, "covers": [], "reason": "default position is clear"}
    x, y, left = best_spot((box[2], box[3]), (box[0], box[1]), occ)
    new = [x, y, box[2], box[3]]
    still = overlap_report(new, occ)
    what = ", ".join(f"{c['kind']} {c.get('text', '')[:30]!r}".strip() for c in covers[:3])
    return {"moved": True, "box": new, "params": {"box_x": x, "box_y": y}, "covers": still,
            "reason": f"default position covered {what}" + ("" if not still else "; no fully free spot, least-covered chosen")}


def caption_bands(out_size: tuple[int, int], margin: float | None = None, band: float = 0.14) -> tuple[list[float], list[float]]:
    """Approximate bottom and top caption bands (output fractions) for ``margin`` from the edge."""
    W, H = out_size
    m = margin if margin is not None else (0.22 if H > W else 0.08)
    return [0.08, max(0.0, 1 - m - band), 0.84, band], [0.08, m, 0.84, band]


def caption_avoid(edl: Any, ws: Any, *, margin: float | None = None, step: float = 0.5) -> list[tuple[float, float]]:
    """Output-time ranges where the bottom caption band would cover on-screen text or a face
    and the top band is clearer. Empty without layout data."""
    if not has_layout(edl, ws):
        return []
    bottom, top = caption_bands(tuple(edl.resolution), margin)
    cache: dict[str, Any] = {}
    dur = edl.output_duration()
    ranges: list[tuple[float, float]] = []
    t = 0.0
    while t < dur:
        occ = occupied(edl, ws, t, min(dur, t + step), step=step, kinds=("text", "faces"), cache=cache)
        cb = sum(geo.intersection(bottom, it["box"]) for it in occ)
        ct = sum(geo.intersection(top, it["box"]) for it in occ)
        if cb > 0.002 and ct < cb * 0.5:
            if ranges and abs(ranges[-1][1] - t) < 1e-6:
                ranges[-1] = (ranges[-1][0], min(dur, t + step))
            else:
                ranges.append((t, min(dur, t + step)))
        t += step
    return ranges


def median_face(edl: Any, ws: Any, grow: float = 2.0) -> list[float] | None:
    """The usual face box over the timeline's sources (median of layout faces, then faces.json),
    grown to include head and shoulders. Source fractions; None when no faces were found."""
    import statistics

    from videocli.core.cache import read_artifact

    boxes: list[list[float]] = []
    for sid in dict.fromkeys(c.source for c in edl.tracks.main):
        doc = read_artifact(ws.source_dir(sid) / "layout.json")
        for w in (doc or {}).get("windows", []):
            weight = max(1, int(round(w["end"] - w["start"])))
            boxes += [f["box"] for f in w.get("faces", [])] * weight
        if not boxes:
            fdoc = read_artifact(ws.source_dir(sid) / "faces.json")
            boxes += [[f["x"] - f["w"] / 2, f["y"] - f["h"] / 2, f["w"], f["h"]] for f in (fdoc or {}).get("faces", [])]
    if not boxes:
        return None
    med = [statistics.median(b[i] for b in boxes) for i in range(4)]
    return geo.expand(med, grow, grow * 0.9)
