"""Analyzer ``layout``: what is where on screen, per stable window.

1. Decode a small grey copy at ``every`` seconds and split the timeline into windows where the
   picture holds still (faces are masked so a talking webcam bubble does not split windows).
2. For each window take one keyframe at full analysis size and record text boxes (OCR), faces,
   optional objects (Florence-2 backend) and the free rectangles left over.
3. Merge neighbouring windows whose text did not change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from videocli.analyze.base import Availability, RunContext, register
from videocli.core import progress
from videocli.core.cache import write_artifact
from videocli.core.errors import DependencyError
from videocli.core.source import Source
from videocli.core.timeutil import ms
from videocli.layout import geometry as geo

CHANGE_PIXEL = 25  # grey levels
CHANGE_SHARE = 0.01  # share of unmasked pixels that must change to start a new window

#: Factory for the face detector (tests replace it): gray frame -> [[x, y, w, h] pixels]
_CASCADE: Any = None


def detect_faces(gray: np.ndarray, min_frac: float = 0.04) -> list[list[int]]:
    import cv2

    global _CASCADE
    if _CASCADE is None:
        _CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    H, W = gray.shape[:2]
    m = max(16, int(min(W, H) * min_frac))
    return [list(map(int, f)) for f in _CASCADE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(m, m))]


def split_windows(times: np.ndarray, grays: np.ndarray, faces: list[list[list[int]]]) -> list[tuple[float, float, int]]:
    """(start, end, keyframe index) for runs of frames that do not change outside faces."""
    n = len(times)
    if n == 0:
        return []
    step = float(times[1] - times[0]) if n > 1 else 1.0
    wins: list[tuple[float, float, int]] = []
    start_i = 0
    ref = grays[0].astype(np.int16)
    for i in range(1, n):
        g = grays[i].astype(np.int16)
        mask = np.ones(g.shape, dtype=bool)
        for fs in (faces[i], faces[i - 1]):
            for x, y, w, h in fs:
                gx, gy = int(w * 0.6), int(h * 0.6)
                mask[max(0, y - gy):y + h + gy, max(0, x - gx):x + w + gx] = False
        changed = (np.abs(g - ref) > CHANGE_PIXEL) & mask
        share = changed.sum() / max(1, mask.sum())
        if share > CHANGE_SHARE:
            wins.append((float(times[start_i]), float(times[i]), (start_i + i - 1) // 2))
            start_i = i
            ref = g
    wins.append((float(times[start_i]), float(times[-1]) + step, (start_i + n - 1) // 2))
    return wins


def text_blocks(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group text lines into blocks (a list, a paragraph, a toolbar) by proximity."""
    blocks: list[dict[str, Any]] = []
    for ln in sorted(lines, key=lambda d: (d["box"][1], d["box"][0])):
        b = ln["box"]
        hit = None
        for blk in blocks:
            bb = blk["box"]
            gap = b[1] - (bb[1] + bb[3])
            overlap_x = min(b[0] + b[2], bb[0] + bb[2]) - max(b[0], bb[0])
            if -b[3] < gap < 1.6 * b[3] and (overlap_x > 0 or abs(b[0] - bb[0]) < 0.05):
                hit = blk
                break
        if hit is None:
            blocks.append({"box": list(b), "lines": 1, "text": ln["text"]})
        else:
            bb = hit["box"]
            x0, y0 = min(bb[0], b[0]), min(bb[1], b[1])
            x1, y1 = max(bb[0] + bb[2], b[0] + b[2]), max(bb[1] + bb[3], b[1] + b[3])
            hit["box"] = geo.clamp_box([x0, y0, x1 - x0, y1 - y0])
            hit["lines"] += 1
            hit["text"] = (hit["text"] + " " + ln["text"])[:240]
    return blocks


