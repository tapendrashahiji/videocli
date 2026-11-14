"""Bakers (SPEC §10.6): parameters in, a clip out, checked against the bake contract.

Phase 6 ships ``stabilize`` (two-pass vid.stab), ``reframe`` (centre crop; subject crops come
from phase-5 face data) and ``generic`` (a command declared in the EDL). Motion-graphics bakers
(``titles``, ``manim``, ``html``) arrive in phase 8 behind the same contract.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from videocli.core.errors import RuntimeFailure, UsageError
from videocli.media import ffmpeg
from videocli.render.graph import Node

LONG_BAKE = 30.0  # seconds: qtrle below, ProRes 4444 above (decisions T3)


@dataclass
class BakeResult:
    path: Path
    duration: float
    size: tuple[int, int]
    fps: str
    alpha: bool


class Baker(Protocol):
    name: str
    version: int

    def bake(self, item: dict[str, Any], out_dir: Path) -> BakeResult: ...


def alpha_codec(duration: float) -> list[str]:
    if duration <= LONG_BAKE:
        return ["-c:v", "qtrle", "-pix_fmt", "argb"]
    return ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-vendor", "apl0"]


def check_contract(path: Path, *, duration: float, size: tuple[int, int] | None, fps: str | None, alpha: bool,
                   tolerance_frames: int = 1) -> BakeResult:
    """The bake contract: exact duration, frame rate and resolution as requested; transparency when required."""
    from videocli.core.timeutil import Rate
    from videocli.media.probe import probe_path

    if not path.is_file():
        raise RuntimeFailure("BAKE_FAILED", f"The baker wrote no file at {path.name}.")
    pr = probe_path(path)
    v = pr.video
    if v is None:
        raise RuntimeFailure("BAKE_FAILED", f"{path.name} has no video stream.")
    reasons = []
    r = Rate.parse(fps) if fps else Rate.parse(v.fps)
    if fps and abs(Rate.parse(v.fps).fps - r.fps) > 1e-3:
        reasons.append(f"frame rate {v.fps} != {fps}")
    got = v.duration if v.duration is not None else pr.duration
    if abs(got - duration) > tolerance_frames / r.fps + 1e-3:
        reasons.append(f"duration {got:.3f}s != {duration:.3f}s")
    if size and (v.width, v.height) != tuple(size):
        reasons.append(f"size {v.width}x{v.height} != {size[0]}x{size[1]}")
    if alpha and not v.alpha:
        reasons.append(f"no transparency (pixel format {v.pix_fmt})")
    if reasons:
        raise RuntimeFailure("BAKE_FAILED", f"Bake {path.name} broke the contract: {'; '.join(reasons)}.", details={"reasons": reasons})
    return BakeResult(path, got, (v.width, v.height), v.fps, v.alpha)


# --------------------------------------------------------------------------- stabilize


def run_stabilize(node: Node, out_dir: Path, inputs: list[Path]) -> str:
    p = {**node.params, **node.extra}
    trf = out_dir / "transforms.trf"
    dur = p["end"] - p["start"]
    strength = float(p["strength"])
    shakiness = max(1, min(10, int(round(1 + 9 * strength))))
    smoothing = max(1, int(round(5 + 45 * strength)))
    ffmpeg.require_filters("vidstabdetect", "vidstabtransform")
    common = ["-ss", f"{p['start']:.6f}", "-t", f"{dur:.6f}", "-i", ffmpeg.fpath(p["path"]), "-map", "0:v:0"]
    ffmpeg.run(["-y", *common, "-vf", f"vidstabdetect=shakiness={shakiness}:accuracy=15:result={trf.name}", "-f", "null", "-"],
               log_name="stabilize-detect", cwd=out_dir)
    out = out_dir / "stable.mp4"
    ffmpeg.run(["-y", *common, "-vf", f"vidstabtransform=input={trf.name}:smoothing={smoothing}:zoom=0:optzoom=1,unsharp=5:5:0.6:3:3:0.3",
                "-c:v", "libx264", "-crf", "10", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-an", ffmpeg.fpath(out)],
               log_name="stabilize-transform", cwd=out_dir)
    check_contract(out, duration=dur, size=None, fps=None, alpha=False, tolerance_frames=2)
    (out_dir / "bake.json").write_text(json.dumps({"baker": "stabilize", "shakiness": shakiness, "smoothing": smoothing}), encoding="utf-8")
    trf.unlink(missing_ok=True)
    return out.name


def stabilize_node(path: str, source: str, start: float, end: float, strength: float, rate: str) -> Node:
    return Node("bake", {"baker": "stabilize", "version": 1, "source": source, "start": start, "end": end, "strength": strength},
                label=f"stabilize-{source[:6]}@{start:.2f}", runner=run_stabilize, extra={"path": path})


# --------------------------------------------------------------------------- generic


def run_generic(name: str, argv: list[str], workdir: Path, *, duration: float, size: tuple[int, int], fps: str,
                alpha: bool = True, output: str = "out.mov") -> BakeResult:
    """Run a command declared in the EDL (``commands[name]``) inside the workspace, then check the contract.

    Placeholders: ``{out}``, ``{duration}``, ``{width}``, ``{height}``, ``{fps}``. No shell is used.
    """
    if not argv:
        raise UsageError("BAD_ARGUMENT", f"Generic baker command {name!r} is empty.")
    out = workdir / output
    subs = {"out": str(out), "duration": f"{duration:.6f}", "width": str(size[0]), "height": str(size[1]), "fps": fps}
    cmd = [a.format(**subs) for a in argv]
    workdir.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        tail = (res.stderr or res.stdout).strip().splitlines()[-1:] or ["no output"]
        raise RuntimeFailure("BAKE_FAILED", f"Generic baker {name!r} exited with {res.returncode}: {tail[0]}")
    return check_contract(out, duration=duration, size=size, fps=fps, alpha=alpha)
