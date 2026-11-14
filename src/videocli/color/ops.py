"""Grade operations (SPEC §8.6): pure functions on RGB arrays in [0, 1], shape (N, 3).

Every op that changes colour only (not position) compiles into one 3D LUT. ``window``,
``vignette``, ``denoise``, ``sharpen`` and ``deband`` are spatial: they become render filters.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import numpy as np

from videocli.core.errors import UsageError

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float64)  # Rec.709
SPATIAL = {"window", "vignette", "denoise", "sharpen", "deband"}
LUT_OPS = {"cdl", "exposure", "temperature", "contrast", "curves", "rolloff", "qualifier", "lut", "saturation"}


def _vec3(v: Any, name: str) -> np.ndarray:
    if isinstance(v, (int, float)):
        return np.array([float(v)] * 3)
    a = np.asarray(v, dtype=np.float64).reshape(-1)
    if a.size != 3:
        raise UsageError("BAD_ARGUMENT", f"{name} needs 1 or 3 values.")
    return a


def luma(rgb: np.ndarray) -> np.ndarray:
    return rgb @ LUMA


def saturate(rgb: np.ndarray, sat: float) -> np.ndarray:
    y = luma(rgb)[:, None]
    return y + (rgb - y) * sat


def op_cdl(rgb: np.ndarray, slope=1.0, offset=0.0, power=1.0, sat: float = 1.0, **_: Any) -> np.ndarray:
    """ASC CDL: out = (in × slope + offset) ^ power per channel, then saturation (Rec.709 weights)."""
    s, o, p = _vec3(slope, "slope"), _vec3(offset, "offset"), _vec3(power, "power")
    x = np.clip(rgb * s + o, 0.0, None) ** p
    return saturate(x, float(sat)) if sat != 1.0 else x


def op_exposure(rgb: np.ndarray, stops: float = 0.0, **_: Any) -> np.ndarray:
    return rgb * (2.0 ** float(stops))


def kelvin_gains(kelvin_shift: float, tint: float = 0.0) -> np.ndarray:
    """Approximate RGB gains for a white-balance shift (positive = warmer)."""
    k = float(kelvin_shift) / 1000.0
    r = 1.0 + 0.06 * k
    b = 1.0 - 0.06 * k
    g = 1.0 - 0.5 * float(tint)
    gains = np.array([r, g, b])
    return gains / (gains @ LUMA)  # keep luma constant


def op_temperature(rgb: np.ndarray, kelvin_shift: float = 0.0, tint: float = 0.0, **_: Any) -> np.ndarray:
    return rgb * kelvin_gains(kelvin_shift, tint)


def op_contrast(rgb: np.ndarray, amount: float = 1.0, pivot: float = 0.435, **_: Any) -> np.ndarray:
    return (rgb - pivot) * float(amount) + pivot


def op_saturation(rgb: np.ndarray, amount: float = 1.0, **_: Any) -> np.ndarray:
    return saturate(rgb, float(amount))


def op_curves(rgb: np.ndarray, master=None, r=None, g=None, b=None, **_: Any) -> np.ndarray:
    """Piecewise-linear curves through control points [[in, out], ...] (master then per channel)."""
    out = rgb.copy()

    def apply(x: np.ndarray, pts) -> np.ndarray:
        pts = np.asarray(pts, dtype=np.float64)
        order = np.argsort(pts[:, 0])
        return np.interp(x, pts[order, 0], pts[order, 1])

    if master:
        out = apply(out, master)
    for i, pts in enumerate((r, g, b)):
        if pts:
            out[:, i] = apply(out[:, i], pts)
    return out


def op_rolloff(rgb: np.ndarray, threshold: float = 0.8, **_: Any) -> np.ndarray:
    """Soft highlight knee: values above ``threshold`` compress smoothly towards 1.0."""
    t = float(threshold)
    x = rgb.copy()
    hi = x > t
    room = 1.0 - t
    x[hi] = t + room * (1 - np.exp(-(x[hi] - t) / room))
    return x


def rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    mx = rgb.max(axis=1)
    mn = rgb.min(axis=1)
    d = mx - mn
    h = np.zeros_like(mx)
    m = d > 1e-9
    rm = m & (mx == r)
    gm = m & (mx == g) & ~rm
    bm = m & ~rm & ~gm
    h[rm] = ((g - b)[rm] / d[rm]) % 6
    h[gm] = (b - r)[gm] / d[gm] + 2
    h[bm] = (r - g)[bm] / d[bm] + 4
    h = h * 60.0
    s = np.where(mx > 1e-9, d / np.maximum(mx, 1e-9), 0.0)
    return np.stack([h, s, mx], axis=1)


def _soft_range(x: np.ndarray, lo: float, hi: float, soft: float, circular: bool = False) -> np.ndarray:
    if circular:
        centre = ((lo + hi) / 2) % 360
        half = ((hi - lo) % 360) / 2
        dist = np.abs((x - centre + 180) % 360 - 180)
        return np.clip(1 - (dist - half) / max(soft, 1e-6), 0, 1)
    return np.clip(np.minimum((x - lo) / max(soft, 1e-6) + 1, (hi - x) / max(soft, 1e-6) + 1), 0, 1)


def op_qualifier(rgb: np.ndarray, hue=(0, 360), sat=(0.0, 1.0), lum=(0.0, 1.0), soft: float = 0.1, ops=None, **_: Any) -> np.ndarray:
    """Soft mask from hue (degrees), saturation and luma ranges; nested ops apply inside the mask."""
    hsv = rgb_to_hsv(np.clip(rgb, 0, 1))
    full_hue = (hue[1] - hue[0]) >= 360
    mh = np.ones(len(rgb)) if full_hue else _soft_range(hsv[:, 0], hue[0], hue[1], soft * 180, circular=True)
    mask = mh * _soft_range(hsv[:, 1], sat[0], sat[1], soft) * _soft_range(luma(np.clip(rgb, 0, 1)), lum[0], lum[1], soft)
    inner = apply_stack(rgb, ops or [])
    return rgb + (inner - rgb) * mask[:, None]


def op_lut(rgb: np.ndarray, path: str = "", strength: float = 1.0, **_: Any) -> np.ndarray:
    from videocli.color.lut import apply_cube, read_cube

    size, table = read_cube(path)
    out = apply_cube(np.clip(rgb, 0, 1), table, size)
    return rgb + (out - rgb) * float(strength) if strength != 1.0 else out


OPS: dict[str, Callable[..., np.ndarray]] = {
    "cdl": op_cdl, "exposure": op_exposure, "temperature": op_temperature, "contrast": op_contrast, "saturation": op_saturation,
    "curves": op_curves, "rolloff": op_rolloff, "qualifier": op_qualifier, "lut": op_lut,
}

RANGES = {
    "slope": (0.0, 4.0), "offset": (-1.0, 1.0), "power": (0.1, 4.0), "sat": (0.0, 3.0), "stops": (-6.0, 6.0),
    "kelvin_shift": (-5000.0, 5000.0), "tint": (-1.0, 1.0), "amount": (0.0, 4.0), "pivot": (0.0, 1.0),
    "threshold": (0.3, 0.99), "strength": (0.0, 1.0), "radius": (0.0, 1.0), "feather": (0.0, 1.0),
}


def validate_op(op: dict[str, Any]) -> dict[str, Any]:
    name = op.get("op")
    if name not in OPS and name not in SPATIAL and name != "match":
        raise UsageError("BAD_ARGUMENT", f"Unknown grade op {name!r}.", hint=f"Ops: {', '.join(sorted({*OPS, *SPATIAL}))}")
    for k, v in op.items():
        if k in RANGES:
            lo, hi = RANGES[k]
            vals = v if isinstance(v, (list, tuple)) else [v]
            for x in vals:
                if not isinstance(x, (int, float)) or not lo <= float(x) <= hi:
                    raise UsageError("BAD_ARGUMENT", f"{name}: {k}={v} is outside {lo}..{hi}.")
    if name == "lut":
        from pathlib import Path

        if not Path(str(op.get("path", ""))).is_file():
            raise UsageError("BAD_ARGUMENT", f"LUT file not found: {op.get('path')}")
    if name == "qualifier":
        for nested in op.get("ops") or []:
            validate_op(nested)
    return op


def apply_stack(rgb: np.ndarray, ops: list[dict[str, Any]]) -> np.ndarray:
    x = np.asarray(rgb, dtype=np.float64)
    for op in ops:
        name = op["op"]
        if name in SPATIAL or name == "match":
            continue
        x = OPS[name](x, **{k: v for k, v in op.items() if k != "op"})
    return x


def lut_ops(ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [o for o in ops if o["op"] in LUT_OPS]


def spatial_ops(ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [o for o in ops if o["op"] in SPATIAL]


_NUM = r"-?\d+(?:\.\d+)?"


def parse_op(text: str) -> dict[str, Any]:
    """Text form: ``cdl slope=1.05,1,0.95 sat=0.9`` or ``exposure stops=0.3``."""
    parts = text.strip().split()
    if not parts:
        raise UsageError("BAD_ARGUMENT", "Empty --op.")
    op: dict[str, Any] = {"op": parts[0]}
    for kv in parts[1:]:
        if "=" not in kv:
            raise UsageError("BAD_ARGUMENT", f"Expected key=value in --op, got {kv!r}.")
        k, v = kv.split("=", 1)
        if re.fullmatch(rf"{_NUM}(,{_NUM})*", v):
            nums = [float(x) for x in v.split(",")]
            op[k] = nums[0] if len(nums) == 1 else nums
        elif k in ("hue", "sat", "lum") and "-" in v:
            a, b = v.split("-", 1)
            op[k] = [float(a), float(b)]
        else:
            op[k] = v
    return validate_op(op)
