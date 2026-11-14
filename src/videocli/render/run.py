"""``render``: compile an EDL to the graph, run it, and encode the delivery file."""

from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from videocli.core import jobs as jobmod
from videocli.core import progress
from videocli.core.cache import Store, make_key
from videocli.core.errors import InputError, UsageError, feature_not_available
from videocli.core.source import check_unchanged, compute_source_id
from videocli.core.timeutil import Rate, ms
from videocli.core.workspace import Workspace
from videocli.edl.model import EDL, Clip
from videocli.media import ffmpeg
from videocli.render import presets
from videocli.render.graph import Node, execute, finalize, node_file, topo
from videocli.render.nodes import media as nodes

SR = nodes.SAMPLE_RATE


@dataclass
class Compiled:
    edl: EDL
    preset: presets.Preset
    size: tuple[int, int]
    rate: Rate
    segments: list[Node]
    lanes: list[Node]
    picture: Node
    mix: Node
    frames: int
    samples: int
    clip_frames: list[tuple[str, int, int]] = field(default_factory=list)  # (clip id, first frame, frame count)

    @property
    def nodes(self) -> list[Node]:
        return topo([self.picture, self.mix])

    @property
    def duration(self) -> float:
        return self.frames / self.rate.fps


def check_renderable(edl: EDL) -> None:
    """Refuse parts a later phase renders (they validate, but are not drawn yet)."""
    t = edl.tracks
    if t.overlay:
        raise feature_not_available(f"The overlay track ({len(t.overlay)} items)", 6)
    if t.graphics:
        raise feature_not_available(f"The graphics track ({len(t.graphics)} items)", 8)
    if t.audio:
        raise feature_not_available(f"The audio track ({len(t.audio)} items: music, effects, room tone)", 6)
    real = [x for x in edl.transitions if x.type != "cut"]
    if real:
        raise feature_not_available(f"Transitions ({', '.join(x.type for x in real)})", 6)
    graded = [c.id for c in t.main if c.grade]
    if graded:
        raise feature_not_available(f"Grades on clips {', '.join(graded[:5])}", 7)
    split = [c.id for c in t.main if c.audio.lead or c.audio.lag]
    if split:
        raise feature_not_available(f"Split edits (audio lead/lag) on clips {', '.join(split[:5])}", 6)
    transforms = [sid for sid, s in edl.sources.items() if s.input_transform]
    if transforms:
        raise feature_not_available(f"Input colour transforms on sources {', '.join(transforms)}", 7)
    if not t.main:
        raise InputError("EDL_INVALID", f"EDL {edl.name} has no clips on the spine.", hint=f"Run: videocli edl show {edl.name}")


def compile_edl(edl: EDL, preset: presets.Preset, *, crf: int, x264_preset: str, fade_ms: int | None = None,
                normalize: bool = True, labels: dict[str, str] | None = None) -> Compiled:
    rate = Rate.parse(edl.rate)
    size = presets.dims(preset, edl.resolution)
    fps = rate.value
    fade = edl.mix.fade_ms if fade_ms is None else fade_ms
    segs: list[Node] = []
    lanes: list[Node] = []
    placements: list[tuple[int, float]] = []
    clip_frames = []
    f0 = 0
    for i, c in enumerate(edl.tracks.main):
        src = edl.sources[c.source]
        frames = max(1, round(c.out_duration * rate.fps))
        f1 = f0 + frames
        s0 = round(f0 * SR / fps)
        s1 = round(f1 * SR / fps)
        seg_params = {
            "source": c.source, "start": c.start, "speed": c.speed, "rate": str(rate), "size": list(size), "frames": frames,
            "pix_fmt": "yuv420p", "crf": crf, "x264_preset": x264_preset, "has_video": src.has_video,
        }
        if labels and c.id in labels:
            seg_params["label"] = labels[c.id]
        segs.append(Node("segment", seg_params, label=c.id, runner=nodes.run_segment, extra={"path": src.path}))
        lanes.append(Node(
            "lane",
            {
                "source": c.source, "start": c.start, "end": c.end, "speed": c.speed, "stream": c.audio.stream,
                "gain_db": c.audio.gain_db, "samples": s1 - s0, "fade_ms": fade, "has_audio": src.has_audio, "lane": i % 2,
            },
            label=c.id, runner=nodes.run_lane, extra={"path": src.path},
        ))
        placements.append((s0, 1.0))
        clip_frames.append((c.id, f0, frames))
        f0 = f1
    total_samples = round(f0 * SR / fps)
    picture = Node("picture", {"rate": str(rate), "count": len(segs)}, inputs=segs, label="picture", runner=nodes.run_picture)
    mix = Node(
        "mix",
        {"samples": total_samples, "placements": placements, "normalize": normalize and edl.mix.normalize,
         "target_lufs": edl.mix.target_lufs, "true_peak_db": edl.mix.true_peak_db},
        inputs=lanes, label="mix", runner=nodes.run_mix,
    )
    return Compiled(edl, preset, size, rate, segs, lanes, picture, mix, f0, total_samples, clip_frames)


