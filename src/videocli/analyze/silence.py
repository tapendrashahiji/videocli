"""Analyzer ``silence``: ffmpeg ``silencedetect`` on the analysis audio; speech is the complement."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from videocli.analyze.base import Availability, RunContext, register
from videocli.core.cache import write_artifact
from videocli.core.source import Source
from videocli.core.timeutil import Range, complement, ms, normalize
from videocli.media import ffmpeg

_START = re.compile(r"silence_start:\s*(-?[\d.]+(?:e-?\d+)?)")
_END = re.compile(r"silence_end:\s*(-?[\d.]+(?:e-?\d+)?)")


def parse_silencedetect(stderr: str, duration: float) -> list[Range]:
    """Parse ``silence_start`` / ``silence_end`` lines. An open silence runs to ``duration``."""
    out: list[Range] = []
    start: float | None = None
    for line in stderr.splitlines():
        if "silencedetect" not in line and "silence_" not in line:
            continue
        m = _START.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = _END.search(line)
        if m:
            end = min(float(m.group(1)), duration) if duration else float(m.group(1))
            s = 0.0 if start is None else start
            if end > s:
                out.append((ms(s), ms(end)))
            start = None
    if start is not None and duration and duration > start:
        out.append((ms(start), ms(duration)))
    return normalize(out)


def detect(wav: Path, duration: float, threshold_db: float, min_duration: float) -> list[Range]:
    res = ffmpeg.run(
        ["-i", ffmpeg.fpath(wav), "-af", f"silencedetect=noise={threshold_db}dB:d={min_duration}", "-f", "null", "-"],
        log_name="silencedetect",
        capture=True,
        progress_total=duration or None,
    )
    return parse_silencedetect(res.stderr, duration)


class SilenceAnalyzer:
    name = "silence"
    version = 1
    requires: tuple[str, ...] = ("audio",)
    schema = "videocli.silence/v1"
    needs_audio = True
    needs_video = False

    def params(self, ctx: RunContext) -> dict[str, Any]:
        s = ctx.settings
        return {
            "threshold_db": float(ctx.options.get("silence_threshold") or s.silence__threshold_db),
            "min_duration": float(ctx.options.get("silence_min") or s.silence__min_duration),
        }

    def available(self, ctx: RunContext) -> Availability:
        return Availability.yes()

    def run(self, src: Source, params: dict[str, Any], ctx: RunContext) -> Path:
        wav = ctx.ws.source_dir(src.id) / "audio.wav"
        duration = float(ctx.probe.duration if ctx.probe else 0.0)
        silences = detect(wav, duration, params["threshold_db"], params["min_duration"])
        speech = complement(silences, 0.0, duration)
        out = ctx.artifact_path(src, self.name)
        clean = {k: v for k, v in params.items() if not k.startswith("_")}
        sil_total = sum(b - a for a, b in silences)
        write_artifact(
            out, schema=self.schema, key=params["_key"], params=clean,
            body={
                "source_id": src.id,
                "duration": duration,
                "silence_seconds": ms(sil_total),
                "speech_seconds": ms(duration - sil_total),
                "silences": [{"start": a, "end": b, "duration": ms(b - a)} for a, b in silences],
                "speech": [{"start": ms(a), "end": ms(b)} for a, b in speech],
            },
        )
        return out


register(SilenceAnalyzer())
