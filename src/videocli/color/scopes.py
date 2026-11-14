"""Scopes as numbers: luma percentiles, clipping, channel means, saturation, white balance, shot-to-shot difference."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from videocli.color.ops import LUMA, apply_stack, rgb_to_hsv


def load_rgb(path: Path) -> np.ndarray:
    import cv2

    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        return np.zeros((0, 3))
    return img[:, :, ::-1].reshape(-1, 3).astype(np.float64) / 255.0


def white_balance(rgb: np.ndarray) -> dict[str, float]:
    """Estimate the cast from near-neutral pixels; kelvin_shift > 0 means the image is warm."""
    if not len(rgb):
        return {"kelvin_shift": 0.0, "tint": 0.0, "neutral_share": 0.0}
    hsv = rgb_to_hsv(rgb)
    y = rgb @ LUMA
    m = (hsv[:, 1] < 0.25) & (y > 0.15) & (y < 0.92)
    share = float(m.mean())
    if m.sum() < 50:
        m = (y > 0.15) & (y < 0.92)
    if m.sum() < 10:
        return {"kelvin_shift": 0.0, "tint": 0.0, "neutral_share": share}
    r, g, b = rgb[m].mean(axis=0)
    ratio = r / max(b, 1e-6)
    k = (ratio - 1) / (0.06 * (ratio + 1)) * 1000.0
    tint = float(1 - g / max((r + b) / 2, 1e-6)) * 2  # positive = magenta cast
    return {"kelvin_shift": round(float(np.clip(k, -5000, 5000)), 0), "tint": round(tint, 3), "neutral_share": round(share, 3)}


def lab_mean(rgb: np.ndarray) -> np.ndarray:
    import cv2

    if not len(rgb):
        return np.zeros(3)
    mean = rgb.mean(axis=0).astype(np.float32).reshape(1, 1, 3)
    return cv2.cvtColor(mean, cv2.COLOR_RGB2Lab).reshape(3).astype(np.float64)


def measure(rgb: np.ndarray, ops: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if ops:
        rgb = np.clip(apply_stack(rgb, ops), 0, 1)
    if not len(rgb):
        return {}
    y = rgb @ LUMA
    hsv = rgb_to_hsv(np.clip(rgb, 0, 1))
    p1, p50, p99 = (float(x) for x in np.percentile(y, [1, 50, 99]))
    return {
        "luma": {"p1": round(p1, 3), "p50": round(p50, 3), "p99": round(p99, 3),
                 "clipped_high": round(float((rgb.max(axis=1) >= 0.995).mean()), 4),
                 "crushed_low": round(float((rgb.max(axis=1) <= 0.005).mean()), 4)},
        "rgb_mean": [round(float(v), 3) for v in rgb.mean(axis=0)],
        "saturation": round(float(hsv[:, 1].mean()), 3),
        "contrast": round(p99 - p1, 3),
        "warmth": round(float(rgb[:, 0].mean() - rgb[:, 2].mean()), 3),
        "white_balance": white_balance(rgb),
        "skin": None,
        "_lab": lab_mean(rgb).tolist(),
    }


def delta_e(lab_a: list[float], lab_b: list[float]) -> float:
    return round(float(np.linalg.norm(np.asarray(lab_a) - np.asarray(lab_b))), 2)


def finish(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prev = None
    for s in shots:
        lab = s.pop("_lab", None)
        s["delta_to_previous"] = delta_e(prev, lab) if (prev is not None and lab is not None) else None
        prev = lab
    return shots
