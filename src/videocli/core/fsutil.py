"""Small filesystem helpers: atomic writes, JSON with stable formatting, sizes."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def dumps(data: Any, *, indent: int | None = 2) -> str:
    """Stable JSON: UTF-8, keys in insertion order, trailing newline."""
    return json.dumps(data, indent=indent, ensure_ascii=False, allow_nan=False) + "\n"


def write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, dumps(data))


def read_json(path: Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(root, f)).st_size
    return total


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"  # pragma: no cover


def rel(path: Path | str, base: Path | None = None) -> str:
    """Path relative to ``base`` (default: cwd) when inside it, else absolute; forward slashes."""
    p = Path(path)
    base = Path.cwd() if base is None else base
    try:
        return p.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return p.resolve().as_posix()
