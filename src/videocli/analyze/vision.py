"""Visual analyzers (phase 5): motion, faces, shots, embed, slides.

All work on a low-resolution decode of the source, made once per analyzer through ffmpeg.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from videocli.analyze.base import Availability, RunContext, load_artifact, register
from videocli.core.cache import write_artifact
from videocli.core.source import Source
from videocli.core.timeutil import ms
from videocli.media import ffmpeg


def decode_frames(path: Path, fps: float, width: int, *, gray: bool = True, start: float = 0.0,
                  duration: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Frames at ``fps`` scaled to ``width`` (even height). Returns (times, frames)."""
    from videocli.media.probe import probe_path

    pr = probe_path(path)
    v = pr.video
    assert v is not None
    h = int(round(width * v.display_height / max(1, v.display_width) / 2)) * 2
    pix = "gray" if gray else "rgb24"
    args = ["-ss", f"{start:.3f}"]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    args += ["-i", ffmpeg.fpath(path), "-map", "0:v:0", "-vf", f"fps={fps},scale={width}:{h}:flags=area,format={pix}", "-f", "rawvideo", "-"]
    res = ffmpeg.run(args, log_name="decode-frames", capture=True)
    ch = 1 if gray else 3
    n = len(res.stdout) // (width * h * ch)
    arr = np.frombuffer(res.stdout[: n * width * h * ch], dtype=np.uint8).reshape(n, h, width, *(() if gray else (3,)))
    times = start + np.arange(n) / fps
    return times, arr


def _scenes(ctx: RunContext, src: Source) -> list[dict[str, Any]]:
    doc = load_artifact(ctx.ws, src, "scenes")
    return doc["scenes"] if doc else []


def _shot_of(scenes: list[dict[str, Any]], t: float) -> str | None:
    for s in scenes:
        if s["start"] <= t < s["end"]:
            return s["id"]
    return scenes[-1]["id"] if scenes else None


class _Base:
    version = 1
    needs_audio = False
    needs_video = True

    def params(self, ctx: RunContext) -> dict[str, Any]:
        return {}

    def available(self, ctx: RunContext) -> Availability:
        return Availability.yes()

    def _write(self, ctx: RunContext, src: Source, params: dict[str, Any], body: dict[str, Any]) -> Path:
        out = ctx.artifact_path(src, self.name)  # type: ignore[attr-defined]
        clean = {k: v for k, v in params.items() if not k.startswith("_")}
        write_artifact(out, schema=self.schema, key=params["_key"], params=clean, body={"source_id": src.id, **body})  # type: ignore[attr-defined]
        return out


# --------------------------------------------------------------------------- motion


def classify_camera(dx: np.ndarray, dy: np.ndarray, zoom: np.ndarray, resid: np.ndarray) -> tuple[str, float]:
    """Camera movement class and a 0-1 stability score from per-step global motion."""
    if len(dx) == 0:
        return "static", 1.0
    mdx, mdy, mz = float(np.mean(dx)), float(np.mean(dy)), float(np.mean(zoom))
    jitter = float(np.std(dx) + np.std(dy))
    mag = float(np.mean(np.hypot(dx, dy)))
    stability = float(np.clip(1 - jitter / 2.0, 0, 1))
    if mag < 0.15 and abs(mz) < 0.002 and jitter < 0.3:
        return "static", stability
    if abs(mz) > 0.004 and abs(mz) * 200 > max(abs(mdx), abs(mdy)):
        return "zoom", stability
    if jitter > 0.8 and max(abs(mdx), abs(mdy)) < jitter:
        return "handheld", stability
    if abs(mdx) >= abs(mdy) and abs(mdx) > 0.3:
        return "pan" if float(np.mean(resid)) < 1.0 else "tracking", stability
    if abs(mdy) > 0.3:
        return "tilt", stability
    return ("handheld" if jitter > 0.4 else "static"), stability


