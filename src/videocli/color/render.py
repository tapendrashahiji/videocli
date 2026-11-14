"""Turn a clip's grade into render filters: input transform → compiled LUT (clip grade, then look) → spatial ops."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from videocli.color import input as ctin
from videocli.color import lut as lutmod
from videocli.color.ops import lut_ops, spatial_ops
from videocli.edl.model import EDL

TO_RGB = "scale=in_color_matrix=bt709:in_range=tv:out_range=pc:flags=accurate_rnd+full_chroma_int,format=gbrp16le"
TO_YUV = "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd,format=yuv420p10le"


def stack_for(edl: EDL, grade_key: str | None) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    if grade_key and grade_key in edl.grades:
        ops += edl.grades[grade_key].ops
    if "look" in edl.grades and grade_key != "look":
        ops += edl.grades["look"].ops
    return ops


def timeline_graded(edl: EDL) -> bool:
    return bool(any(c.grade for c in edl.tracks.main) or ("look" in edl.grades and edl.grades["look"].ops)
                or any(o.grade for o in edl.tracks.overlay)
                or any(s.input_transform and s.input_transform != "none" for s in edl.sources.values()))


def _spatial(op: dict[str, Any], uid: str, nested_lut: str | None) -> str:
    name = op["op"]
    amt = float(op.get("amount", 0.5))
    if name == "vignette":
        return f"vignette=angle={0.2 + amt * 0.6:.3f}"
    if name == "denoise":
        return f"hqdn3d={amt * 6:.2f}:{amt * 4:.2f}:{amt * 6:.2f}:{amt * 4:.2f}"
    if name == "sharpen":
        return f"unsharp=5:5:{amt * 1.5:.2f}:5:5:0"
    if name == "deband":
        return "deband=1thr=0.02:2thr=0.02:3thr=0.02:range=16:blur=1"
    if name == "window":
        shape = op.get("shape", "ellipse")
        cx, cy = float(op.get("x", 0.5)), float(op.get("y", 0.5))
        rx, ry = float(op.get("w", 0.5)) / 2, float(op.get("h", 0.5)) / 2
        fe = max(1e-3, float(op.get("feather", 0.2)))
        if shape == "rect":
            d = f"max(abs(X/W-{cx})/{rx},abs(Y/H-{cy})/{ry})"
        else:
            d = f"hypot((X/W-{cx})/{rx},(Y/H-{cy})/{ry})"
        m = f"255*clip((1+{fe}-{d})/{fe},0,1)"
        inner = f"{TO_RGB},lut3d=file={ctin.esc(nested_lut)}:interp=tetrahedral,{TO_YUV}" if nested_lut else "null"
        return (f"split=3[{uid}a][{uid}b][{uid}c];[{uid}b]{inner}[{uid}g];"
                f"[{uid}c]geq=lum='{m}':cb='{m}':cr='{m}'[{uid}m];[{uid}a][{uid}g][{uid}m]maskedmerge")
    raise ValueError(name)


def filters(edl: EDL, source: str, grade_key: str | None, grades_dir: Path | None, uid: str) -> tuple[list[str], list[str], dict[str, Any]]:
    """(pre filters, post filters, key params) for one picture input."""
    pre: list[str] = []
    post: list[str] = []
    key: dict[str, Any] = {}
    itf = edl.sources[source].input_transform
    f = ctin.filter_for(itf)
    if f:
        pre.append(f)
        key["itf"] = itf
    ops = stack_for(edl, grade_key)
    lops = lut_ops(ops)
    if lops and grades_dir is not None:
        path = lutmod.build(ops, grades_dir)
        post += [TO_RGB, f"lut3d=file={ctin.esc(str(path))}:interp=tetrahedral", TO_YUV]
        key["lut"] = path.stem
    for i, op in enumerate(spatial_ops(ops)):
        nested = None
        if op["op"] == "window" and op.get("ops") and grades_dir is not None:
            nested = str(lutmod.build(op["ops"], grades_dir))
        post.append(_spatial(op, f"{uid}w{i}", nested))
        key.setdefault("spatial", []).append(op)
    return pre, post, key
