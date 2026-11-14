"""Analyzer ``loudness``: ffmpeg ``ebur128`` on the original audio stream.

Stores integrated loudness, loudness range, true peak, momentary loudness downsampled to one
value per second, and a noise-floor estimate used to suggest a silence threshold.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

from videocli.analyze.base import Availability, RunContext, register
from videocli.core.cache import write_artifact
from videocli.core.source import Source
from videocli.media import ffmpeg

_FRAME = re.compile(r"t:\s*([\d.]+)\s+TARGET:.*?M:\s*(-?[\d.]+|-inf|nan)")
_NUM = r"(-?[\d.]+|-inf|nan)"


def _f(text: str) -> float | None:
    try:
        v = float(text)
    except ValueError:
        return None
    return None if np.isnan(v) or np.isinf(v) else v


def parse_ebur128(stderr: str) -> dict[str, Any]:
    momentary: list[tuple[float, float]] = []
    for line in stderr.splitlines():
        m = _FRAME.search(line)
        if m:
            t = float(m.group(1))
            v = _f(m.group(2))
            momentary.append((t, v if v is not None else -120.7))
    summary = stderr[stderr.rfind("Summary:"):] if "Summary:" in stderr else ""

    def grab(label: str, unit: str) -> float | None:
        mm = re.search(rf"{label}:\s*{_NUM}\s*{unit}", summary)
        return _f(mm.group(1)) if mm else None

    integrated = grab("I", "LUFS")
    lra = grab("LRA", "LU")
    peak = grab("Peak", "dBFS")
    # Downsample momentary (100 ms) to one value per second: the mean energy in each second.
    per_second: list[float] = []
    if momentary:
        last = int(momentary[-1][0])
        buckets: dict[int, list[float]] = {}
        for t, v in momentary:
            buckets.setdefault(min(int(t), last), []).append(v)
        for s in range(0, last + 1):
            vals = buckets.get(s)
            if not vals:
                per_second.append(per_second[-1] if per_second else -120.7)
                continue
            energy = np.mean([10 ** (v / 10) for v in vals])
            per_second.append(round(float(10 * np.log10(max(energy, 1e-12))), 1))
    return {
        "integrated_lufs": integrated, "lra_lu": lra, "true_peak_db": peak, "momentary_per_second": per_second,
        "_momentary": [v for _, v in momentary],
    }


def noise_floor(momentary: list[float]) -> float | None:
    """5th percentile of the 100 ms momentary loudness: the level of the quiet stretches (pauses).

    None for very short files or when the quiet stretches are digital silence."""
    if len(momentary) < 20:
        return None
    floor = float(np.percentile(momentary, 5))
    return None if floor < -100 else round(floor, 1)


class LoudnessAnalyzer:
    name = "loudness"
    version = 1
    requires: tuple[str, ...] = ("probe",)
    schema = "videocli.loudness/v1"
    needs_audio = True
    needs_video = False

    def params(self, ctx: RunContext) -> dict[str, Any]:
        return {"stream": int(ctx.options.get("audio_stream") or 0)}

    def available(self, ctx: RunContext) -> Availability:
        return Availability.yes()

    def run(self, src: Source, params: dict[str, Any], ctx: RunContext) -> Path:
        duration = float(ctx.probe.duration if ctx.probe else 0.0)
        res = ffmpeg.run(
            ["-i", ffmpeg.fpath(src.path), "-map", f"0:a:{params['stream']}", "-vn", "-af", "ebur128=peak=true:framelog=info", "-f", "null", "-"],
            log_name="ebur128", capture=True, progress_total=duration or None,
        )
        data = parse_ebur128(res.stderr)
        floor = noise_floor(data.pop("_momentary"))
        target = float(ctx.settings.loudness__target_lufs)
        warn_lu = float(ctx.settings.loudness__warn_lu)
        if data["integrated_lufs"] is not None and abs(data["integrated_lufs"] - target) > warn_lu:
            ctx.warn(
                "LOUDNESS_FAR_FROM_TARGET",
                f"Integrated loudness {data['integrated_lufs']:.1f} LUFS is {abs(data['integrated_lufs'] - target):.1f} LU from the {target:.0f} LUFS target; render normalizes it.",
            )
        suggestion = None
        thr = float(ctx.settings.silence__threshold_db)
        if floor is not None and floor > thr - 3:
            # momentary LUFS is close to dBFS for speech-band noise; leave a margin above the floor
            suggestion = round(min(-20.0, floor + 6.0), 0)
            ctx.warn(
                "NOISY_ROOM",
                f"Noise floor is about {floor:.0f} LUFS, near the silence threshold ({thr:.0f} dB). "
                f"Try: videocli config set silence.threshold_db {suggestion:.0f}",
            )
        out = ctx.artifact_path(src, self.name)
        clean = {k: v for k, v in params.items() if not k.startswith("_")}
        write_artifact(
            out, schema=self.schema, key=params["_key"], params=clean,
            body={"source_id": src.id, **data, "noise_floor_lufs": floor, "suggested_silence_threshold_db": suggestion, "target_lufs": target},
        )
        return out


register(LoudnessAnalyzer())
