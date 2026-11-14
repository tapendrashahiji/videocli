"""Delivery presets: ``source``, ``1080p``, ``720p``, ``preview`` (480p, fast)."""

from __future__ import annotations

from dataclasses import dataclass

from videocli.core.errors import UsageError


@dataclass(frozen=True)
class Preset:
    name: str
    short_side: int | None  # None: keep the timeline resolution
    crf: int | None  # None: take the EDL's output.crf
    x264_preset: str | None  # None: take the render.x264_preset setting
    audio_bitrate: str | None = None
    est_mbps: float = 8.0  # rough bitrate for --estimate
    est_speed: float = 1.5  # rough encode speed (x realtime per worker)


PRESETS = {
    "source": Preset("source", None, None, None, est_mbps=10.0, est_speed=1.2),
    "1080p": Preset("1080p", 1080, None, None, est_mbps=8.0, est_speed=1.5),
    "720p": Preset("720p", 720, None, None, est_mbps=4.0, est_speed=3.0),
    "preview": Preset("preview", 480, 30, "ultrafast", "96k", est_mbps=0.8, est_speed=12.0),
}


def get(name: str) -> Preset:
    if name not in PRESETS:
        raise UsageError("BAD_ARGUMENT", f"Unknown preset {name!r}.", hint=f"Choose one of: {', '.join(PRESETS)}")
    return PRESETS[name]


def dims(preset: Preset, timeline: tuple[int, int]) -> tuple[int, int]:
    w, h = timeline
    if preset.short_side is None:
        return w, h
    short = min(w, h)
    if short <= preset.short_side:
        return w, h
    scale = preset.short_side / short
    nw = int(round(w * scale / 2)) * 2
    nh = int(round(h * scale / 2)) * 2
    return max(nw, 16), max(nh, 16)
