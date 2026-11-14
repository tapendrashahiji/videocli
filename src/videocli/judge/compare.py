"""``compare``: the original and a render side by side, so a person sees the edit at a glance.

Sync mode keeps both halves on the original's clock: while the original plays a part
that was cut, the edited half holds its last frame under a "CUT" badge. That needs a
straight cut-down (one source, clips in source order, normal speed, plain cuts). Any
other EDL uses drift mode: the two files simply play next to each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from videocli.core.errors import UsageError

LABEL_H_FRACTION = 0.075


@dataclass
class Gap:
    start: float  # original time
    end: float

    @property
    def seconds(self) -> float:
        return self.end - self.start


@dataclass
class SyncPlan:
    """Kept pieces in original time and where each sits in the render."""

    pieces: list[tuple[float, float, float]]  # (source start, source end, output start)
    lead: float  # original seconds before the first kept piece
    gaps: list[Gap]  # cut stretches, in original time
    source_duration: float


def sync_plan(edl: Any, source_duration: float) -> SyncPlan:
    """Map a straight cut-down onto the original's timeline, or say why it can't be."""
    main = list(edl.tracks.main)
    if not main:
        raise UsageError("BAD_ARGUMENT", f"EDL '{edl.name}' has no clips.")
    why = None
    if len({c.source for c in main}) > 1:
        why = "it uses more than one source"
    elif any(c.speed != 1.0 or c.ramp or c.freeze for c in main):
        why = "a clip changes speed or freezes"
    elif any(t.type != "cut" for t in edl.transitions):
        why = "it has transitions"
    elif any(b.start < a.end - 1e-6 for a, b in zip(main, main[1:], strict=False)):
        why = "clips are reordered or overlap"
    if why:
        raise UsageError(
            "COMPARE_NOT_SYNCABLE",
            f"Can't line up '{edl.name}' with its source: {why}.",
            hint="Add --drift to play the two files side by side without syncing.",
        )
    tm = edl.timemap()
    pieces = [(c.start, c.end, sp.out_start) for c, sp in zip(main, tm.spans, strict=True)]
    gaps = [Gap(a.end, b.start) for a, b in zip(main, main[1:], strict=False) if b.start - a.end > 1e-3]
    if source_duration - main[-1].end > 1e-3:
        gaps.append(Gap(main[-1].end, source_duration))
    lead = main[0].start
    if lead > 1e-3:
        gaps.insert(0, Gap(0.0, lead))
    return SyncPlan(pieces, lead, gaps, source_duration)


def label_png(text: str, height: int, out: Path, *, color: tuple[int, int, int] = (0, 0, 0)) -> Path:
    """A rounded, semi-opaque label; PNG so no ffmpeg font support is needed."""
    from PIL import Image, ImageDraw, ImageFont

    from videocli.graphics.templates import FONTS

    font = ImageFont.truetype(str(FONTS / "NotoSans-Bold.ttf"), max(10, int(height * 0.62)))
    box = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), text, font=font)
    pad = int(height * 0.35)
    w = box[2] - box[0] + 2 * pad
    img = Image.new("RGBA", (w, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, height - 1), radius=height // 4, fill=(*color, 190))
    d.text((pad - box[0], (height - (box[3] - box[1])) / 2 - box[1]), text, font=font, fill=(255, 255, 255, 255))
    img.save(out)
    return out


def _fmt_s(s: float) -> str:
    return f"{s:.1f}s"


def build_args(
    original: Path,
    edited: Path,
    out: Path,
    *,
    height: int,
    plan: SyncPlan | None,
    original_duration: float,
    edited_duration: float,
    labels: dict[str, Path],
    badges: list[tuple[Gap, Path]],
) -> tuple[list[str], float]:
    """ffmpeg arguments and the output duration."""
    args = ["-y", "-i", str(original), "-i", str(edited)]
    for p in [labels["left"], labels["right"], *(b for _, b in badges)]:
        args += ["-loop", "1", "-i", str(p)]
    f: list[str] = [f"[0:v]scale=-2:{height},setsar=1[L0]", f"[1:v]scale=-2:{height},setsar=1[R0]"]
    if plan is None:
        duration = max(original_duration, edited_duration)  # drift: both play to the longer end
        right = "R0"
    else:
        duration = plan.source_duration
        n = len(plan.pieces)
        f.append(f"[R0]split={n}" + "".join(f"[r{i}]" for i in range(n)))
        parts = []
        for i, (s, e, o) in enumerate(plan.pieces):
            hold = (plan.pieces[i + 1][0] if i + 1 < n else plan.source_duration) - e
            chain = f"[r{i}]trim=start={o:.6f}:duration={e - s:.6f},setpts=PTS-STARTPTS"
            if i == 0 and plan.lead > 1e-3:
                chain += f",tpad=start_mode=clone:start_duration={plan.lead:.6f}"
            if hold > 1e-3:
                chain += f",tpad=stop_mode=clone:stop_duration={hold:.6f}"
            f.append(chain + f"[p{i}]")
            parts.append(f"[p{i}]")
        f.append("".join(parts) + f"concat=n={n}:v=1:a=0[R1]")
        right = "R1"
    margin = max(4, height // 40)
    f.append(f"[L0][2:v]overlay={margin}:{margin}:shortest=1[L1]")
    cur = f"[{right}][3:v]overlay={margin}:{margin}:shortest=1"
    for k, (g, _) in enumerate(badges):
        f.append(f"{cur}[B{k}]")
        cur = f"[B{k}][{4 + k}:v]overlay=(W-w)/2:H-h-{margin * 2}:enable='between(t,{g.start:.3f},{g.end:.3f})':shortest=1"
    f.append(f"{cur}[R9]")
    f.append("[L1][R9]hstack=inputs=2[v]")
    audio = "0:a?" if plan is not None else "1:a?"
    args += [
        "-filter_complex", ";".join(f), "-map", "[v]", "-map", audio, "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out),
    ]
    return args, duration


def compare(ws: Any, edited: Path, edl: Any, out: Path, *, drift: bool = False) -> dict[str, Any]:
    from videocli.core import progress
    from videocli.core.errors import InputError
    from videocli.media import ffmpeg
    from videocli.media import probe as probemod

    sources = {c.source for c in edl.tracks.main}
    if not sources:
        raise UsageError("BAD_ARGUMENT", f"EDL '{edl.name}' has no clips.")
    src_id = edl.tracks.main[0].source
    original = Path(edl.sources[src_id].path)
    if not original.is_file():
        raise InputError("FILE_NOT_FOUND", f"The EDL's source is missing: {original}", hint="Restore the source file at that path.")
    po = probemod.probe_path(original)
    pe = probemod.probe_path(edited)
    if po.video is None or pe.video is None:
        raise InputError("NO_VIDEO", "Both the source and the edited file need a video stream.")
    plan = None if drift else sync_plan(edl, po.duration)
    height = po.video.display_height or po.video.height
    height -= height % 2
    work = ws.dir("reports/compare") / edl.name
    work.mkdir(parents=True, exist_ok=True)
    lh = max(16, int(height * LABEL_H_FRACTION))
    labels = {
        "left": label_png("ORIGINAL", lh, work / "label-left.png"),
        "right": label_png("EDITED", lh, work / "label-right.png", color=(20, 90, 200)),
    }
    badges = []
    for k, g in enumerate(plan.gaps if plan else []):
        badges.append((g, label_png(f"CUT  -{_fmt_s(g.seconds)}", lh, work / f"badge-{k}.png", color=(200, 30, 30))))
    args, duration = build_args(original, edited, out, height=height, plan=plan,
                                original_duration=po.duration, edited_duration=pe.duration,
                                labels=labels, badges=badges)
    with progress.step_ctx("compare"):
        ffmpeg.run(args, log_name=f"compare-{edl.name}", progress_total=duration)
    removed = round(sum(g.seconds for g in plan.gaps), 3) if plan else None
    return {
        "output": out,
        "edl": edl.name,
        "mode": "drift" if drift else "sync",
        "original": str(original),
        "edited": str(edited),
        "duration": round(duration, 3),
        "cuts": [{"from": round(g.start, 3), "to": round(g.end, 3), "seconds": round(g.seconds, 3)} for g in plan.gaps] if plan else [],
        "removed_seconds": removed,
        "audio": "original" if plan else "edited",
    }
