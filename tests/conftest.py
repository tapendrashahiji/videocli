"""Shared fixtures: an isolated environment, a CLI runner and synthetic media from lavfi."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """No user config, no workspace leakage, and JSON output for every test."""
    for k in list(os.environ):
        if k.startswith("VIDEOCLI_"):
            monkeypatch.delenv(k, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
    monkeypatch.setenv("VIDEOCLI_OUTPUT", "json")
    monkeypatch.chdir(tmp_path)
    from videocli.media import ffmpeg

    ffmpeg.set_log_dir(None)
    ffmpeg.locate.cache_clear()
    yield


class Runner:
    def __init__(self, capsys):
        self.capsys = capsys

    def __call__(self, *args: str) -> tuple[int, dict]:
        from videocli.cli.app import main

        self.capsys.readouterr()
        code = main(list(args))
        out = self.capsys.readouterr().out
        lines = [line for line in out.splitlines() if line.strip()]
        assert len(lines) == 1, f"expected exactly one JSON document on stdout, got: {out!r}"
        return code, json.loads(lines[0])

    def ok(self, *args: str) -> dict:
        code, env = self(*args)
        assert code == 0 and env["ok"], env
        return env["data"]


@pytest.fixture
def cli(capsys):
    return Runner(capsys)


# --------------------------------------------------------------------------- media

MEDIA_CACHE: dict[str, Path] = {}


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("media")


def make(media_dir: Path, name: str, *args: str) -> Path:
    p = media_dir / name
    if not p.exists():
        ff(*args, str(p))
    return p


@pytest.fixture(scope="session")
def av_clip(media_dir) -> Path:
    return make(
        media_dir, "av.mp4",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
    )


@pytest.fixture(scope="session")
def audio_only(media_dir) -> Path:
    return make(media_dir, "tone.wav", "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=16000", "-t", "2")


@pytest.fixture(scope="session")
def video_only(media_dir) -> Path:
    return make(media_dir, "silent.mp4", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=30", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p")


@pytest.fixture(scope="session")
def rotated_clip(media_dir, video_only) -> Path:
    p = media_dir / "rotated.mp4"
    if not p.exists():
        ff("-display_rotation", "90", "-i", str(video_only), "-c", "copy", str(p))
    return p


@pytest.fixture(scope="session")
def tenbit_clip(media_dir) -> Path:
    return make(media_dir, "tenbit.mp4", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=24", "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p10le")


@pytest.fixture(scope="session")
def hlg_clip(media_dir) -> Path:
    return make(
        media_dir, "hlg.mp4", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=25", "-t", "1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p10le",
        "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc", "-color_range", "tv",
    )


@pytest.fixture(scope="session")
def counter_clip(media_dir) -> Path:
    """5 s at 25 fps; frame N carries N as 8 black/white stripes (bit k = stripe k), plus a 440 Hz tone."""
    p = media_dir / "counter.mkv"
    if not p.exists():
        ff(
            "-f", "lavfi", "-i", "color=c=black:s=64x64:r=25:d=5,format=yuv420p,geq=lum='if(bitand(N,pow(2,floor(X/8))),235,16)':cb=128:cr=128",
            "-f", "lavfi", "-i", "sine=f=440:sample_rate=48000:d=5",
            "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", "-g", "10", "-c:a", "pcm_s16le", str(p),
        )
    return p


def frame_values(path: Path) -> list[int]:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf", "scale=64:64,format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    ).stdout
    frames = np.frombuffer(out, dtype=np.uint8).reshape(-1, 64, 64)
    vals = []
    for f in frames:
        stripes = [f[16:48, k * 8 + 2: k * 8 + 6].mean() > 128 for k in range(8)]
        vals.append(sum(1 << k for k, on in enumerate(stripes) if on))
    return vals


def stream_durations(path: Path) -> dict[str, float]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(path)],
                         capture_output=True, check=True, text=True).stdout
    return {s["codec_type"]: float(s["duration"]) for s in json.loads(out)["streams"]}


