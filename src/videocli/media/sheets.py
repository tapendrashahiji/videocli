"""Contact sheets and strips, composed with OpenCV (labels need no font file)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from videocli.core.timeutil import format_hms

BG = (24, 24, 24)
FG = (255, 255, 255)
MARGIN = 6


@dataclass
class Tile:
    image: Path
    label: str
    time: float
    extra: dict | None = None


def _put_label(img: np.ndarray, text: str, *, scale: float) -> None:
    import cv2

    font = cv2.FONT_HERSHEY_SIMPLEX
    thick = max(1, int(round(scale * 2)))
    (tw, th), base = cv2.getTextSize(text, font, scale, thick)
    h, w = img.shape[:2]
    x, y = 4, h - 6
    cv2.rectangle(img, (0, y - th - 6), (min(w, tw + 10), h), (0, 0, 0), -1)
    cv2.putText(img, text, (x, y - 2), font, scale, FG, thick, cv2.LINE_AA)


def compose(tiles: list[Tile], out: Path, *, cols: int, width: int, quality: int = 85) -> dict:
    """Write one sheet. Returns its tile map."""
    import cv2

    cols = max(1, min(cols, len(tiles)))
    tile_w = (width - MARGIN * (cols + 1)) // cols
    first = cv2.imread(str(tiles[0].image))
    ar = first.shape[0] / first.shape[1] if first is not None else 9 / 16
    tile_h = max(16, int(round(tile_w * ar)))
    rows = (len(tiles) + cols - 1) // cols
    height = rows * tile_h + MARGIN * (rows + 1)
    sheet = np.full((height, width, 3), BG, dtype=np.uint8)
    scale = max(0.35, min(0.8, tile_w / 380))
    tile_map = []
    for i, t in enumerate(tiles):
        r, c = divmod(i, cols)
        img = cv2.imread(str(t.image))
        if img is None:
            img = np.zeros((tile_h, tile_w, 3), dtype=np.uint8)
        img = cv2.resize(img, (tile_w, tile_h), interpolation=cv2.INTER_AREA)
        _put_label(img, t.label, scale=scale)
        y = MARGIN + r * (tile_h + MARGIN)
        x = MARGIN + c * (tile_w + MARGIN)
        sheet[y:y + tile_h, x:x + tile_w] = img
        entry = {"row": r, "col": c, "time": t.time, "x": x, "y": y, "w": tile_w, "h": tile_h}
        if t.extra:
            entry.update(t.extra)
        tile_map.append(entry)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), sheet, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return {"path": str(out), "width": width, "height": height, "tiles": tile_map}


def paginate(tiles: list[Tile], out_stem: Path, *, cols: int, width: int, max_tiles: int) -> list[dict]:
    sheets = []
    for n, i in enumerate(range(0, len(tiles), max_tiles), 1):
        sheets.append(compose(tiles[i:i + max_tiles], out_stem.with_name(f"{out_stem.name}-{n:03d}.jpg"), cols=cols, width=width))
    return sheets


def label_time(t: float) -> str:
    return format_hms(t)[:-2] if t >= 3600 else format_hms(t)[3:-2]


def strip(frames: list[tuple[float, Path]], samples: np.ndarray, rate: int, start: float, end: float, out: Path, *, width: int) -> dict:
    """Frames across [start, end) above the audio waveform on one shared time axis."""
    import cv2

    n = len(frames)
    tile_w = (width - MARGIN * (n + 1)) // max(1, n)
    first = cv2.imread(str(frames[0][1])) if frames else None
    ar = first.shape[0] / first.shape[1] if first is not None else 9 / 16
    tile_h = max(16, int(round(tile_w * ar)))
    wave_h = max(80, tile_h // 2)
    axis_h = 28
    height = MARGIN * 3 + tile_h + wave_h + axis_h
    img = np.full((height, width, 3), BG, dtype=np.uint8)
    span = max(1e-6, end - start)
    plot_x0, plot_x1 = MARGIN, width - MARGIN
    plot_w = plot_x1 - plot_x0

    def x_of(t: float) -> int:
        return int(round(plot_x0 + (t - start) / span * plot_w))

    tiles = []
    for i, (t, p) in enumerate(frames):
        fr = cv2.imread(str(p))
        if fr is None:
            continue
        fr = cv2.resize(fr, (tile_w, tile_h), interpolation=cv2.INTER_AREA)
        _put_label(fr, label_time(t), scale=max(0.35, min(0.7, tile_w / 380)))
        x = MARGIN + i * (tile_w + MARGIN)
        img[MARGIN:MARGIN + tile_h, x:x + tile_w] = fr
        cx = x_of(t)
        cv2.line(img, (cx, MARGIN + tile_h), (cx, MARGIN * 2 + tile_h + wave_h), (90, 90, 90), 1)
        tiles.append({"col": i, "time": t, "x": x, "w": tile_w, "marker_x": cx})
    # waveform: min/max per pixel column
    wy0 = MARGIN * 2 + tile_h
    mid = wy0 + wave_h // 2
    cv2.line(img, (plot_x0, mid), (plot_x1, mid), (60, 60, 60), 1)
    if len(samples):
        cols = np.array_split(samples, plot_w)
        for i, c in enumerate(cols):
            if len(c) == 0:
                continue
            lo, hi = float(c.min()), float(c.max())
            y0 = int(mid - hi * (wave_h / 2 - 2))
            y1 = int(mid - lo * (wave_h / 2 - 2))
            cv2.line(img, (plot_x0 + i, y0), (plot_x0 + i, y1), (80, 200, 120), 1)
    # time axis with ticks
    ay = wy0 + wave_h + 4
    cv2.line(img, (plot_x0, ay), (plot_x1, ay), (180, 180, 180), 1)
    step = _nice_step(span / 8)
    t = (int(start / step) + 1) * step if start % step else start
    ticks = []
    while t <= end + 1e-9:
        x = x_of(t)
        cv2.line(img, (x, ay), (x, ay + 5), (180, 180, 180), 1)
        cv2.putText(img, f"{t:.2f}", (max(0, x - 18), ay + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.38, FG, 1, cv2.LINE_AA)
        ticks.append({"time": round(t, 3), "x": x})
        t = round(t + step, 6)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), img, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    return {"path": str(out), "width": width, "height": height, "from": start, "to": end, "frames": tiles, "ticks": ticks,
            "axis": {"x0": plot_x0, "x1": plot_x1, "seconds_per_pixel": span / plot_w}}


def _nice_step(x: float) -> float:
    for s in (0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
        if s >= x:
            return s
    return 600.0
