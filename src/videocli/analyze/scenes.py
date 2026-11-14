"""Analyzer ``scenes``: shot boundaries with PySceneDetect on a downscaled stream."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from videocli.analyze.base import Availability, RunContext, register
from videocli.core import progress
from videocli.core.cache import write_artifact
from videocli.core.errors import RuntimeFailure, UsageError
from videocli.core.source import Source
from videocli.core.timeutil import ms

DEFAULT_THRESHOLDS = {"adaptive": 3.0, "content": 27.0}


def _secs(tc: Any) -> float:
    v = getattr(tc, "seconds", None)
    return float(v if v is not None and not callable(v) else tc.get_seconds())


def detect(path: Path, *, detector: str, threshold: float, min_scene: float, duration: float) -> list[tuple[float, float]]:
    from scenedetect import AdaptiveDetector, ContentDetector, SceneManager, open_video

    try:
        video = open_video(str(path), backend="opencv")
    except Exception as exc:  # pragma: no cover - opencv cannot open the file
        raise RuntimeFailure("ANALYZER_FAILED", f"PySceneDetect could not open {path}: {exc}") from exc
    fps = float(video.frame_rate or 25.0)
    min_frames = max(1, int(round(min_scene * fps)))
    if detector == "adaptive":
        det = AdaptiveDetector(adaptive_threshold=threshold, min_scene_len=min_frames)
    elif detector == "content":
        det = ContentDetector(threshold=threshold, min_scene_len=min_frames)
    else:
        raise UsageError("BAD_ARGUMENT", f"--detector must be adaptive or content (got {detector!r}).")
    sm = SceneManager()
    sm.auto_downscale = True
    sm.add_detector(det)
    total = duration or 1.0

    def cb(_frame, tc) -> None:
        progress.update(_secs(tc) / total)

    sm.detect_scenes(video=video, show_progress=False, callback=cb if duration else None)
    scenes = sm.get_scene_list(start_in_scene=True)
    out = [(ms(_secs(a)), ms(_secs(b))) for a, b in scenes]
    if not out and duration:
        out = [(0.0, ms(duration))]
    return out


class ScenesAnalyzer:
    name = "scenes"
    version = 1
    requires: tuple[str, ...] = ("probe",)
    schema = "videocli.scenes/v1"
    needs_audio = False
    needs_video = True

    def params(self, ctx: RunContext) -> dict[str, Any]:
        det = ctx.options.get("detector") or "adaptive"
        thr = ctx.options.get("threshold")
        return {
            "detector": det,
            "threshold": float(thr) if thr is not None else DEFAULT_THRESHOLDS.get(det, 3.0),
            "min_scene": float(ctx.options.get("min_scene") or 0.6),
        }

    def available(self, ctx: RunContext) -> Availability:
        return Availability.yes()

    def run(self, src: Source, params: dict[str, Any], ctx: RunContext) -> Path:
        duration = float(ctx.probe.duration if ctx.probe else 0.0)
        scenes = detect(src.path, detector=params["detector"], threshold=params["threshold"], min_scene=params["min_scene"], duration=duration)
        out = ctx.artifact_path(src, self.name)
        clean = {k: v for k, v in params.items() if not k.startswith("_")}
        write_artifact(
            out, schema=self.schema, key=params["_key"], params=clean,
            body={
                "source_id": src.id,
                "duration": duration,
                "scenes": [{"id": f"s{i + 1:04d}", "start": a, "end": b, "duration": ms(b - a)} for i, (a, b) in enumerate(scenes)],
            },
        )
        return out


register(ScenesAnalyzer())