def around_cuts_edl(edl: EDL, seconds: float) -> tuple[EDL, dict[str, str]]:
    """A reel with ``seconds`` on each side of every join, and a label per piece."""
    reel = edl.model_copy(deep=True)
    clips: list[Clip] = []
    labels: dict[str, str] = {}
    main = edl.tracks.main
    tm = edl.timemap()
    for n, (a, b, sp) in enumerate(zip(main, main[1:], tm.spans, strict=False), 1):
        a_start = max(a.start, a.end - seconds * a.speed)
        b_end = min(b.end, b.start + seconds * b.speed)
        ida, idb = f"r{2 * n - 1:04d}", f"r{2 * n:04d}"
        clips.append(Clip(id=ida, source=a.source, start=a_start, end=a.end, speed=a.speed, audio=a.audio))
        clips.append(Clip(id=idb, source=b.source, start=b.start, end=b_end, speed=b.speed, audio=b.audio))
        join = ms(sp.out_end)
        labels[ida] = f"join {n}: {a.id} | {b.id} @ {join:.2f}s  (src {a.end:.2f})"
        labels[idb] = f"join {n}: {a.id} | {b.id} @ {join:.2f}s  (src {b.start:.2f})"
    if not clips:
        raise UsageError("BAD_ARGUMENT", f"EDL {edl.name} has no joins to review.")
    reel.tracks.main = clips
    reel.transitions = []
    reel.name = f"{edl.name}.around-cuts"
    return reel, labels


def _refuse_source_overwrite(ws: Workspace, edl: EDL, out: Path) -> None:
    out_r = out.resolve()
    paths = {Path(s.path).resolve() for s in edl.sources.values()}
    paths |= {Path(rec["path"]).resolve() for rec in ws.data.get("sources", {}).values()}
    if out_r in paths:
        raise InputError("OUTPUT_IS_SOURCE", f"Refusing to overwrite source file {out}.", hint="Choose another -o path.")
    if out_r.exists():
        try:
            sid = compute_source_id(out_r)
        except OSError:
            return
        if sid in edl.sources:
            raise InputError("OUTPUT_IS_SOURCE", f"{out} has the same content as a source in this EDL.", hint="Choose another -o path.")


def estimate(comp: Compiled, store: Store, jobs: int) -> dict[str, Any]:
    finalize(comp.nodes, store)
    todo = [n for n in comp.nodes if not n.cached]
    seg_secs = sum(n.params["frames"] / comp.rate.fps for n in todo if n.kind == "segment")
    secs = seg_secs / comp.preset.est_speed / max(1, min(jobs, 4)) + comp.duration * 0.05 + 2
    disk = comp.preset.est_mbps * 1e6 / 8 * comp.duration * 2 + comp.samples * 2 * 3
    return {
        "output_duration": ms(comp.duration),
        "nodes": len(comp.nodes),
        "cached": len(comp.nodes) - len(todo),
        "seconds": round(secs, 1),
        "disk_bytes": int(disk),
        "size": list(comp.size),
    }


def encode(comp: Compiled, picture: Path, sound: Path, out: Path, *, captions: list[tuple[float, float, str]] | None,
           audio_bitrate: str) -> None:
    from videocli.index.views import srt

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.stem}.videocli-part{out.suffix or '.mp4'}")
    args = ["-y", "-i", ffmpeg.fpath(picture), "-i", ffmpeg.fpath(sound), "-map", "0:v:0", "-map", "1:a:0"]
    cwd = None
    tmpdir = None
    if captions:
        tmpdir = tempfile.TemporaryDirectory(prefix="videocli-captions-")
        cwd = Path(tmpdir.name)
        (cwd / "captions.srt").write_text(srt(captions), encoding="utf-8")
        h = comp.size[1]
        style = f"FontSize={max(12, int(h / 22))},Outline=2,Shadow=0,MarginV={int(h * 0.06)}"
        crf = comp.preset.crf if comp.preset.crf is not None else comp.edl.output.crf
        args += [
            "-vf", f"subtitles=captions.srt:original_size={comp.size[0]}x{comp.size[1]}:force_style='{style}'",
            "-c:v", "libx264", "-preset", comp.preset.x264_preset or "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
        ]
    else:
        args += ["-c:v", "copy"]
    args += ["-c:a", "aac", "-b:a", audio_bitrate, "-ar", str(SR)]
    if out.suffix.lower() in (".mp4", ".mov", ".m4v", ""):
        args += ["-movflags", "+faststart"]
    args += [ffmpeg.fpath(tmp)]
    try:
        with progress.step_ctx("encode"):
            ffmpeg.run(args, log_name="encode", progress_total=comp.duration, cwd=cwd)
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
        if tmpdir is not None:
            tmpdir.cleanup()


