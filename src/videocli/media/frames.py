"""Still frames: accurate single frames, batched extraction, and a cache keyed by source, time and width."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from videocli.core.errors import InputError
from videocli.media import ffmpeg


def frame_path(cache_dir: Path, t: float, width: int, fmt: str) -> Path:
    return cache_dir / f"{int(round(t * 1000)):09d}-w{width}.{fmt}"


def extract_one(src: Path, t: float, out: Path, *, width: int, quality: int = 3) -> Path:
    """One frame at time ``t`` (accurate seek; display rotation applied by ffmpeg)."""
    if out.is_file():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part" + out.suffix)
    args = ["-y", "-ss", f"{max(0.0, t):.6f}", "-i", ffmpeg.fpath(src), "-map", "0:v:0", "-frames:v", "1",
            "-vf", f"scale={width}:-2:flags=bicubic"]
    if out.suffix == ".jpg":
        args += ["-q:v", str(quality)]
    args += ["-update", "1", ffmpeg.fpath(tmp)]
    ffmpeg.run(args, log_name="frame")
    if not tmp.is_file():
        # Seeking past the last frame yields nothing: fall back to the final frame.
        args[2] = f"{max(0.0, t - 0.5):.6f}"
        ffmpeg.run(args, log_name="frame")
    if not tmp.is_file():
        raise InputError("BAD_ARGUMENT", f"No frame at {t:.3f}s in {src.name} (past the end?).")
    tmp.replace(out)
    return out


def extract_many(src: Path, times: list[float], cache_dir: Path, *, width: int, fmt: str = "jpg", jobs: int = 4) -> list[Path]:
    """Frames at many times. Cached frames are reused; the rest are extracted in parallel."""
    outs = [frame_path(cache_dir, t, width, fmt) for t in times]
    todo = [(t, o) for t, o in zip(times, outs, strict=False) if not o.is_file()]
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            list(pool.map(lambda to: extract_one(src, to[0], to[1], width=width), todo))
    return outs


def extract_every(src: Path, every: float, start: float, end: float, cache_dir: Path, *, width: int, fmt: str = "jpg",
                  limit: int | None = None) -> list[tuple[float, Path]]:
    """Frames every ``every`` seconds from ``start`` to ``end`` with a single decode."""
    times = []
    t = start
    while t < end - 1e-6:
        times.append(round(t, 3))
        t += every
        if limit is not None and len(times) >= limit:
            break
    outs = [frame_path(cache_dir, x, width, fmt) for x in times]
    if all(o.is_file() for o in outs):
        return list(zip(times, outs, strict=False))
    if len(times) <= 8:
        return list(zip(times, extract_many(src, times, cache_dir, width=width, fmt=fmt), strict=False))
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmpdir = cache_dir / f".batch-{int(start * 1000)}-{int(every * 1000)}"
    tmpdir.mkdir(exist_ok=True)
    span = times[-1] - start + every
    # fps=1/every with a start offset emits one frame per interval, nearest to each tick.
    vf = f"fps=1/{every}:start_time=0:round=near,scale={width}:-2:flags=bicubic"
    args = ["-y", "-ss", f"{start:.6f}", "-t", f"{span:.6f}", "-i", ffmpeg.fpath(src), "-map", "0:v:0", "-vf", vf,
            "-frames:v", str(len(times)), "-q:v", "3", ffmpeg.fpath(tmpdir / f"f%06d.{fmt}")]
    ffmpeg.run(args, log_name="frames-every", progress_total=span)
    produced = sorted(tmpdir.glob(f"f*.{fmt}"))
    for path, out in zip(produced, outs, strict=False):
        path.replace(out)
    for p in tmpdir.iterdir():
        p.unlink()
    tmpdir.rmdir()
    missing = [(t, o) for t, o in zip(times, outs, strict=False) if not o.is_file()]
    for t, o in missing:
        extract_one(src, t, o, width=width)
    return list(zip(times, outs, strict=False))


def read_image(path: Path) -> np.ndarray:
    import cv2

    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise InputError("BAD_ARGUMENT", f"Cannot read image {path}.")
    return img
