"""Phase 1: analyzers, cache behaviour, index, transcript views and search."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import ff
from videocli.analyze.silence import parse_silencedetect

SAMPLE_STDERR = """\
[silencedetect @ 0x55d0c8a0] silence_start: -0.00133333
[silencedetect @ 0x55d0c8a0] silence_end: 1.2 | silence_duration: 1.20133
size=N/A time=00:00:05.00 bitrate=N/A speed= 812x
[silencedetect @ 0x55d0c8a0] silence_start: 2.5
[silencedetect @ 0x55d0c8a0] silence_end: 3.75 | silence_duration: 1.25
[silencedetect @ 0x55d0c8a0] silence_start: 4.6
"""


def test_silence_parser_start_middle_end():
    assert parse_silencedetect(SAMPLE_STDERR, 5.0) == [(0.0, 1.2), (2.5, 3.75), (4.6, 5.0)]


def test_silence_parser_empty():
    assert parse_silencedetect("no silences here\n", 10.0) == []


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="session")
def tone_gap_tone(media_dir) -> Path:
    p = media_dir / "tgt.wav"
    if not p.exists():
        ff(
            "-f", "lavfi", "-i", "sine=f=440:sample_rate=48000:d=2",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=1.5",
            "-f", "lavfi", "-i", "sine=f=660:sample_rate=48000:d=2",
            "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", str(p),
        )
    return p


@pytest.fixture(scope="session")
def lecture_clip(media_dir) -> Path:
    """12 s of video with speech-like audio: tone 0-3, gap 3-5, tone 5-8, gap 8-9, tone 9-12."""
    p = media_dir / "lecture.mp4"
    if not p.exists():
        ff(
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:d=12",
            "-f", "lavfi", "-i", "sine=f=300:sample_rate=48000:d=12",
            "-filter_complex",
            "[1]volume='if(between(t,3,5)+between(t,8,9),0,1)':eval=frame[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "25", "-c:a", "aac", "-t", "12", str(p),
        )
    return p


FAKE_SEGMENTS = [
    {"start": 0.1, "end": 2.9, "text": " So today we will look at binary search.",
     "words": [{"start": 0.1 + i * 0.35, "end": 0.4 + i * 0.35, "word": w, "probability": 0.95}
               for i, w in enumerate(["So", "today", "we", "will", "look", "at", "binary", "search."])]},
    {"start": 5.1, "end": 7.9, "text": " Um, it works on a sorted array.",
     "words": [{"start": 5.1 + i * 0.4, "end": 5.4 + i * 0.4, "word": w, "probability": 0.9}
               for i, w in enumerate(["Um,", "it", "works", "on", "a", "sorted", "array."])]},
    {"start": 9.1, "end": 11.9, "text": " Binary",
     "words": [{"start": 11.5, "end": 11.9, "word": "Binary", "probability": 0.4}]},
    {"start": 11.9, "end": 12.0, "text": " search again.",
     "words": [{"start": 11.9, "end": 11.95, "word": "search", "probability": 0.8},
               {"start": 11.98, "end": 11.98, "word": "again.", "probability": 0.8}]},
]


class FakeTranscriber:
    calls = 0

    def __init__(self, settings):
        pass

    def transcribe(self, wav, *, model, language, initial_prompt, on_progress):
        FakeTranscriber.calls += 1
        assert Path(wav).is_file()
        on_progress(1.0)
        return {"language": language or "en", "language_probability": 0.99, "segments": json.loads(json.dumps(FAKE_SEGMENTS))}


@pytest.fixture
def fake_whisper(monkeypatch):
    from videocli.analyze import transcript

    FakeTranscriber.calls = 0
    monkeypatch.setattr(transcript, "BACKEND", FakeTranscriber)
    return FakeTranscriber


# --------------------------------------------------------------------------- analyzers


def test_tone_gap_tone_silence_within_50ms(cli, tone_gap_tone):
    d = cli.ok("analyze", str(tone_gap_tone), "--skip", "transcript")
    assert set(d["ran"]) >= {"audio", "silence", "loudness"}
    sil = cli.ok("silences", str(tone_gap_tone))
    assert sil["total"] == 1
    s = sil["silences"][0]
    assert s["start"] == pytest.approx(2.0, abs=0.05) and s["end"] == pytest.approx(3.5, abs=0.05)


def test_loudness_calibrated_tone(cli, media_dir):
    p = media_dir / "cal.wav"
    if not p.exists():
        ff("-f", "lavfi", "-i", "sine=f=997:sample_rate=48000:d=5", str(p))  # amplitude 1/8 → ≈ -21.07 LUFS (mono)
    cli.ok("analyze", str(p), "--only", "loudness")
    doc = json.loads(next(Path(".videocli/sources").glob("*/loudness.json")).read_text())
    assert doc["integrated_lufs"] == pytest.approx(-21.07, abs=0.5)
    assert doc["true_peak_db"] == pytest.approx(-18.06, abs=0.5)
    assert len(doc["momentary_per_second"]) in (5, 6)


def test_analyze_cache_and_param_change(cli, lecture_clip, fake_whisper):
    first = cli.ok("analyze", str(lecture_clip))
    assert set(first["ran"]) == {"audio", "silence", "loudness", "transcript"} or set(first["ran"]) >= {"audio", "silence", "loudness", "transcript"}
    assert fake_whisper.calls == 1
    second = cli.ok("analyze", str(lecture_clip))
    assert second["ran"] == []
    assert set(second["cached"]) >= {"probe", "audio", "silence", "loudness", "transcript"}
    assert fake_whisper.calls == 1
    cli.ok("config", "set", "silence.threshold_db", "-30")
    third = cli.ok("analyze", str(lecture_clip))
    assert third["ran"] == ["silence"]
    fourth = cli.ok("analyze", str(lecture_clip), "--language", "fr")
    assert fourth["ran"] == ["transcript"]


def test_analyze_summary_and_index(cli, lecture_clip, fake_whisper):
    d = cli.ok("analyze", str(lecture_clip))
    sm = d["summary"]
    assert sm["word_count"] == 18 and sm["language"] == "en"
    assert sm["silence_count"] >= 2
    idx = cli.ok("index", str(lecture_clip))
    assert idx["schema"] == "videocli.index/v1"
    assert {"probe", "audio", "silence", "loudness", "transcript"} <= set(idx["artifacts"])


def test_low_confidence_warning(cli, lecture_clip, fake_whisper):
    cli.ok("config", "set", "transcript.low_confidence_share", "0.01")
    code, env = cli("analyze", str(lecture_clip))
    assert any(w["code"] == "LOW_CONFIDENCE_WORDS" for w in env["warnings"])


def test_words_clamped_and_fixed(cli, lecture_clip, fake_whisper):
    cli.ok("analyze", str(lecture_clip))
    words = cli.ok("transcript", str(lecture_clip), "--format", "words", "--from", "11")["words"]
    for w in words:
        assert w["end"] - w["start"] >= 0.02 - 1e-9
        assert w["end"] <= 12.0 + 1e-6


def test_model_not_found_before_extraction(cli, lecture_clip):
    code, env = cli("analyze", str(lecture_clip))
    assert code == 3
    assert env["error"]["code"] == "MODEL_NOT_FOUND"
    assert "videocli setup" in env["error"]["hint"]
    assert not list(Path(".videocli/sources").glob("*/audio.wav"))


def test_no_audio_skips_audio_analyzers(cli, video_only, fake_whisper):
    d = cli.ok("analyze", str(video_only))
    assert {s["name"] for s in d["skipped"]} >= {"audio", "silence", "loudness", "transcript"}
    code, env = cli("analyze", str(video_only), "--only", "silence")
    assert code == 4 and env["error"]["code"] == "NO_AUDIO"


# --------------------------------------------------------------------------- views & search


def test_transcript_compact_window_and_determinism(cli, lecture_clip, fake_whisper):
    cli.ok("analyze", str(lecture_clip))
    full = cli.ok("transcript", str(lecture_clip), "--format", "compact")
    lines = full["text"].splitlines()
    assert lines[0] == "[0000.10-0002.85] So today we will look at binary search."
    assert lines[1].startswith("[0002.85-0005.10] (silence 2.25)")
    again = cli.ok("transcript", str(lecture_clip), "--format", "compact")
    assert again["text"] == full["text"]
    window = cli.ok("transcript", str(lecture_clip), "--from", "5", "--to", "8")
    assert window["total"] == 1 and "sorted array" in window["text"]
    limited = cli.ok("transcript", str(lecture_clip), "--limit", "1")
    assert limited["truncated"] is True and limited["total"] == 4
    srt = cli.ok("transcript", str(lecture_clip), "--format", "srt")["text"]
    assert srt.startswith("1\n00:00:00,100 --> 00:00:02,850\n")


def test_find_phrase_word_times_across_segments(cli, lecture_clip, fake_whisper):
    cli.ok("analyze", str(lecture_clip))
    res = cli.ok("find", str(lecture_clip), "binary search")
    assert res["total"] == 2
    first, second = res["matches"]
    assert first["start"] == pytest.approx(2.2) and first["end"] == pytest.approx(2.85)
    # "Binary" ends segment 2, "search" starts segment 3: the phrase spans two segments
    assert second["segment"] == 2 and second["start"] == pytest.approx(11.5) and second["end"] == pytest.approx(11.95)
    ctx = cli.ok("find", str(lecture_clip), "SORTED array", "--context", "1")
    assert ctx["matches"][0]["start"] == pytest.approx(7.1)
    assert [c["id"] for c in ctx["matches"][0]["context"]] == [0, 1, 2]
    rx = cli.ok("find", str(lecture_clip), r"\bum\b", "--regex")
    assert rx["total"] == 1 and rx["matches"][0]["start"] == pytest.approx(5.1)


def test_missing_transcript_hint(cli, tone_gap_tone):
    cli.ok("analyze", str(tone_gap_tone), "--skip", "transcript")
    code, env = cli("transcript", str(tone_gap_tone))
    assert code == 4 and env["error"]["code"] == "ARTIFACT_MISSING"
    assert env["error"]["hint"].startswith("Run: videocli analyze")


def test_analyze_estimate(cli, lecture_clip):
    d = cli.ok("analyze", str(lecture_clip), "--estimate")
    assert d["estimate"][0]["analyzers"][0] == "probe"
    assert d["seconds"] > 0
    assert not list(Path(".videocli/sources").glob("*/audio.wav"))


@pytest.mark.slow
def test_real_tiny_model_finds_key_words(cli, media_dir):  # pragma: no cover - needs network/model
    """Runs the real tiny model on a speech clip in tests/data/speech.wav when present."""
    speech = Path(__file__).parent / "data" / "speech.wav"
    if not speech.exists():
        pytest.skip("tests/data/speech.wav not present")
    from videocli.setup.profiles import download_whisper, whisper_model_path

    if whisper_model_path("tiny") is None:
        download_whisper("tiny")
    d = cli.ok("analyze", str(speech), "--model", "tiny", "--language", "en")
    assert d["summary"]["word_count"] > 3


def test_faster_whisper_gets_samples_not_a_path(tmp_path, monkeypatch, audio_only):
    """faster-whisper must not decode files itself (its PyAV call broke with av 19)."""
    import sys
    import types

    import numpy as np

    from videocli.analyze.transcript import FasterWhisper
    from videocli.core import config
    from videocli.media.audio import extract_analysis_wav
    from videocli.setup import profiles

    seen = {}

    class FakeModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, audio, **kw):
            seen["type"] = type(audio)
            seen["dtype"] = getattr(audio, "dtype", None)
            return iter([]), types.SimpleNamespace(duration=2.0, language="en", language_probability=1.0)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(profiles, "whisper_model_path", lambda m: None)
    wav = extract_analysis_wav(audio_only, tmp_path / "a.wav")
    out = FasterWhisper(config.load(None)).transcribe(wav, model="small", language=None, initial_prompt=None, on_progress=lambda f: None)
    assert seen["type"] is np.ndarray and seen["dtype"] == np.float32
    assert out["segments"] == []


def test_gpu_runtime_missing_falls_back_to_cpu(tmp_path, monkeypatch, audio_only):
    from videocli.analyze import transcript as tmod
    from videocli.core import config
    from videocli.media.audio import extract_analysis_wav

    calls = []

    def fake_run(self, audio, model, device, compute, language, prompt, on_progress):
        calls.append((device, compute))
        if device == "cuda":
            raise RuntimeError("Library libcublas.so.12 is not found or cannot be loaded")
        return {"language": "en", "language_probability": 1.0, "segments": [], "device": device}

    monkeypatch.setattr(tmod.FasterWhisper, "_run", fake_run)
    monkeypatch.setattr(tmod.FasterWhisper, "_device", lambda self: ("cuda", "float16"))
    wav = extract_analysis_wav(audio_only, tmp_path / "a.wav")
    fw = tmod.FasterWhisper(config.load(None))
    out = fw.transcribe(wav, model="small", language=None, initial_prompt=None, on_progress=lambda f: None)
    assert calls == [("cuda", "float16"), ("cpu", "int8")] and out["device"] == "cpu" and "libcublas" in fw.fallback


def test_preload_finds_nvidia_wheel_libraries(tmp_path, monkeypatch):
    import importlib.machinery
    import importlib.util

    from videocli.analyze import transcript as tmod

    lib = tmp_path / "nvidia" / "cublas" / "lib"
    lib.mkdir(parents=True)

    def fake_find_spec(name):
        if name == "nvidia.cublas":
            spec = importlib.machinery.ModuleSpec(name, None, is_package=True)
            spec.submodule_search_locations = [str(tmp_path / "nvidia" / "cublas")]
            return spec
        return None

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    assert tmod.nvidia_library_dirs() == [lib]


def test_gpu_group_not_in_full_profile():
    from videocli.setup import profiles

    assert "gpu" in profiles.GROUPS and "gpu" not in profiles.PROFILES["full"]
    assert profiles.resolve(None, ["gpu"]) == ["base", "gpu"]