class MotionAnalyzer(_Base):
    name = "motion"
    requires: tuple[str, ...] = ("scenes",)
    schema = "videocli.motion/v1"

    def run(self, src: Source, params: dict[str, Any], ctx: RunContext) -> Path:
        import cv2

        fps = 5.0
        times, frames = decode_frames(src.path, fps, 160)
        scenes = _scenes(ctx, src)
        dxs, dys, zs, res, energy = [], [], [], [], []
        h, w = frames.shape[1:3] if len(frames) else (0, 0)
        ys, xs = np.mgrid[0:h, 0:w] if h else (np.zeros((0, 0)), np.zeros((0, 0)))
        rx, ry = (xs - w / 2), (ys - h / 2)
        for a, b in zip(frames, frames[1:]):
            flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            fx, fy = flow[..., 0], flow[..., 1]
            gdx, gdy = float(np.median(fx)), float(np.median(fy))
            denom = float((rx * rx + ry * ry).mean()) or 1.0
            z = float(((fx - gdx) * rx + (fy - gdy) * ry).mean() / denom)
            r = np.hypot(fx - gdx - z * rx, fy - gdy - z * ry)
            dxs.append(gdx)
            dys.append(gdy)
            zs.append(z)
            res.append(float(r.mean()))
            energy.append(float(np.hypot(fx, fy).mean()))
        dxs, dys, zs, res, energy = map(np.asarray, (dxs, dys, zs, res, energy))
        mid_t = (times[:-1] + times[1:]) / 2 if len(times) > 1 else np.zeros(0)
        shots = []
        for s in scenes:
            m = (mid_t >= s["start"]) & (mid_t < s["end"])
            cls, stab = classify_camera(dxs[m], dys[m], zs[m], res[m])
            e = energy[m]
            peak = float(mid_t[m][int(np.argmax(e))]) if m.any() else None
            shots.append({"id": s["id"], "start": s["start"], "end": s["end"], "camera": cls, "stability": round(stab, 3),
                          "motion": round(float(e.mean()) if m.any() else 0.0, 3), "subject_motion": round(float(res[m].mean()) if m.any() else 0.0, 3),
                          "action_peak": ms(peak) if peak is not None else None})
        # action peaks across the file: local maxima of motion energy well above the median
        peaks = []
        if len(energy) > 2:
            thr = float(np.median(energy) + 2 * np.std(energy))
            for i in range(1, len(energy) - 1):
                if energy[i] > thr and energy[i] >= energy[i - 1] and energy[i] >= energy[i + 1]:
                    peaks.append(ms(float(mid_t[i])))
        return self._write(ctx, src, params, {"fps": fps, "curve": [round(float(x), 3) for x in energy], "shots": shots, "action_peaks": peaks})


# --------------------------------------------------------------------------- faces


def face_signature(gray_face: np.ndarray) -> np.ndarray:
    """A small appearance descriptor (LBP histogram over a 4x4 grid) for local clustering."""
    import cv2

    f = cv2.resize(gray_face, (64, 64)).astype(np.int16)
    c = f[1:-1, 1:-1]
    code = np.zeros_like(c, dtype=np.uint8)
    for k, (dy, dx) in enumerate([(-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1)]):
        code |= ((f[1 + dy:63 + dy, 1 + dx:63 + dx] >= c).astype(np.uint8) << k)
    hists = []
    for gy in range(4):
        for gx in range(4):
            cell = code[gy * 15:(gy + 1) * 15, gx * 15:(gx + 1) * 15]
            hists.append(np.bincount(cell.ravel(), minlength=256)[:256])
    v = np.concatenate(hists).astype(np.float64)
    return v / (np.linalg.norm(v) + 1e-9)


def cluster(sigs: list[np.ndarray], threshold: float = 0.35) -> list[int]:
    """Greedy agglomerative clustering on cosine distance; returns a cluster index per signature."""
    centroids: list[np.ndarray] = []
    counts: list[int] = []
    labels = []
    for s in sigs:
        best, bd = -1, 1e9
        for i, c in enumerate(centroids):
            d = 1 - float(np.dot(s, c / (np.linalg.norm(c) + 1e-9)))
            if d < bd:
                best, bd = i, d
        if best >= 0 and bd < threshold:
            centroids[best] = centroids[best] + s
            counts[best] += 1
            labels.append(best)
        else:
            centroids.append(s.copy())
            counts.append(1)
            labels.append(len(centroids) - 1)
    return labels