def render(
    ws: Workspace,
    edl: EDL,
    out: Path,
    *,
    preset_name: str,
    settings: Any,
    jobs: int,
    burn_captions: bool = False,
    normalize: bool = True,
    around_cuts: float | None = None,
    transcripts: dict[str, Any] | None = None,
    argv: list[str] | None = None,
    dry_run: bool = False,
    want_estimate: bool = False,
) -> dict[str, Any]:
    check_renderable(edl)
    preset = presets.get(preset_name)
    labels = None
    work = edl
    if around_cuts is not None:
        if around_cuts <= 0:
            raise UsageError("BAD_ARGUMENT", "--around-cuts must be positive.")
        work, labels = around_cuts_edl(edl, around_cuts)
        from videocli.edl.io import snap

        snap(work)
    crf = preset.crf if preset.crf is not None else edl.output.crf
    x264p = preset.x264_preset or settings.render__x264_preset
    comp = compile_edl(work, preset, crf=crf, x264_preset=x264p, normalize=normalize, labels=labels)
    store = Store(ws.dir("graph"))
    if want_estimate:
        return {"estimate": estimate(comp, store, jobs)}
    finalize(comp.nodes, store)
    if dry_run:
        return {
            "dry_run": True,
            "output": str(out),
            "output_duration": ms(comp.duration),
            "size": list(comp.size),
            "preset": preset.name,
            "nodes": [n.describe() for n in comp.nodes] + [{"kind": "encode", "label": out.name, "cached": False}],
        }
    for sid in {c.source for c in work.tracks.main}:
        check_unchanged(sid, edl.sources[sid].path)
    _refuse_source_overwrite(ws, edl, out)

    cues = None
    if burn_captions:
        from videocli.render import captions as capmod

        cues = capmod.build(work, transcripts or {})
    job_id = "render-" + make_key(edl.model_dump(mode="json", by_alias=True), preset.name, str(out.resolve()), around_cuts, burn_captions)[:12]
    job = jobmod.start(ws, job_id, "render", argv or [])
    t0 = time.monotonic()
    total = len(comp.nodes)
    cached = sum(1 for n in comp.nodes if n.cached)
    try:
        with progress.step_ctx("render", nodes=total, cached=cached):
            execute(comp.nodes, store, jobs=jobs, on_done=lambda n: jobmod.update(ws, job, last=f"{n.kind} {n.label}"))
        encode(comp, node_file(comp.picture), node_file(comp.mix), out, captions=cues,
               audio_bitrate=preset.audio_bitrate or edl.output.audio_bitrate)
    except BaseException:
        jobmod.update(ws, job, failed_at=time.time())
        raise
    jobmod.finish(ws, job)
    mixinfo = {}
    mj = comp.mix.output / "mix.json" if comp.mix.output else None
    if mj and mj.is_file():
        import json

        mixinfo = json.loads(mj.read_text())
    return {
        "output": str(out),
        "edl": edl.name,
        "preset": preset.name,
        "size": list(comp.size),
        "rate": str(comp.rate),
        "output_duration": ms(comp.duration),
        "frames": comp.frames,
        "clips": len(work.tracks.main),
        "nodes": {"total": total, "cached": cached, "ran": total - cached},
        "seconds": round(time.monotonic() - t0, 2),
        "captions_burned": bool(cues),
        "loudness_before_lufs": _float((mixinfo.get("measured") or {}).get("input_i")) if mixinfo else None,
        "loudness_target_lufs": edl.mix.target_lufs if (normalize and edl.mix.normalize) else None,
        "around_cuts": around_cuts,
    }


def _float(v: Any) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None
