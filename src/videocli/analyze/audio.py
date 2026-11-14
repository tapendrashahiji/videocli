"""Analyzer ``audio``: the chosen audio stream as 16 kHz mono 16-bit WAV in the workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from videocli.analyze.base import Availability, RunContext, register
from videocli.core.cache import write_artifact
from videocli.core.errors import InputError, UsageError
from videocli.core.source import Source
from videocli.media.audio import ANALYSIS_RATE, extract_analysis_wav


class AudioAnalyzer:
    name = "audio"
    version = 1
    requires: tuple[str, ...] = ("probe",)
    schema = "videocli.audio/v1"
    needs_audio = True
    needs_video = False

    def params(self, ctx: RunContext) -> dict[str, Any]:
        return {"stream": int(ctx.options.get("audio_stream") or 0), "rate": ANALYSIS_RATE}

    def available(self, ctx: RunContext) -> Availability:
        stream = int(ctx.options.get("audio_stream") or 0)
        if ctx.probe is not None and stream >= len(ctx.probe.audio):
            if not ctx.probe.audio:
                return Availability(False, InputError("NO_AUDIO", f"{ctx.probe.path} has no audio stream."))
            return Availability(
                False,
                UsageError(
                    "BAD_ARGUMENT",
                    f"Audio stream {stream} does not exist (the source has {len(ctx.probe.audio)}).",
                    hint="Use --audio-stream with an index from videocli probe.",
                ),
            )
        return Availability.yes()

    def run(self, src: Source, params: dict[str, Any], ctx: RunContext) -> Path:
        d = ctx.ws.source_dir(src.id)
        wav = d / "audio.wav"
        duration = ctx.probe.duration if ctx.probe else None
        extract_analysis_wav(src.path, wav, stream=params["stream"], duration=duration)
        out = ctx.artifact_path(src, self.name)
        clean = {k: v for k, v in params.items() if not k.startswith("_")}
        write_artifact(
            out, schema=self.schema, key=params["_key"], params=clean,
            body={"source_id": src.id, "file": "audio.wav", "sample_rate": ANALYSIS_RATE, "channels": 1, "stream": params["stream"], "duration": duration},
        )
        return out


register(AudioAnalyzer())
