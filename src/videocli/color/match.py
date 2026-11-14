"""Automatic balance and shot matching, expressed as ordinary grade ops (always editable)."""

from __future__ import annotations

from typing import Any

import numpy as np

from videocli.color.ops import LUMA, apply_stack, rgb_to_hsv
from videocli.color.scopes import white_balance

SKIN = np.array([[0.80, 0.60, 0.50], [0.65, 0.45, 0.36], [0.45, 0.30, 0.24]])  # reference skin tones (sRGB)
SKIN_TOLERANCE = 8.0  # degrees of hue


def balance_ops(rgb: np.ndarray, target_mid: float = 0.42) -> list[dict[str, Any]]:
    """Neutralise the white-balance estimate and set exposure from the median luma."""
    ops: list[dict[str, Any]] = []
    wb = white_balance(rgb)
    if abs(wb["kelvin_shift"]) > 100 or abs(wb["tint"]) > 0.02:
        ops.append({"op": "temperature", "kelvin_shift": float(np.clip(-wb["kelvin_shift"], -5000, 5000)),
                    "tint": float(np.clip(-wb["tint"] / 2, -1, 1))})
    y = apply_stack(rgb, ops) @ LUMA if ops else rgb @ LUMA
    mid = float(np.median(y)) if len(y) else target_mid
    if mid > 0.02:
        stops = float(np.clip(np.log2(target_mid / mid), -2, 2))
        p99 = float(np.percentile(y, 99))
        # no added clipping: limit the push so the 99th percentile stays below 0.97 (or where it already was)
        ceiling = max(0.97, p99)
        if p99 * 2**stops > ceiling:
            stops = float(np.log2(ceiling / max(p99, 1e-6)))
        if abs(stops) > 0.05:
            ops.append({"op": "exposure", "stops": round(stops, 3)})
    return ops


def _skin_hue_shift(ops: list[dict[str, Any]]) -> float:
    before = rgb_to_hsv(SKIN)[:, 0]
    after = rgb_to_hsv(np.clip(apply_stack(SKIN, ops), 0, 1))[:, 0]
    d = np.abs((after - before + 180) % 360 - 180)
    return float(d.max())


def match_cdl(rgb: np.ndarray, ref: np.ndarray, strength: float = 1.0) -> dict[str, Any]:
    """A CDL moving ``rgb``'s per-channel mean and spread toward ``ref``'s, limited by strength and skin."""
    mu_s, mu_r = rgb.mean(axis=0), ref.mean(axis=0)
    sd_s, sd_r = rgb.std(axis=0) + 1e-6, ref.std(axis=0) + 1e-6

    def make(k: float) -> dict[str, Any]:
        slope = 1 + k * (sd_r / sd_s - 1)
        slope = np.clip(slope, 0.5, 2.0)
        offset = np.clip(mu_r * k + mu_s * (1 - k) - mu_s * slope, -0.3, 0.3)
        return {"op": "cdl", "slope": [round(float(v), 4) for v in slope], "offset": [round(float(v), 4) for v in offset],
                "power": [1.0, 1.0, 1.0], "sat": 1.0}

    k = float(np.clip(strength, 0, 1))
    op = make(k)
    # skin constraint: shrink the correction until skin hue stays within tolerance
    lo, hi = 0.0, k
    if _skin_hue_shift([op]) > SKIN_TOLERANCE:
        for _ in range(20):
            mid = (lo + hi) / 2
            if _skin_hue_shift([make(mid)]) > SKIN_TOLERANCE:
                hi = mid
            else:
                lo = mid
        op = make(lo)
        op["_clamped"] = True
    # no added clipping
    y0 = float(np.percentile(rgb @ LUMA, 99))
    y1 = float(np.percentile(np.clip(apply_stack(rgb, [op]), 0, 1) @ LUMA, 99))
    if y1 > max(0.97, y0) + 0.01:
        scale = (max(0.97, y0)) / y1
        op["slope"] = [round(v * scale, 4) for v in op["slope"]]
    return op


def look_from_reference(ref: np.ndarray, base: np.ndarray, strength: float = 1.0) -> list[dict[str, Any]]:
    """Fit contrast, saturation and warmth of ``base`` toward a reference image."""
    def stats(x):
        y = x @ LUMA
        hsv = rgb_to_hsv(np.clip(x, 0, 1))
        return float(np.percentile(y, 99) - np.percentile(y, 1)), float(hsv[:, 1].mean()), float(x[:, 0].mean() - x[:, 2].mean())

    c_r, s_r, w_r = stats(ref)
    c_b, s_b, w_b = stats(base)
    k = float(np.clip(strength, 0, 1))
    amount = 1 + k * (c_r / max(c_b, 1e-3) - 1)
    sat = 1 + k * (s_r / max(s_b, 1e-3) - 1)
    kelvin = k * (w_r - w_b) / 0.06 * 1000 * 0.5
    ops = [
        {"op": "contrast", "amount": round(float(np.clip(amount, 0.5, 2.0)), 3), "pivot": 0.435},
        {"op": "saturation", "amount": round(float(np.clip(sat, 0.0, 2.5)), 3)},
        {"op": "temperature", "kelvin_shift": round(float(np.clip(kelvin, -3000, 3000)), 0), "tint": 0.0},
    ]
    return ops
