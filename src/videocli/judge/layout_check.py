"""``verify`` check ``layout``: read the rendered video back and prove the placement worked.

For every text graphic, a frame after its reveal is read with OCR:

* **readable** — the graphic's own text is found in the render (fail otherwise);
* **clear** — where it was found, it does not sit on on-screen text or a face from the layout
  data (warn, with ``gfx set … --place auto``).

Burned captions are sampled the same way: a caption found on top of screen text or a face is a
warning, fixed by ``captions-style … --param position=auto`` (or ``top``).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from videocli.judge.checks import CheckResult, Context

MAX_CAPTION_SAMPLES = 8
READABLE = 0.6


def _ocr_reader() -> Any | None:
    from videocli.layout import analyzer
    from videocli.setup.profiles import module_present

    if analyzer.READER_FACTORY is analyzer._default_reader and not module_present("rapidocr"):
        return None
    return analyzer.READER_FACTORY("rapidocr", "en")


def _frames(output: Path, times: list[float], width: int) -> dict[float, Any]:
    import cv2

    from videocli.media.frames import extract_one

    out: dict[float, Any] = {}
    with tempfile.TemporaryDirectory(prefix="videocli-verify-") as tmp:
        for i, t in enumerate(times):
            p = Path(tmp) / f"f{i}.png"
            extract_one(output, t, p, width=width)
            img = cv2.imread(str(p))
            if img is not None:
                out[t] = img[:, :, ::-1]
    return out


def _find(lines: list[dict[str, Any]], text: str, near: list[float] | None = None) -> dict[str, Any] | None:
    from videocli.layout import geometry as geo
    from videocli.layout.ocr import text_similarity

    best, score = None, 0.0
    for ln in lines:
        s = text_similarity(text, ln["text"])
        if near is not None and geo.intersection(ln["box"], geo.expand(near, 1.3)) <= 0:
            s *= 0.5
        if s > score:
            best, score = ln, s
    return best if best is not None and score >= READABLE else None


def minor_ui(item: dict[str, Any]) -> bool:
    """A small button or label (e.g. "Go to Top ↑"): a caption passing over it hides nothing that matters."""
    from videocli.layout import geometry as geo

    text = str(item.get("text") or "")
    return item["kind"] == "text" and geo.area(item["box"]) < 0.004 and len(text.split()) <= 3


def check_layout(ctx: Context) -> CheckResult:
    from videocli.graphics import templates as tp
    from videocli.layout.place import graphic_box, has_layout, occupied, overlap_report

    edl, ws = ctx.edl, getattr(ctx, "ws", None)
    if ws is None or ctx.probe.video is None:
        return CheckResult("layout", "skip", "no video stream")
    targets: list[dict[str, Any]] = []
    tm = edl.timemap()
    for g in edl.tracks.graphics:
        sp = tm.span(g.anchor)
        if sp is None:
            continue
        try:
            t = tp.get(ws, g.template)
            params = tp.resolve_params(t, g.params, tp.load_brand(ws))
        except Exception:  # noqa: BLE001 - gfx lint reports bad params
            continue
        text = str(params.get("title") or params.get("text") or "").strip()
        if t.baker != "titles" or len(text) < 2:
            continue
        start = sp.out_start + g.offset
        at = min(start + g.duration - 0.15, start + t.duration.in_ + 0.5)
        from videocli.graphics.lint import FULL_FRAME

        targets.append({"kind": "graphic", "item": g.id, "text": text, "at": round(at, 3), "start": start,
                        "end": start + g.duration, "box": graphic_box(ws, edl, g.template, g.params),
                        "full_frame": (t.design or t.name) in FULL_FRAME})
    if edl.captions.mode == "burn" and ctx.transcripts:
        from videocli.graphics.captions_ass import group_words
        from videocli.render.captions import map_words

        words = map_words(edl, ctx.transcripts)
        p = edl.captions.params
        cues = group_words(words, edl.captions.preset, int(p.get("max_chars", edl.captions.max_chars)), int(p.get("lines", 2)))
        step = max(1, len(cues) // MAX_CAPTION_SAMPLES)
        for c in cues[::step][:MAX_CAPTION_SAMPLES]:
            text = " ".join(w["word"] for w in c)
            targets.append({"kind": "caption", "text": text, "at": round((c[0]["start"] + c[-1]["end"]) / 2, 3),
                            "start": c[0]["start"], "end": c[-1]["end"], "box": None})
    if not targets:
        return CheckResult("layout", "skip", "no text graphics or burned captions to check")
    reader = _ocr_reader()
    if reader is None:
        return CheckResult("layout", "skip", "needs the OCR models: videocli setup --with perception")
    with_layout = has_layout(edl, ws)
    frames = _frames(ctx.output, sorted({x["at"] for x in targets}), int(ctx.probe.video.display_width))
    items: list[dict[str, Any]] = []
    unreadable = covered = caption_seen = boxed_over = 0
    boxed = str(edl.captions.params.get("box", "0")).lower() in ("1", "true", "yes", "on")
    for x in targets:
        img = frames.get(x["at"])
        if img is None:
            continue
        lines = reader.read(img)
        hit = _find(lines, x["text"], x["box"])
        if x["kind"] == "caption":
            if hit is None:
                continue  # captions may not be burned into this file
            caption_seen += 1
        elif hit is None:
            unreadable += 1
            items.append({"at": x["at"], "item": x["item"], "problem": "not_readable", "text": x["text"],
                          "fix": f"videocli gfx set {edl.name} {x['item']} --param scale=1.2"})
            continue
        if not with_layout or x.get("full_frame"):
            continue  # title and end cards cover the frame on purpose
        occ = [o for o in occupied(edl, ws, x["at"], x["at"] + 0.01) if o["kind"] in ("text", "face")]
        if x["kind"] == "caption":
            occ = [o for o in occ if not minor_ui(o)]
        region = hit["box"] if x["kind"] == "caption" else (x["box"] or hit["box"])
        hits = overlap_report(region, occ)
        if hits and x["kind"] == "caption" and boxed:
            boxed_over += 1  # a boxed caption stays readable over screen text: accepted
            continue
        if hits:
            covered += 1
            if x["kind"] == "graphic":
                fix = f"videocli gfx set {edl.name} {x['item']} --place auto"
            elif str(edl.captions.params.get("position", "auto")) == "auto":
                fix = f"videocli captions-style {edl.name} --param box=1   # no clear band on this screen: keep it readable"
            else:
                fix = f"videocli captions-style {edl.name} --param position=auto"
            items.append({"at": x["at"], "item": x.get("item", "caption"), "problem": f"{x['kind']}_covers_screen",
                          "text": x["text"][:60], "covers": [h.get("text") or h["kind"] for h in hits[:3]], "fix": fix})
    n_g = sum(1 for x in targets if x["kind"] == "graphic")
    m = {"graphics": n_g, "unreadable": unreadable, "covering": covered, "captions_checked": caption_seen,
         "captions_boxed_over_text": boxed_over, "layout_data": with_layout}
    if unreadable:
        return CheckResult("layout", "fail", f"{unreadable} graphic(s) not readable in the render.", items=items, measured=m)
    if covered:
        return CheckResult("layout", "warn", f"{covered} graphic(s)/caption(s) sit on screen text or a face.", items=items, measured=m)
    note = "" if with_layout else " (no layout data: overlap not checked; run analyze --only layout)"
    return CheckResult("layout", "pass", f"{n_g} graphic(s) readable, {caption_seen} caption(s) checked, nothing covered{note}", measured=m)
