"""``gfx lint``: readability and placement rules for graphics items."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from videocli.core.timeutil import ms
from videocli.edl.model import EDL
from videocli.graphics import templates as tp
from videocli.graphics.timing import reading_time


def lint(edl: EDL, ws: Any, *, sample_frames: bool = True) -> dict[str, Any]:
    from videocli.graphics.titles import layout

    issues: list[dict[str, Any]] = []
    brand = tp.load_brand(ws)
    tm = edl.timemap() if edl.tracks.main else None
    spans = {sp.id: sp for sp in tm.spans} if tm else {}
    W, H = edl.resolution
    S = min(W, H)
    margin = brand.safe_margin * S
    reveals = []
    from videocli.layout.place import has_layout

    use_layout = bool(edl.tracks.main) and has_layout(edl, ws)
    layout_cache: dict[str, Any] = {}
    for g in edl.tracks.graphics:
        sp = spans.get(g.anchor)
        if sp is None:
            issues.append({"rule": "orphan_anchor", "severity": "error", "item": g.id, "anchor": g.anchor})
            continue
        try:
            t = tp.get(ws, g.template)
            params = tp.resolve_params(t, g.params, brand)
        except Exception as exc:  # noqa: BLE001 - reported as a lint issue
            issues.append({"rule": "invalid_params", "severity": "error", "item": g.id, "message": str(exc)})
            continue
        start = sp.out_start + g.offset
        end = start + g.duration
        reveals.append((start, g.id))
        if end > sp.out_end + 0.05:
            issues.append({"rule": "past_anchor_clip", "severity": "warn", "item": g.id, "overrun": ms(end - sp.out_end)})
        if tm and end > tm.duration + 0.01:
            issues.append({"rule": "past_timeline_end", "severity": "error", "item": g.id, "overrun": ms(end - tm.duration),
                           "fix": f"videocli gfx set {edl.name} {g.id} --duration {ms(max(0.5, tm.duration - start))}"})
        text = tp.text_of(params)
        for name, spec in t.params.items():
            if spec.max_chars and len(str(params.get(name, ""))) > spec.max_chars:
                issues.append({"rule": "text_too_long", "severity": "error", "item": g.id, "param": name, "chars": len(str(params[name])),
                               "max": spec.max_chars})
        if text and g.duration + 1e-3 < ms(reading_time(text)):  # equal after rounding is enough
            issues.append({"rule": "too_short_to_read", "severity": "warn", "item": g.id, "duration": g.duration,
                           "needed": ms(reading_time(text)), "fix": f"videocli gfx set {edl.name} {g.id} --duration {ms(reading_time(text))}"})
        if t.baker == "titles":
            lay = layout(t, params, brand, W, H)
            x0, y0, x1, y1 = lay.box
            if lay.overflow:
                issues.append({"rule": "text_overflows_box", "severity": "warn", "item": g.id})
            if t.placement.get("safe_area", True) and (x0 < margin - 1 or y0 < margin - 1 or x1 > W - margin + 1 or y1 > H - margin + 1):
                issues.append({"rule": "outside_safe_area", "severity": "warn", "item": g.id, "box": [x0, y0, x1, y1]})
            if edl.captions.mode == "burn" and y1 > H * 0.75 and (t.design or t.name) in ("lower-third", "callout", "quote"):
                issues.append({"rule": "overlaps_captions", "severity": "warn", "item": g.id})
            if use_layout and not ((x1 - x0) >= W * 0.95 and (y1 - y0) >= H * 0.95):
                from videocli.layout.place import occupied, overlap_report

                box = [x0 / W, y0 / H, (x1 - x0) / W, (y1 - y0) / H]
                for kind, rule in (("text", "covers_screen_text"), ("face", "covers_face"), ("object", "covers_object")):
                    hits = [c for c in overlap_report(box, occupied(edl, ws, start, start + g.duration, cache=layout_cache)) if c["kind"] == kind]
                    if hits:
                        issues.append({"rule": rule, "severity": "warn", "item": g.id,
                                       "covers": [{"text": c.get("text"), "box": c["box"], "covered": c["covered"]} for c in hits[:3]],
                                       "fix": f"videocli gfx set {edl.name} {g.id} --place auto"})
            if sample_frames and text and (t.design or t.name) in ("callout",):
                c = _sample_contrast(edl, ws, sp, start + g.duration / 2, (x0, y0, x1, y1), lay.text_color)
                if c is not None and c < 3.0:
                    issues.append({"rule": "low_contrast", "severity": "warn", "item": g.id, "ratio": round(c, 2)})
    if use_layout and edl.reframe is not None:
        issues += reframe_issues(edl, ws)
    reveals.sort()
    for (a, ia), (b, ib) in zip(reveals, reveals[1:], strict=False):
        if b - a < 0.3:
            issues.append({"rule": "simultaneous_reveals", "severity": "warn", "items": [ia, ib], "at": ms(a)})
    errors = sum(1 for i in issues if i["severity"] == "error")
    return {"name": edl.name, "passed": errors == 0, "errors": errors, "warnings": len(issues) - errors, "issues": issues}


def _sample_contrast(edl: EDL, ws: Any, sp: Any, out_t: float, box: tuple[int, int, int, int], text_color: str) -> float | None:
    from videocli.graphics.titles import contrast_ratio
    from videocli.media.frames import extract_many

    src = edl.sources[sp.source]
    if not src.has_video:
        return None
    t = sp.start + (min(out_t, sp.out_end - 0.05) - sp.out_start) * sp.speed
    t = max(sp.start, min(t, sp.end - 0.05))
    W, H = edl.resolution
    try:
        p = extract_many(Path(src.path), [round(t, 3)], ws.source_dir(sp.source) / "frames", width=W)[0]
    except Exception:  # noqa: BLE001 - contrast is advisory; a missing frame skips the rule
        return None
    import cv2

    img = cv2.imread(str(p))
    if img is None:
        return None
    img = cv2.resize(img, (W, H))
    x0, y0, x1, y1 = box
    region = img[max(0, y0):max(y0 + 1, y1), max(0, x0):max(x0 + 1, x1)]
    mean = region.reshape(-1, 3).mean(axis=0)[::-1]
    bg = "#{:02x}{:02x}{:02x}".format(*tuple(int(v) for v in mean))
    return contrast_ratio(text_color, bg)


def reframe_issues(edl: EDL, ws: Any) -> list[dict[str, Any]]:
    """A vertical crop that cuts through on-screen text, or a face panel that misses the face."""
    from videocli.core.cache import read_artifact
    from videocli.layout import geometry as geo
    from videocli.layout.place import median_face

    rf = edl.reframe
    assert rf is not None
    out: list[dict[str, Any]] = []
    W, H = edl.resolution
    if rf.mode == "stack" and rf.face:
        face = median_face(edl, ws, grow=1.0)
        if face is not None:
            cx, cy = face[0] + face[2] / 2, face[1] + face[3] / 2
            fx, fy, fw, fh = rf.face
            if not (fx <= cx <= fx + fw and fy <= cy <= fy + fh):
                out.append({"rule": "face_panel_misses_face", "severity": "warn", "face": [round(cx, 3), round(cy, 3)],
                            "fix": f"videocli edl reframe {edl.name} --aspect {rf.aspect} --mode stack --face auto"})
    for c in edl.tracks.main:
        doc = read_artifact(ws.source_dir(c.source) / "layout.json")
        if not doc:
            continue
        fw_, fh_ = doc.get("frame") or [16, 9]
        src_ar = fw_ / max(1, fh_)
        if rf.mode == "stack" and rf.face:
            ht = round(H * rf.split / 2) * 2
            out_ar = W / ht
        else:
            out_ar = W / H
        x = rf.crops.get(c.id, 0.5)
        win = geo.crop_window(src_ar, out_ar, x)
        cut = []
        for w in doc.get("windows", []):
            if w["end"] <= c.start or w["start"] >= c.end:
                continue
            for b in w.get("blocks", []):
                inside = geo.covered_share(b["box"], win)
                if 0.05 < inside < 0.9:
                    cut.append(b)
        if cut:
            union_x0 = min(b["box"][0] for b in cut)
            union_x1 = max(b["box"][0] + b["box"][2] for b in cut)
            ww = win[2]
            centre = (union_x0 + union_x1) / 2 if union_x1 - union_x0 <= ww else union_x0 + ww / 2
            best = min(1.0, max(0.0, (centre - ww / 2) / max(1e-6, 1 - ww)))
            out.append({"rule": "crop_cuts_text", "severity": "warn", "clip": c.id,
                        "text": [b["text"][:40] for b in cut[:3]], "x": round(x, 3), "suggested_x": round(best, 2),
                        "fix": f"videocli edl reframe {edl.name} --aspect {rf.aspect} --clip {c.id} --x {round(best, 2)}"})
    return out
