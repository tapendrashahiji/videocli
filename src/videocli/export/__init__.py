"""Export an EDL to editors: OpenTimelineIO, Final Cut XML (FCPXML 1.9) and CMX 3600.

Every writer re-reads its file and compares clip count and total duration (the round-check).
Phase 7 adds ``.cube`` and ASC CDL exports.
"""

from __future__ import annotations

import os
import re
from fractions import Fraction
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

from videocli.core.errors import RuntimeFailure, UsageError
from videocli.core.fsutil import atomic_write_text
from videocli.core.timeutil import Rate, ms
from videocli.edl.model import EDL


def media_path(path: str, relative_to: Path | None) -> str:
    p = Path(path).resolve()
    if relative_to is not None:
        return Path(os.path.relpath(p, relative_to.resolve())).as_posix()
    return p.as_posix()


def _frames(t: float, fps: Fraction) -> int:
    return int(round(t * fps))


def _plans(edl: EDL):
    from videocli.render.timeline import plan_clips

    rate = Rate.parse(edl.rate)
    plans, total = plan_clips(edl, rate.value)
    return rate, plans, total


# --------------------------------------------------------------------------- CMX 3600


def _tc(frame: int, fps_int: int) -> str:
    f = frame % fps_int
    s = frame // fps_int
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}"


def to_cmx3600(edl: EDL, out: Path, relative_to: Path | None = None) -> dict[str, Any]:
    rate, plans, total = _plans(edl)
    fps_int = int(round(rate.fps))
    lines = [f"TITLE: {edl.name.upper()[:70]}", "FCM: NON-DROP FRAME", ""]
    rec = 3600 * fps_int
    left_out = []
    for n, p in enumerate(plans, 1):
        c = p.clip
        src = edl.sources[c.source]
        reel = re.sub(r"[^A-Z0-9]", "", Path(src.path).stem.upper())[:8] or "AX"
        sin = _frames(c.start, rate.value)
        frames = p.frames - p.d_next
        lines.append(f"{n:03d}  {reel:<8} B     C        {_tc(sin, fps_int)} {_tc(sin + frames, fps_int)} {_tc(rec, fps_int)} {_tc(rec + frames, fps_int)}")
        lines.append(f"* FROM CLIP NAME: {Path(src.path).name}")
        lines.append(f"* SOURCE FILE: {media_path(src.path, relative_to)}")
        if c.speed != 1.0 or c.ramp:
            left_out.append(f"speed on {c.id}")
            lines.append(f"M2   {reel:<8}       {c.speed * fps_int:05.1f}                {_tc(sin, fps_int)}")
        lines.append("")
        rec += frames
    if edl.transitions:
        left_out.append(f"{len(edl.transitions)} transition(s) (exported as cuts)")
    if edl.tracks.overlay:
        left_out.append(f"{len(edl.tracks.overlay)} overlay(s)")
    if edl.tracks.audio:
        left_out.append(f"{len(edl.tracks.audio)} audio item(s)")
    if edl.tracks.graphics:
        left_out.append(f"{len(edl.tracks.graphics)} graphic(s)")
    atomic_write_text(out, "\n".join(lines) + "\n")
    # round-check
    text = out.read_text(encoding="utf-8")
    events = re.findall(r"^\d{3}\s+\S+\s+B\s+C\s+\S+ \S+ (\S+) (\S+)$", text, re.M)
    def tc2f(tc: str) -> int:
        h, m, s, f = (int(x) for x in tc.split(":"))
        return ((h * 60 + m) * 60 + s) * fps_int + f
    dur = sum(tc2f(b) - tc2f(a) for a, b in events) / fps_int
    return {"clips": len(events), "duration": ms(dur), "expected_clips": len(plans),
            "expected_duration": ms(sum(p.frames - p.d_next for p in plans) / fps_int), "left_out": left_out}


# --------------------------------------------------------------------------- FCPXML


def _rt(frames: int, fps: Fraction) -> str:
    v = Fraction(frames) / fps
    return f"{v.numerator}/{v.denominator}s" if v.denominator != 1 else f"{v.numerator}s"


