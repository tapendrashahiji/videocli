"""``composite``: one chunk with a transition and/or layers, as one small filter graph in a script file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from videocli.media import ffmpeg
from videocli.render.graph import Node
from videocli.render.nodes.media import encode_args, picture_input, video_chain

MARGIN = 0.04
DIP = {"black": (16, 128, 128), "white": (235, 128, 128)}


def _pip_xy(pos: str | None, w: int, h: int) -> tuple[str, str]:
    m = int(round(min(w, h) * MARGIN))
    pos = pos or "br"
    if pos in ("tl", "tr", "bl", "br"):
        x = str(m) if pos[1] == "l" else f"W-w-{m}"
        y = str(m) if pos[0] == "t" else f"H-h-{m}"
        return x, y
    try:
        xs, ys = pos.split(",")
        fx, fy = float(xs), float(ys)
    except ValueError:
        return f"W-w-{m}", f"H-h-{m}"
    x = f"(W-w)*{fx}" if fx <= 1 else str(int(fx))
    y = f"(H-h)*{fy}" if fy <= 1 else str(int(fy))
    return x, y


def _blend(tr: dict[str, Any]) -> str:
    d = int(tr["frames"])
    off = int(tr["offset"])
    prog = f"clip((N+{off}+0.5)/{d},0,1)"
    if tr["type"] == "dissolve":
        expr = f"A*(1-{prog})+B*{prog}"
        return f"blend=all_expr='{expr}':shortest=1"
    color = DIP.get((tr.get("color") or "black").lower(), DIP["black"])
    if tr["type"] == "dip" and (tr.get("color") or "").lower() == "white":
        color = DIP["white"]
    a = f"max(0,1-2*{prog})"
    b = f"max(0,2*{prog}-1)"
    exprs = [f"A*{a}+B*{b}+{v}*(1-{a}-{b})" for v in color]
    return f"blend=c0_expr='{exprs[0]}':c1_expr='{exprs[1]}':c2_expr='{exprs[2]}':shortest=1"


def build_graph(p: dict[str, Any]) -> tuple[list[str], str]:
    """Return (input args, filter graph text)."""
    w, h = p["size"]
    rate = p["rate"]
    args: list[str] = []
    lines: list[str] = []
    idx = 0
    paths: list[str] = p.get("paths", [])
    inputs: list[Path] = p.get("_inputs", [])
    labels = []
    for k, b in enumerate(p["bases"]):
        bp = dict(b)
        bp["rate"] = rate
        if "path_index" in b:
            bp["path"] = paths[b["path_index"]]
        ins = [inputs[b["input"]]] if "input" in b else []
        args += picture_input(bp, ins, b["start"], w, h)
        chain = video_chain(b["speed"], rate, w, h, "yuv420p", crop=b.get("crop"), hold=bool(b.get("hold")))
        lines.append(f"[{idx}:v]{','.join(chain)}[b{k}]")
        labels.append(f"b{k}")
        idx += 1
    cur = labels[0]
    if p.get("transition") and len(labels) > 1:
        lines.append(f"[{labels[0]}][{labels[1]}]{_blend(p['transition'])}[t0]")
        cur = "t0"
    for j, lay in enumerate(p["layers"]):
        args += ["-ss", f"{lay['start']:.6f}", "-i", ffmpeg.fpath(paths[lay["path_index"]])]
        chain = ["setpts=PTS-STARTPTS", f"fps={rate}"]
        if lay["mode"] == "cover":
            chain += [f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=bicubic", f"crop={w}:{h}"]
        else:
            s = float(lay.get("scale") or 0.33)
            pw = max(16, int(round(w * s / 2)) * 2)
            chain += [f"scale={pw}:-2:flags=bicubic"]
        chain += ["setsar=1", "format=yuva420p"]
        op = float(lay.get("opacity", 1.0))
        if op < 1.0:
            chain.append(f"colorchannelmixer=aa={op:.4f}")
        chain.append("tpad=stop_mode=clone:stop=-1")
        lines.append(f"[{idx}:v]{','.join(chain)}[l{j}]")
        x, y = ("0", "0") if lay["mode"] == "cover" else _pip_xy(lay.get("pos"), w, h)
        lines.append(f"[{cur}][l{j}]overlay=x={x}:y={y}:format=auto:eof_action=repeat[o{j}]")
        cur = f"o{j}"
        idx += 1
    lines.append(f"[{cur}]format=yuv420p[out]")
    return args, ";\n".join(lines)


def run_composite(node: Node, out_dir: Path, inputs: list[Path]) -> str:
    p = {**node.params, **node.extra, "_inputs": inputs}
    args, graph = build_graph(p)
    script = out_dir / "graph.txt"
    script.write_text(graph + "\n", encoding="utf-8")
    out = out_dir / "chunk.mp4"
    ver = ffmpeg.locate("ffmpeg").version_tuple
    gopt = ["-/filter_complex", str(script)] if ver >= (7, 0) else ["-filter_complex_script", str(script)]
    ffmpeg.run(["-y", *args, *gopt, "-map", "[out]", "-an", "-frames:v", str(int(p["frames"])), *encode_args(p), ffmpeg.fpath(out)],
               log_name=f"composite-{node.label}")
    return out.name