def _same_text(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    ta, tb = {d["text"] for d in a}, {d["text"] for d in b}
    if not ta and not tb:
        return True
    return len(ta & tb) / max(1, len(ta | tb)) >= 0.85


class LayoutAnalyzer:
    name = "layout"
    version = 1
    requires: tuple[str, ...] = ("probe",)
    schema = "videocli.layout/v1"
    needs_audio = False
    needs_video = True

    def params(self, ctx: RunContext) -> dict[str, Any]:
        s = ctx.settings
        return {
            "every": float(ctx.options.get("layout_every") or s.layout__every),
            "width": int(s.layout__width),
            "backend": ctx.options.get("layout_backend") or s.layout__backend,
            "ocr_lang": ctx.options.get("ocr_lang") or s.layout__ocr_lang,
        }

    def available(self, ctx: RunContext) -> Availability:
        p = self.params(ctx)
        from videocli.setup.profiles import module_present

        if p["backend"] == "florence":
            from videocli.layout import florence

            return florence.availability()
        if p["backend"] not in ("rapidocr", "florence"):
            from videocli.core.errors import UsageError

            return Availability(False, UsageError("BAD_ARGUMENT", f"Unknown layout backend '{p['backend']}'.", hint="Use rapidocr or florence."))
        if READER_FACTORY is _default_reader and not module_present("rapidocr"):
            return Availability(False, DependencyError(
                "FEATURE_NOT_INSTALLED", "Screen text (layout) needs the OCR models of the perception group.",
                hint="Run: videocli setup --with perception", details={"group": "perception"}))
        return Availability.yes()

    def run(self, src: Source, params: dict[str, Any], ctx: RunContext) -> Path:
        import cv2

        from videocli.analyze.vision import decode_frames
        from videocli.media.frames import extract_many

        pr = ctx.probe
        assert pr is not None and pr.video is not None
        vw = pr.video.display_width
        times, grays = decode_frames(src.path, 1.0 / params["every"], 320)
        small_faces = [detect_faces(g, 0.05) for g in grays]
        wins = split_windows(times, grays, small_faces)
        width = min(int(params["width"]), int(vw) - int(vw) % 2)
        reader = READER_FACTORY(params["backend"], params["ocr_lang"])
        cache = ctx.ws.source_dir(src.id) / "frames"
        key_times = [round(float(times[k]), 3) for _a, _b, k in wins]
        paths = extract_many(src.path, key_times, cache, width=width, fmt="png", jobs=int(getattr(ctx.settings, "jobs", 4)))
        out: list[dict[str, Any]] = []
        for i, ((a, b, _k), t, p) in enumerate(zip(wins, key_times, paths, strict=True)):
            bgr = cv2.imread(str(p))
            rgb = bgr[:, :, ::-1]
            H, W = rgb.shape[:2]
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            got = reader.read(rgb)
            text = [d for d in got if d.get("kind", "text") == "text"]
            objects = [d for d in got if d.get("kind") == "object"]
            faces = [{"box": geo.clamp_box([x / W, y / H, w / W, h / H])} for x, y, w, h in detect_faces(gray)]
            boxes = [d["box"] for d in text] + [geo.expand(f["box"], 1.6) for f in faces] + [d["box"] for d in objects]
            busy = geo.busy_grid(geo.edge_density(gray), boxes)
            win = {
                "start": ms(a), "end": ms(min(b, float(pr.duration))), "keyframe": t,
                "text": sorted(text, key=lambda d: (d["box"][1], d["box"][0])),
                "blocks": text_blocks(text), "faces": faces, "objects": objects,
                "free": [{"box": r} for r in geo.free_rects(busy)], "busy": round(float(busy.mean()), 3),
            }
            if out and _same_text(out[-1]["text"], win["text"]) and len(out[-1]["faces"]) == len(win["faces"]):
                out[-1]["end"] = win["end"]  # nothing readable changed: one window
            else:
                out.append(win)
            progress.update((i + 1) / max(1, len(wins)), f"window {i + 1}/{len(wins)}")
        for n, w in enumerate(out, 1):
            w["id"] = f"L{n:03d}"
        provenance = getattr(reader, "provenance", f"{params['backend']}:{params['ocr_lang']}")
        body = {"source_id": src.id, "provenance": provenance, "frame": [W if out else width, H if out else 0],
                "every": params["every"], "windows": out}
        dest = ctx.artifact_path(src, self.name)
        clean = {k: v for k, v in params.items() if not k.startswith("_")}
        write_artifact(dest, schema=self.schema, key=params["_key"], params=clean, body=body)
        return dest


def _default_reader(backend: str, lang: str):
    from videocli.layout.ocr import make_reader

    return make_reader(backend, lang)


#: Factory for the text/object reader; tests replace it.
READER_FACTORY = _default_reader

register(LayoutAnalyzer())
