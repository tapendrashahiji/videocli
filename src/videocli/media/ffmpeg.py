"""The one door to ffmpeg and ffprobe (SPEC §4.3, §10.3).

* List arguments only, never a shell. ``-nostdin`` and ``-hide_banner`` always.
* Local paths go through :func:`fpath` (``file:`` prefix) so names containing ``:`` or
  starting with ``-`` are not read as protocols or options.
* stderr goes to a log file. On failure, ``FFMPEG_FAILED`` carries the last stderr line.
* Progress comes from ``-progress pipe:1``.
* Version and features are detected once per process.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from videocli.core import progress
from videocli.core.errors import DependencyError, RuntimeFailure

MIN_VERSION = (5, 0)

BASE_FILTERS = ("silencedetect", "ebur128", "loudnorm", "afade", "atempo", "scale", "subtitles", "blackdetect", "freezedetect")
BASE_ENCODERS = ("libx264", "aac")
BASE_DEMUXERS = ("concat",)
FINISHING_FILTERS = (
    "lut3d", "zscale", "overlay", "xfade", "acrossfade", "sidechaincompress", "signalstats", "maskedmerge", "vidstabdetect",
)
ALPHA_ENCODERS = ("qtrle", "prores_ks", "libvpx-vp9")

_log_dir: Path | None = None
_paths: dict[str, str] = {}


def set_log_dir(path: Path | None) -> None:
    global _log_dir
    _log_dir = path


def set_paths(ffmpeg: str | None = None, ffprobe: str | None = None) -> None:
    """Explicit paths from settings (``ffmpeg``/``ffprobe`` keys)."""
    if ffmpeg:
        _paths["ffmpeg"] = ffmpeg
    if ffprobe:
        _paths["ffprobe"] = ffprobe
    locate.cache_clear()


def log_dir() -> Path:
    if _log_dir is not None:
        _log_dir.mkdir(parents=True, exist_ok=True)
        return _log_dir
    from platformdirs import user_cache_dir

    p = Path(user_cache_dir("videocli", appauthor=False)) / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def new_log_path(name: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name)[:60]
    return log_dir() / f"{safe}-{stamp}.log"


def fpath(path: str | os.PathLike[str]) -> str:
    """``file:`` prefixed absolute path for ffmpeg arguments."""
    return "file:" + str(Path(path).resolve())


# --------------------------------------------------------------------------- locate


def parse_version(text: str) -> tuple[int, ...] | None:
    """Parse the first line of ``ffmpeg -version``. Git builds count as very new."""
    m = re.search(r"version\s+n?(\d+)\.(\d+)(?:\.(\d+))?", text)
    if m:
        return tuple(int(x) for x in m.groups() if x is not None)
    if re.search(r"version\s+N-\d+", text) or re.search(r"version\s+[0-9a-f]{7,}", text):
        return (999, 0)
    return None


def _version_of(binary: str) -> tuple[str, tuple[int, ...] | None]:
    try:
        out = subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return "", None
    first = (out.stdout or "").splitlines()[0] if out.stdout else ""
    return first, parse_version(first)


def provisioned_dir() -> Path:
    from platformdirs import user_cache_dir

    return Path(user_cache_dir("videocli", appauthor=False)) / "ffmpeg"


@dataclass(frozen=True)
class Tool:
    name: str
    path: str
    version: str
    version_tuple: tuple[int, ...]
    origin: str  # env | settings | path | provisioned


def _exe(name: str) -> str:
    return name + (".exe" if sys.platform == "win32" else "")


@functools.lru_cache(maxsize=4)
def locate(name: str = "ffmpeg") -> Tool:
    """Find ffmpeg or ffprobe: env var → settings → PATH (≥ 5.0) → provisioned build."""
    candidates: list[tuple[str, str]] = []
    env = os.environ.get(f"VIDEOCLI_{name.upper()}")
    if env:
        candidates.append((env, "env"))
    if _paths.get(name):
        candidates.append((_paths[name], "settings"))
    on_path = shutil.which(name)
    if on_path:
        candidates.append((on_path, "path"))
    prov = provisioned_dir() / _exe(name)
    if prov.is_file():
        candidates.append((str(prov), "provisioned"))

    too_old: Tool | None = None
    for path, origin in candidates:
        first, ver = _version_of(path)
        if ver is None:
            if origin in ("env", "settings"):
                raise DependencyError(
                    "FFMPEG_NOT_FOUND",
                    f"{name} at {path} (from {origin}) did not run.",
                    hint=f"Fix VIDEOCLI_{name.upper()} or the '{name}' setting.",
                )
            continue
        tool = Tool(name, path, first.split(" Copyright")[0].strip(), ver, origin)
        if ver[:2] < MIN_VERSION:
            too_old = too_old or tool
            continue
        return tool
    if too_old:
        raise DependencyError(
            "FFMPEG_TOO_OLD",
            f"{name} {'.'.join(map(str, too_old.version_tuple))} at {too_old.path} is older than {'.'.join(map(str, MIN_VERSION))}.",
            hint="Run: videocli setup   (downloads a pinned build), or install a newer ffmpeg.",
        )
    raise DependencyError(
        "FFMPEG_NOT_FOUND",
        f"No {name} found on PATH or in the videocli cache.",
        hint="Install ffmpeg (for example: apt install ffmpeg, brew install ffmpeg, winget install ffmpeg), or run: videocli setup",
    )


# --------------------------------------------------------------------------- capabilities


@dataclass
class Capabilities:
    version: str
    filters: set[str] = field(default_factory=set)
    encoders: set[str] = field(default_factory=set)
    decoders: set[str] = field(default_factory=set)
    demuxers: set[str] = field(default_factory=set)
    muxers: set[str] = field(default_factory=set)
    x264_10bit: bool = False

    def missing(self, filters=(), encoders=(), demuxers=()) -> list[str]:
        out = [f"filter:{f}" for f in filters if f not in self.filters]
        out += [f"encoder:{e}" for e in encoders if e not in self.encoders]
        out += [f"demuxer:{d}" for d in demuxers if d not in self.demuxers]
        return out

    def base_missing(self) -> list[str]:
        return self.missing(BASE_FILTERS, BASE_ENCODERS, BASE_DEMUXERS)

    def finishing_missing(self) -> list[str]:
        out = self.missing(FINISHING_FILTERS)
        if not self.x264_10bit and "libx265" not in self.encoders and "prores_ks" not in self.encoders:
            out.append("encoder:10-bit")
        if not any(e in self.encoders for e in ALPHA_ENCODERS):
            out.append("encoder:alpha")
        return out


def _list(binary: str, flag: str) -> str:
    try:
        return subprocess.run([binary, "-hide_banner", flag], capture_output=True, text=True, timeout=30, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


_FILTER_RE = re.compile(r"^ [T.][S.][C.] (\S+)\s+\S*->\S*")
_CODEC_RE = re.compile(r"^ [VAS][F.][S.][X.][B.][D.] (\S+)")


def _parse_filters(text: str) -> set[str]:
    return {m.group(1) for line in text.splitlines() if (m := _FILTER_RE.match(line))}


def _parse_codecs(text: str) -> set[str]:
    """Names from ``-encoders``/``-decoders`` (rows after the ``------`` separator)."""
    out: set[str] = set()
    started = False
    for line in text.splitlines():
        if not started:
            started = line.strip().startswith("---")
            continue
        m = _CODEC_RE.match(line)
        if m:
            out.add(m.group(1))
    return out


@functools.lru_cache(maxsize=2)
def capabilities() -> Capabilities:
    tool = locate("ffmpeg")
    caps = Capabilities(version=tool.version)
    caps.filters = _parse_filters(_list(tool.path, "-filters"))
    caps.encoders = _parse_codecs(_list(tool.path, "-encoders"))
    caps.decoders = _parse_codecs(_list(tool.path, "-decoders"))
    fmt = _list(tool.path, "-formats")
    for line in fmt.splitlines():
        m = re.match(r"^\s([D ])([E ])d?\s+(\S+)", line)
        if m:
            for n in m.group(3).split(","):
                if m.group(1) == "D":
                    caps.demuxers.add(n)
                if m.group(2) == "E":
                    caps.muxers.add(n)
    if "libx264" in caps.encoders:
        try:
            info = subprocess.run(
                [tool.path, "-hide_banner", "-h", "encoder=libx264"], capture_output=True, text=True, timeout=20, check=False
            ).stdout
        except (OSError, subprocess.SubprocessError):
            info = ""
        caps.x264_10bit = "yuv420p10le" in info
    return caps


def require_filters(*names: str) -> None:
    caps = capabilities()
    missing = [n for n in names if n not in caps.filters]
    if missing:
        raise DependencyError(
            "FFMPEG_TOO_OLD",
            f"This ffmpeg lacks required filter(s): {', '.join(missing)}.",
            hint="Run: videocli doctor",
        )


# --------------------------------------------------------------------------- run


@dataclass
class RunResult:
    returncode: int
    log: Path
    stderr: str = ""
    stdout: bytes = b""


_PROGRESS_KEYS = ("out_time_us", "out_time_ms")


def _last_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line and not line.startswith(("frame=", "size=", "progress=")):
            return line
    return ""


def run(
    args: list[str],
    *,
    log_name: str,
    progress_total: float | None = None,
    capture: bool = False,
    stdin: bytes | None = None,
    check: bool = True,
    tool: str = "ffmpeg",
    timeout: float | None = None,
    cwd: Path | None = None,
    stdin_iter: Any = None,
) -> RunResult:
    """Run ffmpeg (or ffprobe) with ``args``.

    ``progress_total`` (seconds of output) enables progress events. ``capture`` returns
    stdout bytes and stderr text as well as writing the log.
    """
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise TypeError("ffmpeg arguments must be a list of str")
    binary = locate(tool).path
    cmd = [binary, "-hide_banner"]
    if tool == "ffmpeg":
        cmd.append("-nostdin" if (stdin is None and stdin_iter is None) else "-nostats")
        if progress_total:
            cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += args
    log = new_log_path(log_name)
    with open(log, "w", encoding="utf-8", errors="replace") as lf:
        lf.write("$ " + " ".join(_quote(a) for a in cmd) + "\n\n")
        lf.flush()
        stderr_chunks: list[str] = []
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if (stdin is not None or stdin_iter is not None) else subprocess.DEVNULL,
            stdout=subprocess.PIPE if (capture or progress_total) else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
        )

        def pump_err() -> None:
            assert proc.stderr is not None
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", errors="replace")
                lf.write(line)
                if capture or len(stderr_chunks) < 4000:
                    stderr_chunks.append(line)
                elif len(stderr_chunks) >= 4000:
                    stderr_chunks.pop(0)
                    stderr_chunks.append(line)

        t_err = threading.Thread(target=pump_err, daemon=True)
        t_err.start()
        out_bytes = b""
        try:
            if stdin is not None:
                assert proc.stdin is not None
                try:
                    proc.stdin.write(stdin)
                    proc.stdin.close()
                except BrokenPipeError:
                    pass
            elif stdin_iter is not None:
                assert proc.stdin is not None
                try:
                    for chunk in stdin_iter:
                        proc.stdin.write(chunk)
                    proc.stdin.close()
                except BrokenPipeError:
                    pass
            if progress_total and proc.stdout is not None:
                buf = []
                for raw in iter(proc.stdout.readline, b""):
                    line = raw.decode("utf-8", errors="replace").strip()
                    if capture:
                        buf.append(raw)
                    k, _, v = line.partition("=")
                    if k in _PROGRESS_KEYS and v.strip().lstrip("-").isdigit():
                        secs = int(v) / 1_000_000
                        progress.update(secs / progress_total)
                    elif k == "progress" and v == "end":
                        progress.update(1.0)
                out_bytes = b"".join(buf)
            elif capture and proc.stdout is not None:
                out_bytes = proc.stdout.read()
            rc = proc.wait(timeout=timeout)
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        finally:
            t_err.join(timeout=5)
    stderr_text = "".join(stderr_chunks)
    if check and rc != 0:
        last = _last_line(stderr_text) or f"exit status {rc}"
        raise RuntimeFailure(
            "FFMPEG_FAILED",
            f"{tool} exited with status {rc}: {last}",
            hint="See the log named in details.",
            details={"log": str(log), "returncode": rc},
        )
    return RunResult(rc, log, stderr_text if capture else stderr_text[-20000:], out_bytes)


def _quote(a: str) -> str:
    return a if re.fullmatch(r"[A-Za-z0-9_./:=,+@%-]+", a) else "'" + a.replace("'", "'\\''") + "'"


def run_probe(args: list[str], *, log_name: str = "ffprobe") -> dict[str, Any]:
    """Run ffprobe with ``-of json`` and return the parsed document."""
    res = run(["-v", "error", "-of", "json", *args], log_name=log_name, capture=True, tool="ffprobe")
    try:
        return json.loads(res.stdout.decode("utf-8", errors="replace") or "{}")
    except ValueError as exc:
        raise RuntimeFailure("FFMPEG_FAILED", f"ffprobe returned invalid JSON: {exc}", details={"log": str(res.log)}) from exc