def to_fcpxml(edl: EDL, out: Path, relative_to: Path | None = None) -> dict[str, Any]:
    rate, plans, total = _plans(edl)
    fps = rate.value
    w, h = edl.resolution
    fd = Fraction(1) / fps
    left_out = []
    res = [f'<format id="r0" name="videocli" frameDuration="{fd.numerator}/{fd.denominator}s" width="{w}" height="{h}"/>']
    assets = {}
    for i, (sid, s) in enumerate(edl.sources.items(), 1):
        aid = f"r{i}"
        assets[sid] = aid
        url = media_path(s.path, relative_to)
        src_url = ("file://" + url) if url.startswith("/") else url
        res.append(f'<asset id="{aid}" name={quoteattr(Path(s.path).name)} start="0s" duration="{_rt(_frames(s.duration, fps), fps)}" '
                   f'hasVideo="{int(s.has_video)}" hasAudio="{int(s.has_audio)}" format="r0">'
                   f'<media-rep kind="original-media" src={quoteattr(src_url)}/></asset>')
    spine = []
    offset = 0
    windows = {}
    for ov in edl.tracks.overlay:
        windows.setdefault(ov.anchor, []).append(ov)
    if edl.transitions:
        left_out.append(f"{len(edl.transitions)} transition(s) (exported as cuts)")
    for p in plans:
        c = p.clip
        frames = p.frames - p.d_next
        inner = []
        for ov in windows.get(c.id, []):
            inner.append(f'<asset-clip ref="{assets[ov.source]}" lane="1" name={quoteattr(ov.id)} '
                         f'offset="{_rt(_frames(c.start, fps) + _frames(ov.offset, fps), fps)}" start="{_rt(_frames(ov.start, fps), fps)}" '
                         f'duration="{_rt(_frames(ov.end - ov.start, fps), fps)}"/>')
        spine.append(f'<asset-clip ref="{assets[c.source]}" name={quoteattr(c.id)} offset="{_rt(offset, fps)}" '
                     f'start="{_rt(_frames(c.start, fps), fps)}" duration="{_rt(frames, fps)}">' + "".join(inner) + "</asset-clip>")
        offset += frames
    if edl.tracks.audio and spine:
        # Audio items connect to the first spine clip, on a negative lane, with a role.
        first = spine[0][: -len("</asset-clip>")]
        extra = ""
        for it in edl.tracks.audio:
            src = edl.sources[it.source]
            length = (it.end if it.end is not None else src.duration) - it.start
            extra += (f'<asset-clip ref="{assets[it.source]}" lane="-1" name={quoteattr(it.id)} audioRole="{it.role}" '
                      f'offset="{_rt(_frames(plans[0].clip.start + (it.at or 0.0), fps), fps)}" start="{_rt(_frames(it.start, fps), fps)}" '
                      f'duration="{_rt(_frames(length, fps), fps)}"/>')
        spine[0] = first + extra + "</asset-clip>"
    if edl.tracks.graphics:
        left_out.append(f"{len(edl.tracks.graphics)} graphic(s) (export baked layers instead)")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.9">\n<resources>\n' + "\n".join(res) +
           f'\n</resources>\n<library><event name="videocli"><project name={quoteattr(edl.name)}>'
           f'<sequence format="r0" duration="{_rt(offset, fps)}" tcStart="0s" tcFormat="NDF"><spine>\n' + "\n".join(spine) +
           "\n</spine></sequence></project></event></library>\n</fcpxml>\n")
    atomic_write_text(out, xml)
    import xml.etree.ElementTree as ET

    root = ET.parse(out).getroot()
    sp = root.find(".//spine")
    clips = sp.findall("asset-clip") if sp is not None else []
    def secs(v: str) -> Fraction:
        v = v.rstrip("s")
        return Fraction(v) if "/" in v else Fraction(int(v))
    dur = sum(secs(c.get("duration", "0s")) for c in clips)
    return {"clips": len(clips), "duration": ms(float(dur)), "expected_clips": len(plans), "expected_duration": ms(offset / rate.fps),
            "left_out": left_out}


# --------------------------------------------------------------------------- OTIO


