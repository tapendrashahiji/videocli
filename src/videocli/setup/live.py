"""Live progress for long setup work: streamed installer output and download meters.

Installs and model downloads can take minutes. Silence looks like a hang to people
and to agents, so every long action reports at least every ``HEARTBEAT`` seconds:
what it is doing, how much has arrived, how fast, and for how long.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from videocli.core import progress

HEARTBEAT = 1.0

# Approximate download sizes of the faster-whisper models (MB), for a percentage.
WHISPER_SIZES_MB = {
    "tiny": 75, "tiny.en": 75, "base": 145, "base.en": 145, "small": 484, "small.en": 484,
    "medium": 1530, "medium.en": 1530, "large-v1": 3090, "large-v2": 3090, "large-v3": 3090,
    "large-v3-turbo": 1620, "turbo": 1620, "distil-large-v3": 1510,
}

_SIZE_RE = re.compile(r"\(\s*([\d.]+)\s*([KMG]i?B)\s*\)", re.I)
_UNITS = {"k": 1024, "m": 1024**2, "g": 1024**3}


def fmt_mb(n: float) -> str:
    mb = n / 1024**2
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"


def fmt_elapsed(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def parse_size(line: str) -> int | None:
    """Bytes from installer lines such as ``Downloading nvidia-cudnn-cu12 (571.0MiB)``."""
    m = _SIZE_RE.search(line)
    if not m:
        return None
    return int(float(m.group(1)) * _UNITS[m.group(2)[0].lower()])


def net_rx_bytes() -> int | None:
    """Bytes received on all non-loopback interfaces (Linux), else None."""
    try:
        total = 0
        with open("/proc/net/dev", encoding="ascii") as f:
            for line in f.readlines()[2:]:
                name, _, rest = line.partition(":")
                if name.strip() != "lo":
                    total += int(rest.split()[0])
        return total
    except (OSError, ValueError, IndexError):
        return None


def dir_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.stat(os.path.join(root, f)).st_size
    return total


class Meter:
    """Turns a growing byte count into ``done / total · speed · elapsed`` events."""

    def __init__(self, total: int | None = None):
        self.t0 = time.monotonic()
        self.total = total
        self.samples: list[tuple[float, int]] = []

    def report(self, done: int | None, note: str = "") -> None:
        now = time.monotonic()
        elapsed = now - self.t0
        parts: list[str] = []
        fields: dict[str, Any] = {"elapsed": round(elapsed, 1)}
        fraction = None
        if done is not None:
            self.samples.append((now, done))
            self.samples = [s for s in self.samples if now - s[0] <= 5.0]  # 5 s window
            t_old, b_old = self.samples[0]
            speed = (done - b_old) / (now - t_old) if now - t_old > 0.2 else 0.0
            parts.append(fmt_mb(done) + (f" / {fmt_mb(self.total)}" if self.total else ""))
            parts.append(f"{fmt_mb(speed)}/s")
            fields.update(bytes_done=done, bytes_per_second=int(speed))
            if self.total:
                fields["bytes_total"] = self.total
                fraction = min(0.99, done / self.total)
        parts.append(fmt_elapsed(elapsed))
        if note:
            parts.append(note)
        progress.update(fraction, " · ".join(parts), **fields)


def run_streaming(cmd: list[str], on_line: Callable[[str], None] | None = None) -> tuple[int, list[str]]:
    """Run ``cmd``, reporting its output lines and download volume live. Returns (code, tail)."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        text=True, errors="replace", bufsize=1, env=env,
    )
    tail: list[str] = []
    state = {"last": "starting", "total": 0}
    lock = threading.Lock()

    def reader() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            # pip and uv redraw with \r; keep the latest piece.
            line = raw.rstrip("\n").split("\r")[-1].strip()
            if not line:
                continue
            with lock:
                tail.append(line)
                del tail[:-40]
                state["last"] = line[:90]
                size = parse_size(line) if "download" in line.lower() else None
                if size:
                    state["total"] += size
            if on_line:
                on_line(line)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    rx0 = net_rx_bytes()
    meter = Meter()
    while proc.poll() is None:
        rx = net_rx_bytes()
        with lock:
            meter.total = state["total"] or None
            last = state["last"]
        meter.report(None if rx is None or rx0 is None else rx - rx0, last)
        proc_wait(proc, HEARTBEAT)
    t.join(timeout=5)
    return proc.returncode, tail


def proc_wait(proc: subprocess.Popen, seconds: float) -> None:
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=seconds)


def hf_cache_dirs() -> list[Path]:
    """Where Hugging Face downloads can stage bytes before the final file appears.

    The Xet transfer backend writes chunks to its own cache and only creates the
    model file at the end, so watching the target folder alone would sit near zero.
    """
    home = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface")
    dirs = [Path(os.environ["HF_XET_CACHE"])] if os.environ.get("HF_XET_CACHE") else []
    return [*dirs, home / "xet"]


def watch_download(fn: Callable[[], Any], target: Path, total: int | None, extra: list[Path] | None = None) -> Any:
    """Run ``fn`` in a thread while reporting how many bytes have landed under ``target``.

    ``extra`` folders (download caches) count too; the larger growth wins, so a
    backend that stages elsewhere and then copies is not counted twice.
    """
    watched = [target, *(extra or [])]

    def landed() -> int:
        return max(dir_bytes(d) - b for d, b in zip(watched, base, strict=True))

    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # re-raised in the caller's thread
            box["error"] = exc

    base = [dir_bytes(d) for d in watched]
    t = threading.Thread(target=run, daemon=True)
    t.start()
    meter = Meter(total)
    while t.is_alive():
        meter.report(max(0, landed()))
        t.join(timeout=HEARTBEAT)
    if "error" in box:
        raise box["error"]
    done = max(0, landed())
    progress.update(1.0, f"{fmt_mb(done)} · {fmt_elapsed(time.monotonic() - meter.t0)}", bytes_done=done)
    return box.get("value")


__all__ = ["WHISPER_SIZES_MB", "Meter", "dir_bytes", "hf_cache_dirs", "net_rx_bytes", "parse_size", "run_streaming", "watch_download"]
