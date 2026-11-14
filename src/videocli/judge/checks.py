"""Verify checks (SPEC §10.9). One function per check; each returns a status and items.

Statuses: ``pass``, ``warn``, ``fail``, ``skip``. Items carry output times; the report maps
them back to clips and source times.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from videocli.core.timeutil import ms, normalize
from videocli.edl.model import EDL
from videocli.media import ffmpeg


@dataclass
class CheckResult:
    name: str
    status: str
    message: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)
    measured: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "status": self.status, "message": self.message, "items": self.items}
        if self.measured:
            out["measured"] = self.measured
        return out


@dataclass
class Context:
    output: Path
    edl: EDL
    probe: Any  # ProbeResult of the output
    settings: Any
    transcripts: dict[str, Any]
    scan: dict[str, Any] = field(default_factory=dict)  # one-pass detector results
    source_cache: dict[tuple, list] = field(default_factory=dict)

    @property
    def rate(self) -> float:
        from videocli.core.timeutil import Rate

        return Rate.parse(self.edl.rate).fps

    @property
    def expected_duration(self) -> float:
        from videocli.core.timeutil import Rate
        from videocli.render.timeline import plan_clips

        r = Rate.parse(self.edl.rate)
        _, frames = plan_clips(self.edl, r.value)
        return frames / r.fps


# --------------------------------------------------------------------------- one-pass scan

_BLACK = re.compile(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)")
_FREEZE_S = re.compile(r"freeze_start:\s*([\d.]+)")
_FREEZE_E = re.compile(r"freeze_end:\s*([\d.]+)")


def parse_black(stderr: str) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for a, b in _BLACK.findall(stderr)]


def parse_freeze(stderr: str, duration: float) -> list[tuple[float, float]]:
    out = []
    start = None
    for line in stderr.splitlines():
        m = _FREEZE_S.search(line)
        if m:
            start = float(m.group(1))
            continue
        m = _FREEZE_E.search(line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    if start is not None:
        out.append((start, duration))
    return out


def scan_output(ctx: Context) -> dict[str, Any]:
    """blackdetect, freezedetect, silencedetect and ebur128 in a single decode of the output."""
    from videocli.analyze.loudness import parse_ebur128
    from videocli.analyze.silence import parse_silencedetect

    s = ctx.settings
    dur = ctx.probe.duration
    args = ["-i", ffmpeg.fpath(ctx.output)]
    has_v = ctx.probe.video is not None
    has_a = bool(ctx.probe.audio)
    if has_v:
        args += ["-map", "0:v:0", "-vf", f"blackdetect=d={s.verify__black_min}:pix_th=0.10,freezedetect=n=-60dB:d={s.verify__freeze_min}"]
    if has_a:
        args += ["-map", "0:a:0", "-af", f"silencedetect=noise={s.silence__threshold_db}dB:d={s.verify__silence_gap},ebur128=peak=true:framelog=quiet"]
    args += ["-f", "null", "-"]
    res = ffmpeg.run(args, log_name="verify-scan", capture=True, progress_total=dur or None)
    out: dict[str, Any] = {}
    if has_v:
        out["black"] = parse_black(res.stderr)
        out["frozen"] = parse_freeze(res.stderr, dur)
    if has_a:
        out["silence"] = parse_silencedetect(res.stderr, dur)
        out["loudness"] = parse_ebur128(res.stderr)
    return out


def _source_ranges(ctx: Context, a: float, b: float) -> list[tuple[str, float, float]]:
    """Source ranges that play during output [a, b)."""
    tm = ctx.edl.timemap()
    out = []
    for sp in tm.spans:
        lo, hi = max(a, sp.out_start), min(b, sp.out_end)
        if hi > lo:
            out.append((sp.source, sp.start + (lo - sp.out_start) * sp.speed, sp.start + (hi - sp.out_start) * sp.speed))
    return out


def _source_detect(ctx: Context, kind: str, sid: str, a: float, b: float) -> list[tuple[float, float]]:
    key = (kind, sid, round(a, 3), round(b, 3))
    if key in ctx.source_cache:
        return ctx.source_cache[key]
    src = ctx.edl.sources[sid]
    s = ctx.settings
    pad = 0.5
    start = max(0.0, a - pad)
    vf = f"blackdetect=d={min(s.verify__black_min, 0.2)}:pix_th=0.10" if kind == "black" else f"freezedetect=n=-60dB:d={min(s.verify__freeze_min, 0.5)}"
    res = ffmpeg.run(["-ss", f"{start:.3f}", "-t", f"{b - start + pad:.3f}", "-i", ffmpeg.fpath(src.path), "-map", "0:v:0", "-vf", vf, "-f", "null", "-"],
                     log_name=f"verify-source-{kind}", capture=True)
    found = parse_black(res.stderr) if kind == "black" else parse_freeze(res.stderr, b - start + pad)
    ranges = [(start + x, start + y) for x, y in found]
    ctx.source_cache[key] = ranges
    return ranges


def _in_source_too(ctx: Context, kind: str, a: float, b: float) -> bool:
    """True when the source material behind output [a, b) is also black/frozen (mostly)."""
    parts = _source_ranges(ctx, a, b)
    if not parts:
        return False
    total = covered = 0.0
    for sid, x, y in parts:
        if not ctx.edl.sources[sid].has_video:
            covered += y - x
            total += y - x
            continue
        total += y - x
        for p, q in _source_detect(ctx, kind, sid, x, y):
            covered += max(0.0, min(y, q) - max(x, p))
    return total > 0 and covered / total >= 0.8


# --------------------------------------------------------------------------- checks


def check_duration(ctx: Context) -> CheckResult:
    exp = ctx.expected_duration
    got = ctx.probe.duration
    tol = max(ctx.settings.verify__duration_tolerance, 1.0 / ctx.rate)
    m = {"expected": ms(exp), "actual": ms(got), "tolerance": round(tol, 3)}
    if abs(got - exp) <= tol:
        return CheckResult("duration", "pass", f"{got:.3f}s as planned", measured=m)
    return CheckResult("duration", "fail", f"Output is {got:.3f}s; the EDL plans {exp:.3f}s.", measured=m,
                       items=[{"at": ms(min(got, exp)), "expected": ms(exp), "actual": ms(got)}])


def check_streams(ctx: Context) -> CheckResult:
    items = []
    needs_video = any(s.has_video for s in ctx.edl.sources.values())
    if needs_video and ctx.probe.video is None:
        items.append({"problem": "missing video stream"})
    if not ctx.probe.audio:
        items.append({"problem": "missing audio stream"})
    v = ctx.probe.video
    m = {}
    if v is not None:
        m = {"video": f"{v.codec} {v.width}x{v.height} @ {v.fps}", "pix_fmt": v.pix_fmt}
        if v.vfr:
            items.append({"problem": "variable frame rate output"})
    if ctx.probe.audio:
        a = ctx.probe.audio[0]
        m["audio"] = f"{a.codec} {a.sample_rate} Hz {a.channels} ch"
    if items:
        return CheckResult("streams", "fail", "; ".join(i["problem"] for i in items), items=items, measured=m)
    return CheckResult("streams", "pass", "video and audio present", measured=m)


def check_av_sync(ctx: Context) -> CheckResult:
    v = ctx.probe.video
    a = ctx.probe.audio[0] if ctx.probe.audio else None
    if v is None or a is None or v.duration is None or a.duration is None:
        return CheckResult("av_sync", "skip", "needs both streams with durations")
    diff = abs(v.duration - a.duration)
    tol = ctx.settings.verify__av_sync_tolerance
    m = {"video": ms(v.duration), "audio": ms(a.duration), "difference": ms(diff), "tolerance": tol}
    if diff <= tol:
        return CheckResult("av_sync", "pass", f"streams differ by {diff * 1000:.0f} ms", measured=m)
    return CheckResult("av_sync", "fail", f"Audio and video lengths differ by {diff * 1000:.0f} ms.", measured=m,
                       items=[{"at": ms(min(v.duration, a.duration)), "difference": ms(diff)}])


def _range_check(ctx: Context, name: str, kind: str, ranges: list[tuple[float, float]], what: str) -> CheckResult:
    items = []
    ignored = 0
    for a, b in normalize(ranges):
        if _in_source_too(ctx, kind, a, b):
            ignored += 1
            continue
        items.append({"from": ms(a), "to": ms(b), "duration": ms(b - a), "at": ms(a)})
    m = {"found": len(ranges), "ignored_as_in_source": ignored}
    if items:
        return CheckResult(name, "fail", f"{len(items)} {what} stretch(es) not present in the source.", items=items, measured=m)
    msg = f"no {what} stretches" if not ranges else f"{ignored} {what} stretch(es), all present in the source"
    return CheckResult(name, "pass", msg, measured=m)


def check_black_frames(ctx: Context) -> CheckResult:
    if "black" not in ctx.scan:
        return CheckResult("black_frames", "skip", "no video stream")
    return _range_check(ctx, "black_frames", "black", ctx.scan["black"], "black")


def check_frozen_frames(ctx: Context) -> CheckResult:
    if "frozen" not in ctx.scan:
        return CheckResult("frozen_frames", "skip", "no video stream")
    return _range_check(ctx, "frozen_frames", "frozen", ctx.scan["frozen"], "frozen")


def joins(ctx: Context) -> list[tuple[float, str, str]]:
    tm = ctx.edl.timemap()
    return [(sp.out_end, sp.id, nxt.id) for sp, nxt in zip(tm.spans, tm.spans[1:], strict=False)]


def click_score(samples: np.ndarray, idx: int, guard: int = 48) -> tuple[float, float]:
    """Sample step at ``idx`` versus the typical step around it (ratio, absolute step)."""
    lo, hi = max(1, idx - guard), min(len(samples), idx + guard)
    if hi - lo < 4:
        return 0.0, 0.0
    d = np.abs(np.diff(samples[lo - 1:hi]))
    step = float(d.max())
    typical = float(np.median(d)) + 1e-4
    return step / typical, step


def check_audio_clicks(ctx: Context) -> CheckResult:
    from videocli.media.audio import decode_pcm

    if not ctx.probe.audio:
        return CheckResult("audio_clicks", "skip", "no audio stream")
    js = joins(ctx)
    if not js:
        return CheckResult("audio_clicks", "pass", "no joins")
    rate = 48000
    thr = ctx.settings.verify__click_threshold
    items = []
    worst = 0.0
    for t, a, b in js:
        start = max(0.0, t - 0.02)
        pcm = decode_pcm(ctx.output, start=start, duration=0.04, rate=rate, channels=1)
        if len(pcm) < 8:
            continue
        idx = int(round((t - start) * rate))
        ratio, step = click_score(pcm, min(idx, len(pcm) - 1))
        worst = max(worst, step)
        if step > thr and ratio > 8:
            items.append({"at": ms(t), "clips": [a, b], "step": round(step, 3), "ratio": round(ratio, 1)})
    m = {"joins": len(js), "worst_step": round(worst, 3), "threshold": thr}
    if items:
        # Reported as a warning at first (see the phase-3 risk table) until calibrated on real renders.
        return CheckResult("audio_clicks", "warn", f"{len(items)} join(s) with a sharp audio step.", items=items, measured=m)
    return CheckResult("audio_clicks", "pass", f"{len(js)} joins, no sharp steps", measured=m)


def check_silence_gaps(ctx: Context) -> CheckResult:
    if "silence" not in ctx.scan:
        return CheckResult("silence_gaps", "skip", "no audio stream")
    gap = ctx.settings.verify__silence_gap
    items = [{"from": ms(a), "to": ms(b), "duration": ms(b - a), "at": ms(a)} for a, b in ctx.scan["silence"] if b - a >= gap]
    if items:
        return CheckResult("silence_gaps", "warn", f"{len(items)} silence(s) of {gap:.1f}s or more remain.", items=items,
                           measured={"min_gap": gap})
    return CheckResult("silence_gaps", "pass", f"no silences of {gap:.1f}s or more", measured={"min_gap": gap})


def check_loudness(ctx: Context) -> CheckResult:
    if "loudness" not in ctx.scan:
        return CheckResult("loudness", "skip", "no audio stream")
    lo = ctx.scan["loudness"]
    il, tp = lo.get("integrated_lufs"), lo.get("true_peak_db")
    target, ceiling = ctx.edl.mix.target_lufs, ctx.edl.mix.true_peak_db
    tol = ctx.settings.verify__loudness_tolerance
    m = {"integrated_lufs": il, "true_peak_db": tp, "target_lufs": target, "ceiling_db": ceiling, "tolerance": tol}
    if il is None:
        return CheckResult("loudness", "warn", "output is silent", measured=m)
    if not ctx.edl.mix.normalize:
        return CheckResult("loudness", "pass", f"{il:.1f} LUFS (normalization off)", measured=m)
    if abs(il - target) > tol:
        return CheckResult("loudness", "fail", f"Integrated loudness {il:.1f} LUFS is off the {target:.0f} LUFS target.", measured=m)
    if tp is not None and tp > ceiling + 0.5:
        return CheckResult("loudness", "warn", f"True peak {tp:.1f} dBTP is above the {ceiling:.1f} dBTP ceiling.", measured=m)
    return CheckResult("loudness", "pass", f"{il:.1f} LUFS, peak {tp:.1f} dBTP", measured=m)


def check_clipped_words(ctx: Context) -> CheckResult:
    from videocli.edl.lint import lint

    if not any(ctx.transcripts.values()):
        return CheckResult("clipped_words", "skip", "no transcript")
    res = lint(ctx.edl, transcripts=ctx.transcripts, check_files=False, min_clip=0, min_cut=0)
    tm = ctx.edl.timemap()
    items = []
    for i in res["issues"]:
        if i["rule"] != "clipped_word":
            continue
        sp = tm.span(i["clip"])
        at = sp.out_start if (sp and i["edge"] == "start") else (sp.out_end if sp else 0.0)
        items.append({"at": ms(at), "clip": i["clip"], "word": i["word"], "edge": i["edge"], "fix": i.get("fix")})
    if items:
        return CheckResult("clipped_words", "fail", f"{len(items)} clip edge(s) cut through a word.", items=items)
    return CheckResult("clipped_words", "pass", "no clip edge cuts through a word")


def _sample_y(path: Path, times: list[float], w: int = 160) -> list[np.ndarray]:
    """Luma planes (8-bit codes) of frames at ``times``."""
    out = []
    for t in times:
        res = ffmpeg.run(["-ss", f"{t:.3f}", "-i", ffmpeg.fpath(path), "-map", "0:v:0", "-frames:v", "1",
                          "-vf", f"scale={w}:-2:in_range=tv:out_range=tv,format=gray", "-f", "rawvideo", "-"],
                         log_name="verify-sample", capture=True, check=False)
        if res.stdout:
            out.append(np.frombuffer(res.stdout, dtype=np.uint8))
    return out


def _graded(ctx: Context) -> bool:
    from videocli.color.render import timeline_graded

    return timeline_graded(ctx.edl)


def check_legal_range(ctx: Context) -> CheckResult:
    """Broadcast-legal luma (16-235) on sampled frames of a graded render."""
    if ctx.probe.video is None:
        return CheckResult("legal_range", "skip", "no video stream")
    if not _graded(ctx):
        return CheckResult("legal_range", "skip", "not graded")
    dur = ctx.probe.duration
    times = [dur * (i + 0.5) / 12 for i in range(12)]
    items = []
    worst = 0.0
    for t, y in zip(times, _sample_y(ctx.output, times), strict=False):
        share = float(((y < 16) | (y > 235)).mean())
        worst = max(worst, share)
        if share > 0.01:
            items.append({"at": ms(t), "illegal_share": round(share, 4)})
    m = {"worst_share": round(worst, 4)}
    if items:
        return CheckResult("legal_range", "warn", f"{len(items)} sampled frame(s) outside 16-235.", items=items, measured=m)
    return CheckResult("legal_range", "pass", "sampled frames within legal range", measured=m)


def check_added_clipping(ctx: Context) -> CheckResult:
    """Grades must not clip highlights or crush shadows that the source kept."""
    if ctx.probe.video is None or not _graded(ctx):
        return CheckResult("added_clipping", "skip", "not graded")
    tm = ctx.edl.timemap()
    items = []
    for sp in tm.spans:
        t_out = (sp.out_start + sp.out_end) / 2
        t_src = sp.start + (t_out - sp.out_start) * sp.speed
        src = ctx.edl.sources[sp.source]
        if not src.has_video:
            continue
        yo = _sample_y(ctx.output, [t_out])
        ys = _sample_y(Path(src.path), [t_src])
        if not yo or not ys:
            continue
        hi_o, hi_s = float((yo[0] >= 234).mean()), float((ys[0] >= 234).mean())
        lo_o, lo_s = float((yo[0] <= 17).mean()), float((ys[0] <= 17).mean())
        if hi_o - hi_s > 0.02 or lo_o - lo_s > 0.02:
            items.append({"at": ms(t_out), "clip": sp.id, "clipped_added": round(hi_o - hi_s, 4), "crushed_added": round(lo_o - lo_s, 4),
                          "fix": f"videocli grade set {ctx.edl.name} --clip {sp.id} --op 'rolloff threshold=0.85'"})
    if items:
        return CheckResult("added_clipping", "fail", f"{len(items)} clip(s) gained clipping from the grade.", items=items)
    return CheckResult("added_clipping", "pass", "no clipping added by the grade")


CHECKS = {
    "duration": check_duration,
    "streams": check_streams,
    "av_sync": check_av_sync,
    "black_frames": check_black_frames,
    "frozen_frames": check_frozen_frames,
    "audio_clicks": check_audio_clicks,
    "silence_gaps": check_silence_gaps,
    "loudness": check_loudness,
    "clipped_words": check_clipped_words,
    "legal_range": check_legal_range,
    "added_clipping": check_added_clipping,
}
SCAN_NEEDED = {"black_frames", "frozen_frames", "silence_gaps", "loudness"}
