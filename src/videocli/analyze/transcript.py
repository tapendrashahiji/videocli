"""Analyzer ``transcript``: faster-whisper with word timestamps and a voice-activity filter.

The speech model sits behind :class:`Transcriber`, so unit tests swap in a fake through
:data:`BACKEND`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from videocli.analyze.base import Availability, RunContext, register
from videocli.core import progress
from videocli.core.cache import write_artifact
from videocli.core.errors import DependencyError
from videocli.core.source import Source
from videocli.core.timeutil import ms

MIN_WORD = 0.02


class Transcriber(Protocol):
    def transcribe(
        self, wav: Path, *, model: str, language: str | None, initial_prompt: str | None,
        on_progress: Callable[[float], None],
    ) -> dict[str, Any]:
        """Return ``{"language", "language_probability", "segments": [{start, end, text, words: [{start, end, word, probability}]}]}``."""
        ...


class FasterWhisper:
    def __init__(self, settings: Any):
        self.settings = settings

    def _device(self) -> tuple[str, str]:
        device = self.settings.transcript__device
        compute = self.settings.transcript__compute_type
        if device == "auto":
            try:
                import ctranslate2

                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                device = "cpu"
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        return device, compute

    def transcribe(self, wav, *, model, language, initial_prompt, on_progress):
        from faster_whisper import WhisperModel

        from videocli.setup.profiles import whisper_model_path

        path = whisper_model_path(model)
        device, compute = self._device()
        jobs = int(getattr(self.settings, "jobs", 4))
        wm = WhisperModel(str(path) if path else model, device=device, compute_type=compute, cpu_threads=jobs, local_files_only=True)
        segments, info = wm.transcribe(
            str(wav), language=language or None, initial_prompt=initial_prompt or None,
            word_timestamps=True, vad_filter=True, beam_size=5,
        )
        total = float(info.duration or 0.0) or 1.0
        out = []
        for seg in segments:
            out.append({
                "start": seg.start, "end": seg.end, "text": seg.text,
                "words": [{"start": w.start, "end": w.end, "word": w.word, "probability": w.probability} for w in (seg.words or [])],
            })
            on_progress(seg.end / total)
        return {"language": info.language, "language_probability": info.language_probability, "segments": out}


#: Factory for the transcription backend; tests replace it.
BACKEND: Callable[[Any], Transcriber] = FasterWhisper


def clean_segments(raw: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    """Clamp times to the file, fix zero-length words, drop empty words, keep order."""
    segs = []
    for seg in raw:
        words = []
        for w in seg.get("words") or []:
            text = str(w.get("word", "")).strip()
            if not text:
                continue
            a = max(0.0, float(w["start"]))
            b = float(w["end"])
            if duration:
                a = min(a, duration)
                b = min(b, duration)
            if b - a < MIN_WORD:
                b = min(a + MIN_WORD, duration) if duration else a + MIN_WORD
                if b - a < MIN_WORD:  # word at the very end of the file
                    a = max(0.0, b - MIN_WORD)
            words.append({"start": ms(a), "end": ms(b), "word": text, "p": round(float(w.get("probability", 1.0)), 3)})
        words.sort(key=lambda w: w["start"])
        for i in range(1, len(words)):  # no overlaps between consecutive words
            if words[i]["start"] < words[i - 1]["end"]:
                words[i - 1]["end"] = max(words[i - 1]["start"] + MIN_WORD, words[i]["start"])
        text = str(seg.get("text", "")).strip()
        if not words and not text:
            continue
        start = words[0]["start"] if words else max(0.0, float(seg["start"]))
        end = words[-1]["end"] if words else min(float(seg["end"]), duration or float(seg["end"]))
        segs.append({"id": len(segs), "start": ms(start), "end": ms(max(end, start)), "text": text, "words": words})
    return segs


class TranscriptAnalyzer:
    name = "transcript"
    version = 1
    requires: tuple[str, ...] = ("audio",)
    schema = "videocli.transcript/v1"
    needs_audio = True
    needs_video = False

    def params(self, ctx: RunContext) -> dict[str, Any]:
        s = ctx.settings
        return {
            "model": ctx.options.get("model") or s.transcript__model,
            "language": ctx.options.get("language") or s.transcript__language or None,
            "initial_prompt": ctx.options.get("initial_prompt") or None,
        }

    def available(self, ctx: RunContext) -> Availability:
        if BACKEND is not FasterWhisper:
            return Availability.yes()
        from videocli.setup.profiles import module_present, whisper_model_path

        if not module_present("faster_whisper"):
            return Availability(False, DependencyError(
                "FEATURE_NOT_INSTALLED", "Transcription needs faster-whisper (base group).", hint="Run: videocli setup",
            ))
        model = self.params(ctx)["model"]
        if whisper_model_path(model) is None:
            return Availability(False, DependencyError(
                "MODEL_NOT_FOUND",
                f"The speech model '{model}' is not downloaded.",
                hint="Run: videocli setup   (or skip transcription with --skip transcript)",
                details={"model": model},
            ))
        return Availability.yes()

    def run(self, src: Source, params: dict[str, Any], ctx: RunContext) -> Path:
        wav = ctx.ws.source_dir(src.id) / "audio.wav"
        duration = float(ctx.probe.duration if ctx.probe else 0.0)
        backend = BACKEND(ctx.settings)
        raw = backend.transcribe(
            wav, model=params["model"], language=params["language"], initial_prompt=params["initial_prompt"],
            on_progress=lambda f: progress.update(f),
        )
        segments = clean_segments(raw.get("segments", []), duration)
        words = [w for s in segments for w in s["words"]]
        low = float(ctx.settings.transcript__low_confidence)
        low_share = (sum(1 for w in words if w["p"] < low) / len(words)) if words else 0.0
        if words and low_share > float(ctx.settings.transcript__low_confidence_share):
            ctx.warn(
                "LOW_CONFIDENCE_WORDS",
                f"{low_share:.0%} of words have probability below {low}; check the language setting or try a larger model (--model medium).",
            )
        out = ctx.artifact_path(src, self.name)
        clean = {k: v for k, v in params.items() if not k.startswith("_")}
        write_artifact(
            out, schema=self.schema, key=params["_key"], params=clean,
            body={
                "source_id": src.id,
                "language": raw.get("language"),
                "language_probability": round(float(raw.get("language_probability") or 0.0), 3),
                "model": params["model"],
                "duration": duration,
                "word_count": len(words),
                "low_confidence_share": round(low_share, 3),
                "segments": segments,
            },
        )
        return out


register(TranscriptAnalyzer())


def iter_words(transcript: dict[str, Any]):
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []):
            yield seg["id"], w
