"""Box arithmetic, free-space search and mapping source boxes into a reframed output."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np

Box = list[float]  # [x, y, w, h] fractions


def clamp_box(b: Sequence[float]) -> Box:
    x, y, w, h = (float(v) for v in b)
    x0, y0 = max(0.0, x), max(0.0, y)
    x1, y1 = min(1.0, x + w), min(1.0, y + h)
    return [round(x0, 4), round(y0, 4), round(max(0.0, x1 - x0), 4), round(max(0.0, y1 - y0), 4)]


def area(b: Sequence[float]) -> float:
    return max(0.0, b[2]) * max(0.0, b[3])


def intersection(a: Sequence[float], b: Sequence[float]) -> float:
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def covered_share(target: Sequence[float], cover: Sequence[float]) -> float:
    """How much of ``target`` lies under ``cover`` (0-1)."""
    a = area(target)
    return intersection(target, cover) / a if a > 0 else 0.0


def expand(b: Sequence[float], fx: float, fy: float | None = None) -> Box:
    """Grow a box around its centre by factors fx, fy."""
    fy = fx if fy is None else fy
    cx, cy = b[0] + b[2] / 2, b[1] + b[3] / 2
    w, h = b[2] * fx, b[3] * fy
    return clamp_box([cx - w / 2, cy - h / 2, w, h])


def quad_to_box(quad: Iterable[Sequence[float]], W: float, H: float) -> Box:
    pts = np.asarray(list(quad), dtype=float)
    x0, y0 = pts[:, 0].min() / W, pts[:, 1].min() / H
    x1, y1 = pts[:, 0].max() / W, pts[:, 1].max() / H
    return clamp_box([x0, y0, x1 - x0, y1 - y0])


# --------------------------------------------------------------------------- free space


def busy_grid(detail: np.ndarray, boxes: Iterable[Sequence[float]], cols: int = 32, rows: int = 18,
              detail_threshold: float = 0.06, pad: float = 0.01) -> np.ndarray:
    """True where the frame is busy: visual detail (edge density) or a known box (text, face, object)."""
    import cv2

    small = cv2.resize(detail.astype(np.float32), (cols, rows), interpolation=cv2.INTER_AREA)
    grid = small > detail_threshold
    for b in boxes:
        x0 = int(np.floor(max(0.0, b[0] - pad) * cols))
        y0 = int(np.floor(max(0.0, b[1] - pad) * rows))
        x1 = int(np.ceil(min(1.0, b[0] + b[2] + pad) * cols))
        y1 = int(np.ceil(min(1.0, b[1] + b[3] + pad) * rows))
        grid[y0:y1, x0:x1] = True
    return grid


def edge_density(gray: np.ndarray) -> np.ndarray:
    import cv2

    edges = cv2.Canny(gray, 60, 160)
    return (edges > 0).astype(np.float32)


def _largest_rectangle(free: np.ndarray) -> tuple[int, int, int, int, int]:
    """Largest all-True rectangle: (area, x, y, w, h) in grid cells (histogram method)."""
    rows, cols = free.shape
    heights = np.zeros(cols, dtype=int)
    best = (0, 0, 0, 0, 0)
    for r in range(rows):
        heights = np.where(free[r], heights + 1, 0)
        stack: list[int] = []
        for c in range(cols + 1):
            h = heights[c] if c < cols else 0
            start = c
            while stack and heights[stack[-1]] >= h:
                top = stack.pop()
                left = stack[-1] + 1 if stack else 0
                hh = int(heights[top])
                a = hh * (c - left)
                if a > best[0]:
                    best = (a, left, r - hh + 1, c - left, hh)
                start = left
            del start
            stack.append(c)
    return best


def free_rects(busy: np.ndarray, *, max_rects: int = 6, min_w: float = 0.12, min_h: float = 0.08) -> list[Box]:
    """Largest empty rectangles, biggest first, as fractions of the frame."""
    rows, cols = busy.shape
    free = ~busy.copy()
    out: list[Box] = []
    for _ in range(max_rects):
        a, x, y, w, h = _largest_rectangle(free)
        if a == 0 or w / cols < min_w or h / rows < min_h:
            break
        out.append(clamp_box([x / cols, y / rows, w / cols, h / rows]))
        free[y:y + h, x:x + w] = False
    return out


# --------------------------------------------------------------------------- reframe mapping


def crop_window(src_ar: float, out_ar: float, cx: float) -> Box:
    """The source region a centre/subject crop keeps, as a box of the source frame."""
    if out_ar < src_ar:  # output narrower: full height, part of the width
        w = out_ar / src_ar
        return [round((1 - w) * cx, 4), 0.0, round(w, 4), 1.0]
    h = src_ar / out_ar
    return [0.0, round((1 - h) / 2, 4), 1.0, round(h, 4)]


def _through(b: Sequence[float], win: Sequence[float], dst: Sequence[float]) -> Box | None:
    """Map box ``b`` (source fractions) through crop window ``win`` into the output region ``dst``."""
    x0, y0 = (b[0] - win[0]) / win[2], (b[1] - win[1]) / win[3]
    x1, y1 = (b[0] + b[2] - win[0]) / win[2], (b[1] + b[3] - win[1]) / win[3]
    x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1)
    if x1 <= x0 or y1 <= y0:
        return None
    return clamp_box([dst[0] + x0 * dst[2], dst[1] + y0 * dst[3], (x1 - x0) * dst[2], (y1 - y0) * dst[3]])


def map_box(b: Sequence[float], src_size: tuple[int, int], out_size: tuple[int, int], reframe: Any = None,
            cx: float = 0.5) -> Box | None:
    """Where a source box appears in the output frame (None when it is cropped away).

    Handles letterboxing (no reframe), centre/subject crops and the stacked layout (whose top
    panel shows a crop of the screen and bottom panel the face box).
    """
    sw, sh = src_size
    ow, oh = out_size
    src_ar, out_ar = sw / sh, ow / oh
    if reframe is None:
        if abs(src_ar - out_ar) < 1e-3:
            return clamp_box(b)
        if src_ar > out_ar:  # pillarbox top/bottom bars
            h = out_ar / src_ar
            return _through(b, [0, 0, 1, 1], [0.0, (1 - h) / 2, 1.0, h])
        w = src_ar / out_ar
        return _through(b, [0, 0, 1, 1], [(1 - w) / 2, 0.0, w, 1.0])
    if getattr(reframe, "mode", None) == "stack" and getattr(reframe, "face", None):
        split = float(reframe.split)
        ht = round(oh * split / 2) * 2
        top_ar = ow / ht
        top = _through(b, crop_window(src_ar, top_ar, cx), [0.0, 0.0, 1.0, ht / oh])
        if top is not None:
            return top
        fx, fy, fw, fh = reframe.face
        hb = oh - ht
        bar = ow / hb
        # same cover crop as the renderer: inside the face box, at the bottom panel's shape
        ch = min(fh * sh, fw * sw / bar) / sh
        cw = ch * sh * bar / sw
        wx = min(max(fx + fw / 2 - cw / 2, 0.0), 1 - cw)
        wy = min(max(fy + fh / 2 - ch / 2, 0.0), 1 - ch)
        return _through(b, [wx, wy, cw, ch], [0.0, ht / oh, 1.0, hb / oh])
    return _through(b, crop_window(src_ar, out_ar, cx), [0.0, 0.0, 1.0, 1.0])
