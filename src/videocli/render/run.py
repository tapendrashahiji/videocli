"""``render``: compile an EDL to the graph, run it, and encode the delivery file."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

from videocli.core import jobs as jobmod
from videocli.core import progress
from videocli.core.cache import Store, make_key
from videocli.core.errors import InputError, UsageError, feature_not_available
from videocli.core.source import check_unchanged, compute_source_id
from videocli.core.timeutil import ms
from videocli.core.workspace import Workspace
from videocli.edl.model import EDL, Clip
from videocli.media import ffmpeg
from videocli.render import presets
from videocli.render.graph import execute, finalize, node_file
from videocli.render.nodes import media as nodes
from videocli.render.timeline import Compiled, compile_timeline

SR = nodes.SAMPLE_RATE


def check_renderable(edl: EDL) -> None:
    """Refuse parts a later phase renders (they validate, but are not drawn yet)."""
    t = edl.tracks
    if t.graphics:
        raise feature_not_available(f"The graphics track ({len(t.graphics)} items)", 8)
    graded = [c.id for c in t.main if c.grade] + [o.id for o in t.overlay if o.grade]
    if graded:
        raise feature_not_available(f"Grades on {', '.join(graded[:5])}", 7)
    transforms = [sid for sid, s in edl.sources.items() if s.input_transform]
    if transforms:
        raise feature_not_available(f"Input colour transforms on sources {', '.join(transforms)}", 7)
    if not t.main:
        raise InputError("EDL_INVALID", f"EDL {edl.name} has no clips on the spine.", hint=f"Run: videocli edl show {edl.name}")


def compile_edl(edl: EDL, preset: presets.Preset, *, crf: int, x264_preset: str, fade_ms: int | None = None,
                normalize: bool = True, labels: dict[str, str] | None = None, **kw: Any) -> Compiled:
    if fade_ms is not None:
        edl = edl.model_copy(deep=True)
        edl.mix.fade_ms = fade_ms
    return compile_timeline(edl, preset, crf=crf, x264_preset=x264_preset, normalize=normalize, labels=labels, **kw)


def speech_ranges(ws: Workspace, edl: EDL) -> list[tuple[float, float]] | None:
    """Speech in output time, from the spine sources' silence analysis (None if any is missing)."""
    from videocli.core.cache import read_artifact

    tm = edl.timemap()
    out: list[tuple[float, float]] = []
    for sid in {c.source for c in edl.tracks.main}:
        doc = read_artifact(ws.source_dir(sid) / "silence.json")
        if doc is None:
            if edl.sources[sid].has_audio:
                return None
            continue
        for r in doc.get("speech", []):
            out += tm.map_range(sid, r["start"], r["end"])
    return sorted(out)


def room_tone_spec(ws: Workspace, edl: EDL) -> dict[str, Any] | None:
    from videocli.core.cache import read_artifact

    rt = edl.sound.room_tone
    if not rt:
        return None
    if rt != "auto":
        if rt in edl.sources:
            s = edl.sources[rt]
            return {"source": rt, "path": s.path, "start": 0.0, "end": min(s.duration, 5.0)}
        raise UsageError("BAD_ARGUMENT", f"Room tone {rt!r} is not a source of this EDL.", hint="Use: videocli edl sound NAME --room-tone FILE")
    for sid in dict.fromkeys(c.source for c in edl.tracks.main):
        doc = read_artifact(ws.source_dir(sid) / "silence.json")
        if not doc:
            continue
        sil = [x for x in doc.get("silences", []) if x["duration"] >= 0.8]
        if not sil:
            continue
        best = max(sil, key=lambda x: x["duration"])
        mid = (best["start"] + best["end"]) / 2
        half = min(1.0, best["duration"] / 2 - 0.15)
        return {"source": sid, "path": edl.sources[sid].path, "start": round(mid - half, 3), "end": round(mid + half, 3)}
    return None


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
    seg_secs = sum(n.params["frames"] / comp.rate.fps * (3 if n.kind == "composite" else 1) for n in todo if n.kind in ("segment", "composite"))
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
    stems: bool = False,
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
    comp = compile_edl(work, preset, crf=crf, x264_preset=x264p, normalize=normalize, labels=labels,
                       speech=speech_ranges(ws, work) if work.tracks.audio else None,
                       room_tone=room_tone_spec(ws, work), stems=stems)
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
        "chunks": {"segment": sum(1 for n in comp.chunks if n.kind == "segment"), "composite": sum(1 for n in comp.chunks if n.kind == "composite")},
        "stems": _copy_stems(comp, out) if stems else [],
        "warnings": comp.warnings,
    }


def _copy_stems(comp: Compiled, out: Path) -> list[str]:
    import shutil

    paths = []
    for stem in ("dialogue", "music", "effects"):
        src = comp.mix.output / f"stem-{stem}.wav" if comp.mix.output else None
        if src and src.is_file():
            dst = out.with_name(f"{out.stem}.{stem}.wav")
            shutil.copyfile(src, dst)
            paths.append(str(dst))
    return paths


def _float(v: Any) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None
