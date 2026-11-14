"""Graphics bake nodes: ``titles`` (base), ``manim`` and ``html`` (optional groups), ``generic``."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from videocli.core.errors import RuntimeFailure
from videocli.render.graph import Node

GFX_VERSION = 1


def run_graphic(node: Node, out_dir: Path, inputs: list[Path]) -> str:
    from videocli.graphics.templates import Brand, Template

    p = node.params
    t = Template.model_validate(p["template"])
    brand = Brand.model_validate(p["brand"])
    size = (int(p["size"][0]), int(p["size"][1]))
    if t.baker == "titles":
        from videocli.graphics.titles import bake

        out = bake(t, p["params"], brand, size, p["rate"], int(p["frames"]), out_dir)
    elif t.baker == "manim":
        out = bake_manim(t, p, out_dir)
    elif t.baker == "html":
        out = bake_html(t, p, out_dir)
    else:
        from videocli.core.timeutil import Rate
        from videocli.render.bakers import run_generic

        argv = p.get("command") or []
        res = run_generic(t.name, argv, out_dir, duration=int(p["frames"]) / Rate.parse(p["rate"]).fps, size=size, fps=p["rate"])
        out = res.path
    (out_dir / "bake.json").write_text(json.dumps({"baker": t.baker, "template": t.name}), encoding="utf-8")
    return out.name


def graphic_node(template: Any, params: dict[str, Any], brand: Any, size: tuple[int, int], rate: str, frames: int, item_id: str,
                 command: list[str] | None = None) -> Node:
    tdump = template.model_dump(by_alias=True)
    folder = tdump.pop("folder", None)
    np: dict[str, Any] = {"baker": template.baker, "version": GFX_VERSION, "template": tdump, "params": params,
                          "brand": brand.model_dump(), "size": list(size), "rate": rate, "frames": frames}
    if folder and template.baker in ("manim", "html"):
        from videocli.core.cache import make_key

        np["template_files"] = make_key(sorted((str(p.relative_to(folder)), p.read_bytes().hex()[:200000])
                                               for p in Path(folder).rglob("*") if p.is_file()))
    if command:
        np["command"] = command
    return Node("bake", np, label=f"gfx-{item_id}", runner=run_graphic, extra={"folder": folder})


def bake_manim(t: Any, p: dict[str, Any], out_dir: Path) -> Path:
    """Render a Manim scene class with transparency at the requested size, rate and duration."""
    from videocli.setup.profiles import require_group

    require_group("manim", "Manim graphics")
    from videocli.core.timeutil import Rate
    from videocli.render.bakers import check_contract

    folder = Path(p["template"].get("folder") or "")
    scene_file = Path(folder) / (t.entry or "scene.py")
    fps = Rate.parse(p["rate"]).fps
    duration = int(p["frames"]) / fps
    work = out_dir / "manim"
    work.mkdir()
    (work / "params.json").write_text(json.dumps({**p["params"], "_duration": duration}), encoding="utf-8")
    shutil.copy(scene_file, work / "scene.py")
    w, h = p["size"]
    cmd = [shutil.which("manim") or "manim", "render", "scene.py", "-t", "--format", "mov", "-r", f"{w},{h}",
           "--fps", str(round(fps)), "--media_dir", "media", "-o", "out"]
    res = subprocess.run(cmd, cwd=work, capture_output=True, text=True, check=False,
                         env={**__import__("os").environ, "VIDEOCLI_PARAMS": str(work / "params.json")})
    outs = list((work / "media").rglob("out.mov"))
    if res.returncode != 0 or not outs:
        tail = (res.stderr or res.stdout).strip().splitlines()[-1:] or ["no output"]
        raise RuntimeFailure("BAKE_FAILED", f"Manim scene {t.name} failed: {tail[0]}", details={"log": res.stderr[-4000:]})
    out = out_dir / "graphic.mov"
    from videocli.media import ffmpeg

    ffmpeg.run(["-y", "-i", ffmpeg.fpath(outs[0]), "-vf", f"fps={p['rate']},tpad=stop_mode=clone:stop=-1", "-frames:v", str(p["frames"]),
                "-c:v", "qtrle", "-pix_fmt", "argb", ffmpeg.fpath(out)], log_name="manim-conform")
    check_contract(out, duration=duration, size=(w, h), fps=p["rate"], alpha=True)
    return out


def bake_html(t: Any, p: dict[str, Any], out_dir: Path) -> Path:
    """Render an HTML composition with HyperFrames (Node.js ≥ 22) to a transparent clip."""
    from videocli.setup.profiles import require_group

    require_group("html", "HTML graphics (HyperFrames)")
    from videocli.core.timeutil import Rate
    from videocli.render.bakers import check_contract

    folder = Path(p["template"].get("folder") or "")
    fps = Rate.parse(p["rate"]).fps
    duration = int(p["frames"]) / fps
    w, h = p["size"]
    (out_dir / "params.json").write_text(json.dumps(p["params"]), encoding="utf-8")
    raw = out_dir / "raw.mov"
    npx = shutil.which("npx") or "npx"
    cmd = [npx, "--yes", "hyperframes", "render", str(folder / (t.entry or "index.html")), "--output", str(raw),
           "--width", str(w), "--height", str(h), "--fps", str(round(fps)), "--duration", f"{duration:.3f}",
           "--transparent", "--props", str(out_dir / "params.json")]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0 or not raw.is_file():
        tail = (res.stderr or res.stdout).strip().splitlines()[-1:] or ["no output"]
        raise RuntimeFailure("BAKE_FAILED", f"HyperFrames template {t.name} failed: {tail[0]}",
                             hint="Check the template with: npx hyperframes render … ; see docs for the CLI version tested.")
    out = out_dir / "graphic.mov"
    from videocli.media import ffmpeg

    ffmpeg.run(["-y", "-i", ffmpeg.fpath(raw), "-vf", f"fps={p['rate']},tpad=stop_mode=clone:stop=-1", "-frames:v", str(p["frames"]),
                "-c:v", "qtrle", "-pix_fmt", "argb", ffmpeg.fpath(out)], log_name="html-conform")
    check_contract(out, duration=duration, size=(w, h), fps=p["rate"], alpha=True)
    return out