def to_otio(edl: EDL, out: Path, relative_to: Path | None = None) -> dict[str, Any]:
    from videocli.setup.profiles import require_group

    require_group("otio", "OpenTimelineIO export")
    import opentimelineio as otio

    rate, plans, total = _plans(edl)
    fps = rate.fps
    tl = otio.schema.Timeline(name=edl.name)
    tl.metadata["videocli"] = {"edl": edl.name, "rate": edl.rate, "resolution": list(edl.resolution)}
    video = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    refs = {sid: otio.schema.ExternalReference(target_url=media_path(s.path, relative_to)) for sid, s in edl.sources.items()}

    def rt(frames: int) -> Any:
        return otio.opentime.RationalTime(frames, fps)

    for i, p in enumerate(plans):
        c = p.clip
        head = p.d_prev - p.d_prev // 2  # half of the previous transition comes off this clip's head
        tail = p.d_next // 2
        sin = _frames(c.start, rate.value) + head
        frames = p.frames - head - tail
        clip = otio.schema.Clip(name=c.id, media_reference=refs[c.source].clone(),
                                source_range=otio.opentime.TimeRange(rt(sin), rt(frames)))
        clip.metadata["videocli"] = {k: v for k, v in {"reason": c.reason, "speed": c.speed if c.speed != 1 else None,
                                                       "grade": c.grade, "locked": c.locked or None}.items() if v is not None}
        if c.speed != 1.0:
            clip.effects.append(otio.schema.LinearTimeWarp(time_scalar=c.speed))
        video.append(clip)
        if p.d_next and i + 1 < len(plans):
            t = next(t for t in edl.transitions if t.after == c.id)
            video.append(otio.schema.Transition(name=t.type, transition_type=otio.schema.TransitionTypes.SMPTE_Dissolve,
                                                in_offset=rt(p.d_next // 2), out_offset=rt(p.d_next - p.d_next // 2)))
    tl.tracks.append(video)
    if edl.tracks.overlay:
        v2 = otio.schema.Track(name="V2", kind=otio.schema.TrackKind.Video)
        starts = {p.clip.id: p.start for p in plans}
        cur = 0
        for ov in sorted(edl.tracks.overlay, key=lambda o: starts.get(o.anchor, 0) + _frames(o.offset, rate.value)):
            if ov.anchor not in starts:
                continue
            s = starts[ov.anchor] + _frames(ov.offset, rate.value)
            if s > cur:
                v2.append(otio.schema.Gap(source_range=otio.opentime.TimeRange(rt(0), rt(s - cur))))
            n = _frames(ov.end - ov.start, rate.value)
            o = otio.schema.Clip(name=ov.id, media_reference=refs[ov.source].clone(),
                                 source_range=otio.opentime.TimeRange(rt(_frames(ov.start, rate.value)), rt(n)))
            o.metadata["videocli"] = {"mode": ov.mode, "pos": ov.pos, "scale": ov.scale, "opacity": ov.opacity}
            v2.append(o)
            cur = s + n
        tl.tracks.append(v2)
    for role in ("music", "sfx", "tone"):
        items = [a for a in edl.tracks.audio if a.role == role]
        if not items:
            continue
        tr = otio.schema.Track(name=f"A-{role}", kind=otio.schema.TrackKind.Audio)
        cur = 0
        for it in sorted(items, key=lambda a: a.at or 0.0):
            s = _frames(it.at or 0.0, rate.value)
            if s > cur:
                tr.append(otio.schema.Gap(source_range=otio.opentime.TimeRange(rt(0), rt(s - cur))))
            src = edl.sources[it.source]
            n = _frames((it.end if it.end is not None else src.duration) - it.start, rate.value)
            a = otio.schema.Clip(name=it.id, media_reference=refs[it.source].clone(),
                                 source_range=otio.opentime.TimeRange(rt(_frames(it.start, rate.value)), rt(n)))
            a.metadata["videocli"] = {"gain_db": it.gain_db, "duck_db": it.duck_db, "fade": it.fade}
            tr.append(a)
            cur = s + n
        tl.tracks.append(tr)
    if edl.grades:
        tl.metadata["videocli"]["grades"] = {k: g.model_dump() for k, g in edl.grades.items()}
    if edl.tracks.graphics:
        tl.metadata["videocli"]["graphics"] = [g.model_dump() for g in edl.tracks.graphics]
    otio.adapters.write_to_file(tl, str(out))
    back = otio.adapters.read_from_file(str(out))
    v1 = back.tracks[0]
    clips = [x for x in v1 if isinstance(x, otio.schema.Clip)]
    dur = v1.duration().to_seconds()
    return {"clips": len(clips), "duration": ms(dur), "expected_clips": len(plans), "expected_duration": ms(total / fps), "left_out": []}


WRITERS = {"otio": (to_otio, ".otio"), "fcpxml": (to_fcpxml, ".fcpxml"), "edl": (to_cmx3600, ".edl")}


def export(edl: EDL, fmt: str, out: Path | None, relative_to: Path | None) -> dict[str, Any]:
    if fmt not in WRITERS:
        raise UsageError("BAD_ARGUMENT", f"--to must be one of {', '.join(WRITERS)} (cube and cdl arrive with phase 7).")
    fn, ext = WRITERS[fmt]
    out = out or Path(f"{edl.name}{ext}")
    res = fn(edl, out, relative_to)
    ok = res["clips"] == res["expected_clips"] and abs(res["duration"] - res["expected_duration"]) <= 1.0 / Rate.parse(edl.rate).fps + 1e-3
    res.update({"format": fmt, "output": str(out), "round_check": "pass" if ok else "fail"})
    if not ok:
        raise RuntimeFailure("RENDER_FAILED", f"{fmt} round-check failed: wrote {res['clips']} clips / {res['duration']}s, expected "
                             f"{res['expected_clips']} / {res['expected_duration']}s.", details=res)
    return res
