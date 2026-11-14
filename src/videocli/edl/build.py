"""``plan``: build a new EDL from a source and its analysis facts."""

from __future__ import annotations

from typing import Any

from videocli.core.cache import read_artifact
from videocli.core.errors import InputError
from videocli.core.source import Source
from videocli.core.timeutil import Rate, ms
from videocli.core.workspace import Workspace
from videocli.edl.model import EDL, Clip, CutRecord, SourceRef
from videocli.edl.planners import PlanOptions, plan


def source_ref(ws: Workspace, src: Source) -> tuple[SourceRef, Any]:
    from videocli.media import probe as probemod

    pr = probemod.load(ws, src)
    ref = SourceRef(
        path=str(src.path), duration=pr.duration, fps=pr.video.fps if pr.video else None,
        has_video=pr.video is not None, has_audio=bool(pr.audio),
        width=pr.video.display_width if pr.video else None, height=pr.video.display_height if pr.video else None,
    )
    return ref, pr


def timeline_from_probe(pr: Any) -> tuple[str, tuple[int, int]]:
    if pr.video is None:
        return "25/1", (1280, 720)
    rate = Rate.parse(pr.video.fps if not pr.video.vfr else pr.video.avg_fps)
    # Round awkward VFR averages to a common rate.
    if pr.video.vfr:
        common = [Rate.parse(x) for x in ("24000/1001", "24/1", "25/1", "30000/1001", "30/1", "50/1", "60000/1001", "60/1")]
        rate = min(common, key=lambda r: abs(r.fps - rate.fps))
    w, h = pr.video.display_width, pr.video.display_height
    return str(rate), (w - w % 2, h - h % 2)


def transcript_words(ws: Workspace, src: Source) -> tuple[list[dict[str, Any]], str | None]:
    doc = read_artifact(ws.source_dir(src.id) / "transcript.json")
    if not doc:
        return [], None
    return [w for s in doc.get("segments", []) for w in s.get("words", [])], doc.get("language")


def build_plan(ws: Workspace, src: Source, name: str, opts: PlanOptions, *, need_transcript: bool) -> tuple[EDL, dict[str, Any], list[dict[str, str]]]:
    warnings: list[dict[str, str]] = []
    ref, pr = source_ref(ws, src)
    rate, res = timeline_from_probe(pr)
    silences: list[tuple[float, float]] = []
    if opts.cut_silence is not None:
        doc = read_artifact(ws.source_dir(src.id) / "silence.json")
        if not doc:
            raise InputError("ARTIFACT_MISSING", f"No silence analysis for {src.path.name}.", hint=f"Run: videocli analyze {src.path} --only silence")
        silences = [(s["start"], s["end"]) for s in doc.get("silences", [])]
    words, language = transcript_words(ws, src)
    if need_transcript and not words:
        raise InputError("ARTIFACT_MISSING", f"No transcript for {src.path.name}; filler removal needs one.",
                         hint=f"Run: videocli analyze {src.path}")
    if opts.snap and not words:
        warnings.append({"code": "NO_TRANSCRIPT_SNAP", "message": "No transcript: cut boundaries were not snapped to word edges."})
    opts.language = opts.language or language
    result = plan(pr.duration, silences, words, opts)
    clips = [Clip(id=f"c{i + 1:04d}", source=src.id, start=a, end=b, reason=reason) for i, (a, b, reason) in enumerate(result.keep)]
    edl = EDL(name=name, rate=rate, resolution=res, sources={src.id: ref})
    edl.tracks.main = clips
    edl.cuts = [CutRecord(source=src.id, start=c.start, end=c.end, reason=c.reason) for c in result.cuts]
    info = {"steps": result.steps, "kept_seconds": ms(sum(b - a for a, b, _ in result.keep))}
    if not clips:
        raise InputError("EDL_INVALID", "The plan removed everything; nothing would be left to render.",
                         hint="Loosen --cut-silence (larger min) or check --keep-only ranges.")
    return edl, info, warnings
