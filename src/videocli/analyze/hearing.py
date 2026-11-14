"""Audio analyzers (phase 5): prosody, events, music structure, diarization.

``music`` uses librosa when the ``music`` group is installed and otherwise a built-in beat
tracker (beats, downbeats and unlabelled sections; decisions X4). ``diarize`` needs the
``diarize`` group.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from videocli.analyze.base import Availability, RunContext, load_artifact, register
from videocli.core.cache import write_artifact
from videocli.core.errors import feature_not_installed
from videocli.core.source import Source
from videocli.core.timeutil import ms
from videocli.media.audio import read_wav

SR = 16000


class _Base:
    version = 1
    needs_audio = True
    needs_video = False

    def params(self, ctx: RunContext) -> dict[str, Any]:
        return {}

    def available(self, ctx: RunContext) -> Availability:
        return Availability.yes()

    def _wav(self, ctx: RunContext, src: Source) -> np.ndarray:
        x, _ = read_wav(ctx.ws.source_dir(src.id) / "audio.wav")
        return x

    def _write(self, ctx: RunContext, src: Source, params: dict[str, Any], body: dict[str, Any]) -> Path:
        out = ctx.artifact_path(src, self.name)  # type: ignore[attr-defined]
        clean = {k: v for k, v in params.items() if not k.startswith("_")}
        write_artifact(out, schema=self.schema, key=params["_key"], params=clean, body={"source_id": src.id, **body})  # type: ignore[attr-defined]
        return out


# --------------------------------------------------------------------------- prosody


def pitch_hz(x: np.ndarray, sr: int = SR, fmin: float = 70, fmax: float = 400) -> float | None:
    """Fundamental frequency by normalised autocorrelation (None when unvoiced)."""
    if len(x) < sr // 50:
        return None
    x = x - x.mean()
    if np.sqrt(np.mean(x**2)) < 1e-3:
        return None
    n = 1 << int(np.ceil(np.log2(2 * len(x))))
    f = np.fft.rfft(x, n)
    ac = np.fft.irfft(f * np.conj(f))[: len(x)]
    ac /= ac[0] + 1e-12
    lo, hi = int(sr / fmax), min(int(sr / fmin), len(ac) - 1)
    if hi <= lo:
        return None
    k = lo + int(np.argmax(ac[lo:hi]))
    return float(sr / k) if ac[k] > 0.3 else None


class ProsodyAnalyzer(_Base):
    name = "prosody"
    requires: tuple[str, ...] = ("audio", "transcript")
    schema = "videocli.prosody/v1"

    def run(self, src: Source, params: dict[str, Any], ctx: RunContext) -> Path:
        x = self._wav(ctx, src)
        tr = load_artifact(ctx.ws, src, "transcript") or {}
        words = [w for s in tr.get("segments", []) for w in s["words"]]
        feats = []
        for w in words:
            seg = x[int(w["start"] * SR): int(w["end"] * SR)]
            e = 20 * np.log10(float(np.sqrt(np.mean(seg**2))) + 1e-9) if len(seg) else -100.0
            p = pitch_hz(seg)
            letters = max(1, sum(c.isalpha() for c in w["word"]))
            rate = letters / max(0.05, w["end"] - w["start"])
            feats.append((e, p, rate))
        E = np.array([f[0] for f in feats]) if feats else np.zeros(0)
        P = np.array([f[1] if f[1] else np.nan for f in feats]) if feats else np.zeros(0)
        R = np.array([f[2] for f in feats]) if feats else np.zeros(0)

        def z(a: np.ndarray) -> np.ndarray:
            if not len(a):
                return a
            m, s = np.nanmean(a), np.nanstd(a) + 1e-6
            return np.nan_to_num((a - m) / s)

        zE, zP, zR = z(E), z(P), z(R)
        out = []
        for i, w in enumerate(words):
            emph = (max(0, zE[i]) + max(0, zP[i]) + max(0, -zR[i])) / 3
            out.append({"start": w["start"], "end": w["end"], "word": w["word"], "energy_db": round(float(E[i]), 1),
                        "pitch_hz": None if np.isnan(P[i]) else round(float(P[i]), 1), "rate_z": round(float(zR[i]), 2),
                        "emphasis": round(float(emph), 3)})
        pauses = [{"start": a["end"], "end": b["start"], "duration": ms(b["start"] - a["end"]), "breath": 0.15 <= b["start"] - a["end"] < 0.6}
                  for a, b in zip(words, words[1:], strict=False) if b["start"] - a["end"] >= 0.15]
        top = sorted(out, key=lambda w: -w["emphasis"])[: max(1, len(out) // 20)]
        return self._write(ctx, src, params, {"words": out, "pauses": pauses, "emphasised": [{"start": w["start"], "word": w["word"]} for w in top]})


# --------------------------------------------------------------------------- events


class EventsAnalyzer(_Base):
    name = "events"
    requires: tuple[str, ...] = ("audio",)
    schema = "videocli.events/v1"

    def run(self, src: Source, params: dict[str, Any], ctx: RunContext) -> Path:
        x = self._wav(ctx, src)
        win = SR  # 1 s
        events = []
        rms = []
        for i in range(0, max(0, len(x) - win + 1), win // 2):
            seg = x[i:i + win]
            spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) + 1e-9
            flat = float(np.exp(np.mean(np.log(spec))) / np.mean(spec))
            r = float(np.sqrt(np.mean(seg**2)))
            rms.append(r)
            peak_ratio = float(spec.max() / np.mean(spec))
            events.append((i / SR, flat, r, peak_ratio))
        if not events:
            return self._write(ctx, src, params, {"events": [], "provenance": "heuristic"})
        med = float(np.median(rms)) + 1e-6
        out = []
        for t, flat, r, pk in events:
            if r > 4 * med and r > 0.05:
                out.append({"kind": "noise_burst", "start": ms(t), "end": ms(t + 1.0), "confidence": 0.5})
            elif flat < 0.02 and pk > 50 and r > 0.01:
                out.append({"kind": "music_present", "start": ms(t), "end": ms(t + 1.0), "confidence": 0.4})
        merged: list[dict[str, Any]] = []
        for e in out:
            if merged and merged[-1]["kind"] == e["kind"] and e["start"] <= merged[-1]["end"]:
                merged[-1]["end"] = e["end"]
            else:
                merged.append(e)
        return self._write(ctx, src, params, {"events": merged, "provenance": "heuristic (laughter and applause need the perception group)"})


# --------------------------------------------------------------------------- music


def onset_envelope(x: np.ndarray, sr: int, hop: int = 256) -> np.ndarray:
    n_fft = 1024
    frames = 1 + max(0, (len(x) - n_fft) // hop)
    win = np.hanning(n_fft)
    prev = None
    env = np.zeros(frames)
    for i in range(frames):
        seg = x[i * hop: i * hop + n_fft]
        if len(seg) < n_fft:
            break
        mag = np.log1p(np.abs(np.fft.rfft(seg * win)) * 10)
        if prev is not None:
            env[i] = np.maximum(0, mag - prev).sum()
        prev = mag
    env -= np.convolve(env, np.ones(16) / 16, mode="same")
    return np.maximum(env, 0)


def track_beats(x: np.ndarray, sr: int) -> dict[str, Any]:
    """Tempo by autocorrelation of the onset envelope, beats by dynamic programming (Ellis 2007)."""
    hop = 256
    env = onset_envelope(x, sr, hop)
    fps = sr / hop
    if len(env) < fps * 4 or env.max() <= 0:
        return {"tempo": None, "beats": [], "downbeats": [], "env": env, "fps": fps}
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    lags = np.arange(len(ac))
    bpm = 60 * fps / np.maximum(lags, 1)
    valid = (bpm >= 60) & (bpm <= 200)
    weight = np.exp(-0.5 * (np.log2(bpm / 120.0) / 0.9) ** 2)
    lag = int(lags[valid][np.argmax((ac * weight)[valid])])
    tempo = 60 * fps / lag
    # dynamic programming
    score = env.copy()
    back = np.full(len(env), -1)
    tight = 100.0
    for i in range(len(env)):
        lo, hi = i - int(2 * lag), i - int(lag / 2)
        if hi <= 0:
            continue
        lo = max(0, lo)
        prev = np.arange(lo, hi)
        pen = -tight * (np.log((i - prev) / lag)) ** 2
        cand = score[prev] + pen
        j = int(np.argmax(cand))
        score[i] = env[i] + cand[j]
        back[i] = prev[j]
    i = int(np.argmax(score[-int(lag):])) + len(env) - int(lag)
    beats = []
    while i >= 0:
        beats.append(i)
        i = int(back[i])
    beats = np.array(beats[::-1])
    # downbeats: the phase (of 4) whose beats carry the most onset strength
    phase = int(np.argmax([env[beats[k::4]].sum() for k in range(4)])) if len(beats) >= 8 else 0
    down = beats[phase::4]
    off = 512 / sr  # frame times are window starts; the onset sits near the window centre
    return {"tempo": round(float(tempo), 1), "beats": [ms(b / fps + off) for b in beats], "downbeats": [ms(b / fps + off) for b in down],
            "env": env, "fps": fps}


class MusicAnalyzer(_Base):
    name = "music"
    version = 2
    requires: tuple[str, ...] = ("audio",)
    schema = "videocli.music/v1"

    def params(self, ctx: RunContext) -> dict[str, Any]:
        from videocli.setup.profiles import module_present

        return {"engine": "librosa" if module_present("librosa") else "builtin"}

    def run(self, src: Source, params: dict[str, Any], ctx: RunContext) -> Path:
        x = self._wav(ctx, src)
        warnings = []
        if params["engine"] == "librosa":
            import librosa

            tempo, frames = librosa.beat.beat_track(y=x, sr=SR)
            beats = [ms(float(t)) for t in librosa.frames_to_time(frames, sr=SR)]
            down = beats[::4]
            info = {"tempo": round(float(np.atleast_1d(tempo)[0]), 1), "beats": beats, "downbeats": down}
        else:
            info = track_beats(x, SR)
            info.pop("env")
            info.pop("fps")
        down = info["downbeats"]
        bars = list(zip(down, down[1:], strict=False))
        bar_energy = [round(float(np.sqrt(np.mean(x[int(a * SR):int(b * SR)] ** 2) + 1e-12)), 4) for a, b in bars]
        sections = []
        if len(bar_energy) >= 8:
            e = np.asarray(bar_energy)
            nov = np.abs(np.diff(np.convolve(e, np.ones(4) / 4, mode="same")))
            cuts = sorted(set([0] + [int(i) + 1 for i in np.argsort(-nov)[: max(1, len(e) // 8)]] + [len(e)]))
            labels = "ABCDEFGH"
            for k, (i, j) in enumerate(zip(cuts, cuts[1:], strict=False)):
                if j <= i:
                    continue
                lab = "intro" if k == 0 and e[i:j].mean() < e.mean() * 0.8 else ("outro" if j == len(e) and e[i:j].mean() < e.mean() * 0.8 else labels[k % 8])
                sections.append({"label": lab, "start": bars[i][0], "end": bars[j - 1][1], "energy": round(float(e[i:j].mean()), 4)})
        tr = load_artifact(ctx.ws, src, "transcript", required=False)
        if tr and tr.get("word_count", 0) > 2 * (ctx.probe.duration if ctx.probe else 60) / 60 * 60 / 2:
            warnings.append("speech-heavy audio: beats may be unreliable")
        return self._write(ctx, src, params, {**info, "bars": len(bars), "bar_energy": bar_energy, "sections": sections,
                                              "engine": params["engine"], "warnings": warnings})


# --------------------------------------------------------------------------- diarize


class DiarizeAnalyzer(_Base):
    name = "diarize"
    requires: tuple[str, ...] = ("audio", "transcript")
    schema = "videocli.diarize/v1"

    def available(self, ctx: RunContext) -> Availability:
        from videocli.setup.profiles import check_group

        if not check_group("diarize", deep=False).ok:
            return Availability(False, feature_not_installed("diarize", "Speaker diarization"))
        return Availability.yes()

    def run(self, src: Source, params: dict[str, Any], ctx: RunContext) -> Path:  # pragma: no cover - needs the diarize group
        from pyannote.audio import Pipeline

        pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")
        dia = pipe(str(ctx.ws.source_dir(src.id) / "audio.wav"))
        turns = [{"start": ms(t.start), "end": ms(t.end), "speaker": spk} for t, _, spk in dia.itertracks(yield_label=True)]
        return self._write(ctx, src, params, {"turns": turns})


for _a in (ProsodyAnalyzer(), EventsAnalyzer(), MusicAnalyzer(), DiarizeAnalyzer()):
    register(_a)


def speakers_summary(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for t in turns:
        s = out.setdefault(t["speaker"], {"speaker": t["speaker"], "talk_seconds": 0.0, "turns": 0})
        s["talk_seconds"] = ms(s["talk_seconds"] + t["end"] - t["start"])
        s["turns"] += 1
    return sorted(out.values(), key=lambda s: -s["talk_seconds"])
