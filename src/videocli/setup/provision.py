"""Download a pinned ffmpeg build into the user cache, with a checksum check (SPEC §4.3).

Which build to pin per platform is decision X1 (deferred until each build is confirmed to
have every feature in SPEC §4.3). Until a checksum is pinned for a platform, provisioning
refuses rather than install an unverified binary.
"""

from __future__ import annotations

import hashlib
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from videocli.core.errors import DependencyError, RuntimeFailure
from videocli.media.ffmpeg import _exe, locate, provisioned_dir


@dataclass(frozen=True)
class Build:
    url: str
    sha256: str | None  # None: not pinned yet (decision X1)
    archive: str  # "tar.xz" | "zip"
    members: tuple[str, str]  # paths of ffmpeg and ffprobe inside the archive (suffix match)


PINNED: dict[str, Build] = {
    "linux-x86_64": Build(
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n7.1-latest-linux64-gpl-7.1.tar.xz",
        None, "tar.xz", ("bin/ffmpeg", "bin/ffprobe"),
    ),
    "linux-aarch64": Build(
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n7.1-latest-linuxarm64-gpl-7.1.tar.xz",
        None, "tar.xz", ("bin/ffmpeg", "bin/ffprobe"),
    ),
    "windows-amd64": Build(
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n7.1-latest-win64-gpl-7.1.zip",
        None, "zip", ("bin/ffmpeg.exe", "bin/ffprobe.exe"),
    ),
}


def platform_key() -> str:
    machine = platform.machine().lower()
    return f"{sys.platform.replace('win32', 'windows').replace('darwin', 'macos')}-{machine}"


def provision(*, dry_run: bool = False) -> dict:
    """Ensure ffmpeg/ffprobe exist. Returns a description of what was (or would be) done."""
    try:
        ff = locate("ffmpeg")
        fp = locate("ffprobe")
        return {"action": "none", "ffmpeg": ff.path, "ffprobe": fp.path, "origin": ff.origin}
    except DependencyError:
        pass
    key = platform_key()
    build = PINNED.get(key)
    if build is None or build.sha256 is None:
        raise DependencyError(
            "FFMPEG_NOT_FOUND",
            f"No usable ffmpeg was found, and no checksum-pinned build is available for {key} yet.",
            hint="Install ffmpeg 5.0 or newer with your package manager (apt install ffmpeg, brew install ffmpeg, "
            "winget install ffmpeg), or point VIDEOCLI_FFMPEG and VIDEOCLI_FFPROBE at a build.",
            details={"platform": key},
        )
    if dry_run:
        return {"action": "download", "url": build.url, "to": str(provisioned_dir())}
    dest = provisioned_dir()
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        archive = Path(td) / ("ffmpeg." + build.archive)
        try:
            with urllib.request.urlopen(build.url, timeout=60) as resp, open(archive, "wb") as fh:
                shutil.copyfileobj(resp, fh)
        except OSError as exc:
            raise RuntimeFailure("DOWNLOAD_FAILED", f"Downloading ffmpeg failed: {exc}", retryable=True) from exc
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != build.sha256:
            raise RuntimeFailure(
                "DOWNLOAD_FAILED", "Downloaded ffmpeg did not match its pinned checksum.",
                details={"expected": build.sha256, "actual": digest},
            )
        _extract(archive, build, dest)
    locate.cache_clear()
    return {"action": "downloaded", "url": build.url, "to": str(dest)}


def _extract(archive: Path, build: Build, dest: Path) -> None:
    wanted = {m: dest / _exe(Path(m).stem) for m in build.members}
    if build.archive == "zip":
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                for suffix, target in wanted.items():
                    if info.filename.endswith(suffix):
                        target.write_bytes(zf.read(info))
    else:
        with tarfile.open(archive, "r:*") as tf:
            for member in tf.getmembers():
                for suffix, target in wanted.items():
                    if member.name.endswith(suffix) and member.isfile():
                        src = tf.extractfile(member)
                        if src:
                            target.write_bytes(src.read())
    for target in wanted.values():
        if not target.is_file():
            raise RuntimeFailure("DOWNLOAD_FAILED", f"Archive did not contain {target.name}.")
        target.chmod(0o755)
