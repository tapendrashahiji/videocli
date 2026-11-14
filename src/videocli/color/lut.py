"""LUT compiler: evaluate a grade stack on a 33³ or 65³ lattice, write ``.cube``, check it, cache it."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from videocli.color.ops import apply_stack, lut_ops
from videocli.core.cache import make_key
from videocli.core.errors import InputError, RuntimeFailure
from videocli.core.fsutil import atomic_write_text

LUT_VERSION = 1


def lattice(size: int) -> np.ndarray:
    """All lattice points in .cube order (red varies fastest), shape (size³, 3)."""
    g = np.linspace(0.0, 1.0, size)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    return np.stack([r.ravel(), gg.ravel(), b.ravel()], axis=1)


def compile_stack(ops: list[dict[str, Any]], size: int = 33) -> np.ndarray:
    pts = lattice(size)
    return np.clip(apply_stack(pts, lut_ops(ops)), 0.0, 1.0)


def write_cube(path: Path, table: np.ndarray, size: int, title: str = "videocli") -> None:
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in table]
    atomic_write_text(path, "\n".join(lines) + "\n")


def read_cube(path: str | Path) -> tuple[int, np.ndarray]:
    p = Path(path)
    if not p.is_file():
        raise InputError("FILE_NOT_FOUND", f"No LUT at {path}.")
    size = None
    rows = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.upper().startswith("LUT_3D_SIZE"):
            size = int(line.split()[1])
            continue
        if line[0].isalpha():
            continue
        vals = line.split()
        if len(vals) == 3:
            rows.append([float(v) for v in vals])
    if size is None or len(rows) != size ** 3:
        raise InputError("BAD_ARGUMENT", f"{path} is not a valid 3D .cube (size {size}, {len(rows)} rows).")
    return size, np.asarray(rows, dtype=np.float64)


def apply_cube(rgb: np.ndarray, table: np.ndarray, size: int) -> np.ndarray:
    """Trilinear interpolation of ``rgb`` (N, 3) through a .cube table."""
    t = table.reshape(size, size, size, 3)  # [b][g][r]
    x = np.clip(rgb, 0, 1) * (size - 1)
    i0 = np.floor(x).astype(int)
    i0 = np.clip(i0, 0, size - 2)
    f = x - i0
    r0, g0, b0 = i0[:, 0], i0[:, 1], i0[:, 2]
    fr, fg, fb = f[:, 0:1], f[:, 1:2], f[:, 2:3]
    out = np.zeros_like(rgb, dtype=np.float64)
    for db in (0, 1):
        for dg in (0, 1):
            for dr in (0, 1):
                w = (fr if dr else 1 - fr) * (fg if dg else 1 - fg) * (fb if db else 1 - fb)
                out += w * t[b0 + db, g0 + dg, r0 + dr]
    return out


def check_table(table: np.ndarray, size: int) -> list[str]:
    """A broken stack cannot ship: values in range, and a grey ramp that never goes backwards."""
    problems = []
    if not np.all(np.isfinite(table)):
        problems.append("non-finite values")
    if table.min() < -1e-6 or table.max() > 1 + 1e-6:
        problems.append("values outside 0..1")
    ramp = np.linspace(0, 1, 256)
    grey = apply_cube(np.stack([ramp] * 3, axis=1), table, size)
    y = grey @ np.array([0.2126, 0.7152, 0.0722])
    if np.any(np.diff(y) < -1e-4):
        problems.append("grey ramp is not monotonic")
    return problems


def stack_key(ops: list[dict[str, Any]], size: int) -> str:
    lut_files = {}
    for o in ops:
        if o["op"] == "lut":
            p = Path(o["path"])
            lut_files[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
    return make_key("lut", LUT_VERSION, lut_ops(ops), size, lut_files)


def build(ops: list[dict[str, Any]], cache_dir: Path, size: int = 33) -> Path:
    """Compiled .cube for a stack, cached by content."""
    key = stack_key(ops, size)
    path = cache_dir / f"{key[:24]}.cube"
    if path.is_file():
        return path
    table = compile_stack(ops, size)
    problems = check_table(table, size)
    if problems:
        raise RuntimeFailure("RENDER_FAILED", f"The grade stack compiles to a broken LUT: {', '.join(problems)}.",
                             hint="Reduce extreme values (contrast, curves, power) and try again.")
    cache_dir.mkdir(parents=True, exist_ok=True)
    write_cube(path, table, size)
    return path
