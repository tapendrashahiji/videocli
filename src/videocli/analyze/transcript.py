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
        from videocli.media.audio import read_wav

        # Hand Whisper the samples ourselves: faster-whisper's own file decoding goes through PyAV,
        # whose API changes between releases (av 19 dropped open(metadata_errors=...)).
        audio, _rate = read_wav(Path(wav))
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        device, compute = self._device()
        try:
            return self._run(audio, model, device, compute, language, initial_prompt, on_progress)
        except RuntimeError as exc:
            # A GPU without its CUDA runtime (libcublas, cuDNN) fails only once work starts.
            # With device "auto", fall back to the CPU instead of failing the whole analysis.
            if device != "cuda" or self.settings.transcript__device != "auto" or not _is_cuda_runtime_error(exc):
                raise
            self.fallback = f"GPU unavailable ({str(exc).splitlines()[0]}); transcribed on the CPU"
            compute = "int8" if self.settings.transcript__compute_type == "auto" else self.settings.transcript__compute_type
            return self._run(audio, model, "cpu", compute, language, initial_prompt, on_progress)

    def _run(self, audio, model, device, compute, language, initial_prompt, on_progress):
        if device == "cuda":
            preload_cuda_libraries()
        from faster_whisper import WhisperModel

        from videocli.setup.profiles import whisper_model_path

        path = whisper_model_path(model)
        jobs = int(getattr(self.settings, "jobs", 4))
        wm = WhisperModel(str(path) if path else model, device=device, compute_type=compute, cpu_threads=jobs, local_files_only=True)
        segments, info = wm.transcribe(
            audio, language=language or None, initial_prompt=initial_prompt or None,
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
        return {"language": info.language, "language_probability": info.language_probability, "segments": out, "device": device}


def nvidia_library_dirs() -> list[Path]:
    """lib folders of NVIDIA's pip wheels (nvidia-cublas-cu12, nvidia-cudnn-cu12) in this environment."""
    import importlib.util

    dirs = []
    for pkg in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_runtime", "nvidia.cuda_nvrtc"):
        try:
            spec = importlib.util.find_spec(pkg)
        except (ImportError, ValueError):
            spec = None
        if spec and spec.submodule_search_locations:
            for loc in spec.submodule_search_locations:
                lib = Path(loc) / ("bin" if __import__("sys").platform == "win32" else "lib")
                if lib.is_dir():
                    dirs.append(lib)
    return dirs


_PRELOADED = False


def preload_cuda_libraries() -> list[str]:
    """Load cuBLAS/cuDNN from NVIDIA's pip wheels so CTranslate2 finds them without LD_LIBRARY_PATH.

    CTranslate2 opens ``libcublas.so.12`` and ``libcudnn*.so.9`` by name; a library already loaded
    with that name is reused, so loading the wheel copies first is enough.
    """
    global _PRELOADED
    if _PRELOADED:
        return []
    import ctypes
    import sys

    loaded = []
    for d in nvidia_library_dirs():
        if sys.platform == "win32":  # pragma: no cover
            import os

            os.add_dll_directory(str(d))
            loaded.append(str(d))
            continue
        # dependencies first: cudart, cublasLt, cublas, then cudnn (whose sub-libraries load lazily)
        order = ["libcudart", "libnvrtc", "libcublasLt", "libcublas", "libcudnn"]
        libs = sorted(d.glob("lib*.so*"), key=lambda p: next((i for i, k in enumerate(order) if p.name.startswith(k)), 99))
        for lib in libs:
            try:
                ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
                loaded.append(lib.name)
            except OSError:
                pass
    _PRELOADED = True
    return loaded


def _is_cuda_runtime_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("libcublas", "libcudnn", "cudnn", "cuda", "cublas", "libcudart"))


#: Factory for the local transcription backend; tests replace it.
BACKEND: Callable[[Any], Transcriber] = FasterWhisper

WHISPER_LADDER = ("tiny", "base", "small", "medium", "large-v3")
WHISPER_PREFIXES = ("tiny", "base", "small", "medium", "large", "distil", "turbo")


def low_confidence_hint(model: str, backend: str) -> str:
    """What to try next when many words are uncertain; never suggests a smaller model."""
    cloud = "or try cloud transcription: videocli keys set elevenlabs, then videocli analyze FILE --backend elevenlabs --force"
    if backend == "elevenlabs":
        return "check the language (--language CODE); mixed-language speech is often transcribed with lower confidence."
    name = model.split("/")[-1].replace(".en", "")
    if name in WHISPER_LADDER[:-1]:
        bigger = WHISPER_LADDER[WHISPER_LADDER.index(name) + 1]
        return f"check the language setting (--language CODE) or try a larger model (--model {bigger})."
    return f"you are on the largest local model; set --language, add names and jargon with --prompt, {cloud}."


def make_backend(name: str, settings: Any) -> Transcriber:
    if name == "elevenlabs":
        from videocli.analyze.elevenlabs import ElevenLabs

        return ElevenLabs(settings)
    return BACKEND(settings)


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
        backend = ctx.options.get("backend") or s.transcript__backend
        p: dict[str, Any] = {
            "model": ctx.options.get("model") or s.transcript__model,
            "language": ctx.options.get("language") or s.transcript__language or None,
            "initial_prompt": ctx.options.get("initial_prompt") or None,
        }
        if backend != "whisper":  # only added when set, so existing whisper transcripts keep their cache keys
            p["backend"] = backend
            if backend == "elevenlabs":
                asked = str(ctx.options.get("model") or "")
                p["model"] = asked if asked and not asked.startswith(WHISPER_PREFIXES) else s.transcript__elevenlabs_model
        return p

    def available(self, ctx: RunContext) -> Availability:
        from videocli.core.errors import UsageError

        backend = self.params(ctx).get("backend", "whisper")
        if backend not in ("whisper", "elevenlabs"):
            return Availability(False, UsageError("BAD_ARGUMENT", f"Unknown transcript backend '{backend}'.", hint="Use whisper or elevenlabs."))
        if backend == "elevenlabs":
            from videocli.setup import keys

            if not keys.get("elevenlabs"):
                return Availability(False, DependencyError(
                    "API_KEY_MISSING", "ElevenLabs transcription needs an API key.",
                    hint="A person runs (the key is typed, never shown): videocli keys set elevenlabs",
                ))
            return Availability.yes()
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
        backend_name = params.get("backend", "whisper")
        backend = make_backend(backend_name, ctx.settings)
        raw = backend.transcribe(
            wav, model=params["model"], language=params["language"], initial_prompt=params["initial_prompt"],
            on_progress=lambda f: progress.update(f),
        )
        if getattr(backend, "fallback", None):
            ctx.warn("GPU_FALLBACK", backend.fallback + ". Install the CUDA 12 libraries to use the GPU, or set: videocli config set transcript.device cpu")
        segments = clean_segments(raw.get("segments", []), duration)
        words = [w for s in segments for w in s["words"]]
        low = float(ctx.settings.transcript__low_confidence)
        low_share = (sum(1 for w in words if w["p"] < low) / len(words)) if words else 0.0
        if words and low_share > float(ctx.settings.transcript__low_confidence_share):
            ctx.warn(
                "LOW_CONFIDENCE_WORDS",
                f"{low_share:.0%} of words have probability below {low}; {low_confidence_hint(params['model'], backend_name)}",
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
                "backend": backend_name,
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
