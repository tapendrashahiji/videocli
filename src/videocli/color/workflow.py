"""Grade workflows used by the CLI: sampling pixels, scopes per clip, auto balance/match, looks, previews."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from videocli.color import scopes as sc
from videocli.color.match import balance_ops, look_from_reference, match_cdl
from videocli.color.ops import apply_stack, lut_ops
from videocli.color.render import stack_for
from videocli.core.workspace import Workspace
from videocli.edl.model import EDL, Clip, Grade
from videocli.media.frames import extract_many

SAMPLE_W = 320


def clip_times(c: Clip, n: int = 3) -> list[float]:
    d = c.end - c.start
    return [round(c.start + d * (i + 0.5) / n, 3) for i in range(n)]


def clip_pixels(ws: Workspace, edl: EDL, c: Clip, n: int = 3) -> tuple[np.ndarray, list[Path]]:
    src = edl.sources[c.source]
    paths = extract_many(Path(src.path), clip_times(c, n), ws.source_dir(c.source) / "frames", width=SAMPLE_W)
    px = [sc.load_rgb(p) for p in paths]
    rgb = np.concatenate([x for x in px if len(x)]) if px else np.zeros((0, 3))
    if len(rgb) > 60000:
        rgb = rgb[:: len(rgb) // 60000 + 1]
    return rgb, paths


def image_pixels(path: Path) -> np.ndarray:
    import cv2

    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        from videocli.core.errors import InputError

        raise InputError("BAD_ARGUMENT", f"Cannot read image {path}.")
    img = cv2.resize(img, (SAMPLE_W, max(1, int(SAMPLE_W * img.shape[0] / img.shape[1]))), interpolation=cv2.INTER_AREA)
    return img[:, :, ::-1].reshape(-1, 3).astype(np.float64) / 255.0


def media_pixels(ws: Workspace, path: Path) -> np.ndarray:
    if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"):
        return image_pixels(path)
    from videocli.core.source import lookup
    from videocli.media import probe as probemod

    src = lookup(ws, str(path))
    pr = probemod.load(ws, src)
    times = [round(pr.duration * (i + 0.5) / 6, 3) for i in range(6)]
    frames = extract_many(src.path, times, ws.source_dir(src.id) / "frames", width=SAMPLE_W)
    return np.concatenate([sc.load_rgb(p) for p in frames])


def scopes_for_edl(ws: Workspace, edl: EDL, *, graded: bool = True, clip_id: str | None = None) -> list[dict[str, Any]]:
    out = []
    for c in edl.tracks.main:
        if clip_id and c.id != clip_id:
            continue
        rgb, _ = clip_pixels(ws, edl, c)
        ops = stack_for(edl, c.grade) if graded else []
        m = sc.measure(rgb, lut_ops(ops))
        out.append({"clip": c.id, "source": c.source, "grade": c.grade, **m})
    return sc.finish(out)


def shot_difference(shots: list[dict[str, Any]]) -> float:
    ds = [s["delta_to_previous"] for s in shots if s.get("delta_to_previous") is not None]
    return round(float(np.mean(ds)), 2) if ds else 0.0


def auto(ws: Workspace, edl: EDL, *, balance: bool, match: bool, reference: str | None, strength: float,
         reference_image: Path | None = None, force: bool = False) -> dict[str, Any]:
    """Group clips by source (one shoot, one correction) and write ``auto_<source>`` grades."""
    groups: dict[str, list[Clip]] = {}
    for c in edl.tracks.main:
        if c.locked or (c.grade and not c.grade.startswith("auto_") and not force):
            continue
        groups.setdefault(c.source, []).append(c)
    before = scopes_for_edl(ws, edl)
    pix = {sid: np.concatenate([clip_pixels(ws, edl, c)[0] for c in cl]) for sid, cl in groups.items()}
    stacks: dict[str, list[dict[str, Any]]] = {}
    for sid, rgb in pix.items():
        stacks[sid] = balance_ops(rgb) if balance else []
    ref_rgb = None
    if match:
        if reference_image is not None:
            ref_rgb = media_pixels(ws, reference_image)
        else:
            ref_clip = edl.clip(reference) if reference else (edl.tracks.main[0] if edl.tracks.main else None)
            if ref_clip is None:
                from videocli.core.errors import InputError

                raise InputError("ITEM_NOT_FOUND", f"No clip {reference!r}.")
            r, _ = clip_pixels(ws, edl, ref_clip)
            ref_rgb = np.clip(apply_stack(r, stacks.get(ref_clip.source, [])), 0, 1)
    clamped = []
    for sid, rgb in pix.items():
        ops = stacks[sid]
        if ref_rgb is not None:
            rc = edl.clip(reference) if reference else None
            ref_src = rc.source if rc is not None else (edl.tracks.main[0].source if not reference_image else None)
            if sid != ref_src:
                cur = np.clip(apply_stack(rgb, ops), 0, 1)
                cdl = match_cdl(cur, ref_rgb, strength)
                if cdl.pop("_clamped", False):
                    clamped.append(sid)
                ops = [*ops, cdl]
        key = f"auto_{sid[:6]}"
        edl.grades[key] = Grade(ops=ops)
        for c in groups[sid]:
            c.grade = key
    after = scopes_for_edl(ws, edl)
    return {
        "groups": {sid: {"grade": f"auto_{sid[:6]}", "clips": [c.id for c in cl], "ops": edl.grades[f"auto_{sid[:6]}"].ops}
                   for sid, cl in groups.items()},
        "shot_difference": {"before": shot_difference(before), "after": shot_difference(after)},
        "skin_clamped": clamped,
    }


def look_from(ws: Workspace, edl: EDL, ref: Path, strength: float) -> list[dict[str, Any]]:
    if ref.suffix.lower() == ".cube":
        return [{"op": "lut", "path": str(ref.resolve()), "strength": strength}]
    ref_rgb = media_pixels(ws, ref)
    base = np.concatenate([clip_pixels(ws, edl, c, 2)[0] for c in edl.tracks.main[:12]])
    return look_from_reference(ref_rgb, base, strength)


def preview_split(ws: Workspace, edl: EDL, out_dir: Path, clip_id: str | None = None) -> list[dict[str, Any]]:
    """Before/after stills side by side, one per clip, with the measurements."""
    import cv2

    out_dir.mkdir(parents=True, exist_ok=True)
    res = []
    for c in edl.tracks.main:
        if clip_id and c.id != clip_id:
            continue
        src = edl.sources[c.source]
        t = clip_times(c, 1)
        p = extract_many(Path(src.path), t, ws.source_dir(c.source) / "frames", width=640)[0]
        raw = cv2.imread(str(p))
        assert raw is not None
        img = raw[:, :, ::-1].astype(np.float64) / 255.0
        h, w = img.shape[:2]
        ops = lut_ops(stack_for(edl, c.grade))
        graded = np.clip(apply_stack(img.reshape(-1, 3), ops), 0, 1).reshape(h, w, 3) if ops else img
        both = np.concatenate([img, np.full((h, 6, 3), 0.1), graded], axis=1)
        path = out_dir / f"{edl.name}-{c.id}-split.jpg"
        cv2.imwrite(str(path), (both[:, :, ::-1] * 255).round().astype(np.uint8))
        res.append({"clip": c.id, "path": str(path), "before": sc.measure(img.reshape(-1, 3)), "after": sc.measure(graded.reshape(-1, 3))})
    for r in res:
        r["before"].pop("_lab", None)
        r["after"].pop("_lab", None)
    return res


def candidates(ws: Workspace, edl: EDL, n: int, out_dir: Path, clip_id: str | None) -> dict[str, Any]:
    """N strengths of automatic balance+match, each measured, on one sheet, ranked by shot-to-shot difference."""
    import cv2

    out = []
    tiles = []
    strengths = [round(x, 2) for x in np.linspace(0.25, 1.0, n)]
    for s in strengths:
        e = edl.model_copy(deep=True)
        info = auto(ws, e, balance=True, match=True, reference=None, strength=s, force=True)
        shots = scopes_for_edl(ws, e)
        first = next((c for c in e.tracks.main if not clip_id or c.id == clip_id), e.tracks.main[0])
        p = extract_many(Path(e.sources[first.source].path), clip_times(first, 1), ws.source_dir(first.source) / "frames", width=320)[0]
        raw = cv2.imread(str(p))
        assert raw is not None
        img = raw[:, :, ::-1].astype(np.float64) / 255.0
        h, w = img.shape[:2]
        g = np.clip(apply_stack(img.reshape(-1, 3), lut_ops(stack_for(e, first.grade))), 0, 1).reshape(h, w, 3)
        tiles.append(g)
        out.append({"strength": s, "shot_difference": info["shot_difference"]["after"],
                    "mean_saturation": round(float(np.mean([x["saturation"] for x in shots])), 3), "grades": info["groups"]})
    sheet = np.concatenate([np.pad(t, ((0, 0), (0, 6), (0, 0)), constant_values=0.1) for t in tiles], axis=1)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{edl.name}-candidates.jpg"
    cv2.imwrite(str(path), (sheet[:, :, ::-1] * 255).round().astype(np.uint8))
    ranked = sorted(out, key=lambda x: x["shot_difference"])
    return {"sheet": str(path), "candidates": ranked}
