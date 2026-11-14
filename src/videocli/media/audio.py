"""Audio extraction and PCM helpers."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from videocli.media import ffmpeg

ANALYSIS_RATE = 16000


def extract_analysis_wav(src_path: Path, out: Path, *, stream: int = 0, duration: float | None = None) -> Path:
    """Extract audio stream ``stream`` as 16 kHz mono 16-bit WAV (atomic)."""
    tmp = out.with_name(out.stem + ".part.wav")
    args = [
        "-y", "-i", ffmpeg.fpath(src_path), "-map", f"0:a:{stream}", "-vn", "-sn", "-dn",
        "-ac", "1", "-ar", str(ANALYSIS_RATE), "-c:a", "pcm_s16le", "-f", "wav", ffmpeg.fpath(tmp),
    ]
    ffmpeg.run(args, log_name="extract-audio", progress_total=duration)
    tmp.replace(out)
    return out


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Read a 16-bit PCM WAV into float32 samples in [-1, 1] (mono: 1-D; else frames × channels)."""
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit PCM, got {width * 8}-bit")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch)
    return data, rate


def decode_pcm(src_path: Path, *, start: float = 0.0, duration: float | None = None, stream: int = 0,
               rate: int = 48000, channels: int = 1) -> np.ndarray:
    """Decode a window of audio to float32 via ffmpeg (for checks such as click detection)."""
    args = ["-ss", f"{start:.6f}", "-i", ffmpeg.fpath(src_path)]
    if duration is not None:
        args += ["-t", f"{duration:.6f}"]
    args += ["-map", f"0:a:{stream}", "-ac", str(channels), "-ar", str(rate), "-f", "f32le", "-c:a", "pcm_f32le", "pipe:1"]
    res = ffmpeg.run(args, log_name="decode-pcm", capture=True)
    data = np.frombuffer(res.stdout, dtype="<f4")
    return data.reshape(-1, channels) if channels > 1 else data