class FacesAnalyzer(_Base):
    name = "faces"
    requires: tuple[str, ...] = ("scenes",)
    schema = "videocli.faces/v1"

    def params(self, ctx: RunContext) -> dict[str, Any]:
        return {"fps": 2.0, "detector": "haar"}

    def run(self, src: Source, params: dict[str, Any], ctx: RunContext) -> Path:
        import cv2

        times, frames = decode_frames(src.path, params["fps"], 480)
        det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        eyes = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml")
        scenes = _scenes(ctx, src)
        faces, sigs = [], []
        for t, f in zip(times, frames):
            H, W = f.shape
            for x, y, w, h in det.detectMultiScale(f, scaleFactor=1.1, minNeighbors=5, minSize=(max(24, W // 25), max(24, W // 25))):
                crop = f[y:y + h, x:x + w]
                e = eyes.detectMultiScale(crop[: h // 2 + h // 6], scaleFactor=1.1, minNeighbors=4)
                faces.append({"t": ms(float(t)), "x": round((x + w / 2) / W, 4), "y": round((y + h / 2) / H, 4), "w": round(w / W, 4), "h": round(h / H, 4),
                              "area": round(w * h / (W * H), 4), "eyes_open": bool(len(e) >= 1), "shot": _shot_of(scenes, float(t))})
                sigs.append(face_signature(crop))
        labels = cluster(sigs)
        order: dict[int, str] = {}
        for f, lab in zip(faces, labels):
            order.setdefault(lab, f"p{len(order) + 1}")
            f["person"] = order[lab]
        people = {}
        for f in faces:
            p = people.setdefault(f["person"], {"id": f["person"], "detections": 0, "first": f["t"], "shots": set()})
            p["detections"] += 1
            if f["shot"]:
                p["shots"].add(f["shot"])
        people_list = [{**p, "shots": sorted(p["shots"])} for p in people.values()]
        return self._write(ctx, src, params, {"provenance": "local:opencv-haar", "faces": faces, "people": people_list})


# --------------------------------------------------------------------------- shots


def shot_metrics(frames: np.ndarray) -> dict[str, float]:
    import cv2

    if not len(frames):
        return {"blur": 0.0, "brightness": 0.0, "black_ratio": 1.0}
    blur = float(np.mean([cv2.Laplacian(f, cv2.CV_64F).var() for f in frames]))
    bright = float(frames.mean() / 255)
    black = float((frames < 20).mean())
    return {"blur": round(blur, 1), "brightness": round(bright, 3), "black_ratio": round(black, 3)}


class ShotsAnalyzer(_Base):
    name = "shots"
    requires: tuple[str, ...] = ("scenes", "motion")
    schema = "videocli.shots/v1"

    def run(self, src: Source, params: dict[str, Any], ctx: RunContext) -> Path:
        times, frames = decode_frames(src.path, 2.0, 320)
        motion = {s["id"]: s for s in (load_artifact(ctx.ws, src, "motion") or {}).get("shots", [])}
        faces = load_artifact(ctx.ws, src, "faces", required=False) or {}
        shots = []
        for s in _scenes(ctx, src):
            m = (times >= s["start"]) & (times < s["end"])
            met = shot_metrics(frames[m])
            mo = motion.get(s["id"], {})
            fs = [f for f in faces.get("faces", []) if f.get("shot") == s["id"]]
            people: dict[str, list[dict[str, Any]]] = {}
            for f in fs:
                people.setdefault(f["person"], []).append(f)
            face_rec = [{"person": p, "area": round(float(np.median([x["area"] for x in v])), 4),
                         "x": round(float(np.median([x["x"] for x in v])), 3), "y": round(float(np.median([x["y"] for x in v])), 3)}
                        for p, v in people.items()]
            size = None
            if face_rec:
                a = max(f["area"] for f in face_rec)
                size = "ECU" if a > 0.25 else "CU" if a > 0.1 else "MCU" if a > 0.04 else "MS" if a > 0.012 else "WS"
            shots.append({
                "id": s["id"], "start": s["start"], "end": s["end"], "duration": ms(s["end"] - s["start"]),
                "size": size, "angle": None, "type": ("single" if len(face_rec) == 1 else "two-shot" if len(face_rec) == 2 else
                                                      "group" if len(face_rec) > 2 else None),
                "motion": mo.get("camera"), "location": None, "subject": "human" if face_rec else None,
                "faces": face_rec,
                "metrics": {"motion": mo.get("motion"), "stability": mo.get("stability"), "action_peak": mo.get("action_peak"), **met},
                "description": None, "usable": None if met["black_ratio"] < 0.95 else False,
                "provenance": {"attributes": "local:metrics+faces" if face_rec else "local:metrics", "description": None},
            })
        return self._write(ctx, src, params, {"shots": shots})


# --------------------------------------------------------------------------- embed


def visual_features(rgb: np.ndarray) -> np.ndarray:
    """A compact appearance vector: HSV histogram plus a coarse 8x8 luma layout."""
    import cv2

    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256]).ravel()
    hist = hist / (hist.sum() + 1e-9)
    lay = cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), (8, 8), interpolation=cv2.INTER_AREA).ravel().astype(np.float64) / 255
    lay = lay - lay.mean()
    v = np.concatenate([hist * 4, lay / (np.linalg.norm(lay) + 1e-9)])
    return v / (np.linalg.norm(v) + 1e-9)


class EmbedAnalyzer(_Base):
    name = "embed"
    requires: tuple[str, ...] = ("scenes",)
    schema = "videocli.embed/v1"

    def run(self, src: Source, params: dict[str, Any], ctx: RunContext) -> Path:
        times, frames = decode_frames(src.path, 1.0, 160, gray=False)
        vecs, index = [], []
        for s in _scenes(ctx, src):
            m = np.where((times >= s["start"]) & (times < s["end"]))[0]
            if not len(m):
                m = np.array([int(np.argmin(np.abs(times - (s["start"] + s["end"]) / 2)))]) if len(times) else m
            picks = m[np.linspace(0, len(m) - 1, num=min(len(m), 1 + int((s["end"] - s["start"]) // 10))).astype(int)] if len(m) else []
            for k in picks:
                vecs.append(visual_features(frames[k]))
                index.append({"shot": s["id"], "t": ms(float(times[k]))})
        npy = ctx.ws.source_dir(src.id) / "embed.npy"
        np.save(npy, np.asarray(vecs, dtype=np.float32))
        sims = []
        sc = _scenes(ctx, src)
        by_shot: dict[str, list[int]] = {}
        for i, r in enumerate(index):
            by_shot.setdefault(r["shot"], []).append(i)
        arr = np.asarray(vecs)
        for a, b in zip(sc, sc[1:]):
            ia, ib = by_shot.get(a["id"]), by_shot.get(b["id"])
            if ia and ib:
                sims.append({"a": a["id"], "b": b["id"], "similarity": round(float(np.max(arr[ia] @ arr[ib].T)), 4)})
        return self._write(ctx, src, params, {"model": "local:hsv-layout-v1", "file": "embed.npy", "vectors": index, "neighbours": sims})


# --------------------------------------------------------------------------- slides


class SlidesAnalyzer(_Base):
    name = "slides"
    requires: tuple[str, ...] = ("probe",)
    schema = "videocli.slides/v1"

    def params(self, ctx: RunContext) -> dict[str, Any]:
        return {"hold": float(ctx.options.get("slide_hold") or 2.0), "threshold": 0.12, "crop": ctx.options.get("slide_crop")}

    def run(self, src: Source, params: dict[str, Any], ctx: RunContext) -> Path:
        times, frames = decode_frames(src.path, 1.0, 160)
        hold, thr = params["hold"], params["threshold"]
        slides = []
        cur_start, ref = 0.0, None
        for t, f in zip(times, frames):
            g = f.astype(np.float32) / 255
            if ref is None:
                ref = g
                continue
            if float(np.mean(np.abs(g - ref))) > thr:
                if t - cur_start >= hold:
                    slides.append((cur_start, float(t)))
                cur_start, ref = float(t), g
            else:
                ref = 0.8 * ref + 0.2 * g
        dur = float(ctx.probe.duration) if ctx.probe else (float(times[-1]) + 1 if len(times) else 0)
        if dur - cur_start >= min(hold, dur):
            slides.append((cur_start, dur))
        tr = load_artifact(ctx.ws, src, "transcript", required=False) or {}
        out = []
        for i, (a, b) in enumerate(slides, 1):
            text = " ".join(s["text"] for s in tr.get("segments", []) if s["start"] < b and s["end"] > a)
            out.append({"id": f"sl{i:03d}", "start": ms(a), "end": ms(b), "text": text[:2000]})
        return self._write(ctx, src, params, {"slides": out})


for _a in (MotionAnalyzer(), FacesAnalyzer(), ShotsAnalyzer(), EmbedAnalyzer(), SlidesAnalyzer()):
    register(_a)
