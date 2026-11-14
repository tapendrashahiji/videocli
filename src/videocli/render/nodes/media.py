"""Node runners: ``segment`` (picture of one clip), ``lane`` (sound of one clip), ``picture``
(join segments by stream copy) and ``mix`` (place lanes on one timeline, normalize)."""

from __future__ import annotations

import json
import re
import wave
from pathlib import Path
from typing import Any

import numpy as np

from videocli.core.errors import RuntimeFailure
from videocli.media import ffmpeg
from videocli.render.graph import Node, entry_file

SAMPLE_RATE = 48000
CHANNELS = 2


def atempo_chain(speed: float) -> list[str]:
    """``atempo`` accepts 0.5–100 per instance; chain for slower speeds."""
    out = []
    s = speed
    while s < 0.5:
        out.append("atempo=0.5")
        s /= 0.5
    out.append(f"atempo={s:.6f}")
    return out if abs(speed - 1.0) > 1e-9 else []


def timescale(rate: str) -> int:
    num, den = (int(x) for x in rate.split("/"))
    return num * 10 if den != 1 else max(num * 100, 1000)


# --------------------------------------------------------------------------- segment


def run_segment(node: Node, out_dir: Path, inputs: list[Path]) -> str:
    p = {**node.params, **node.extra}
    w, h = p["size"]
    out = out_dir / "seg.mp4"
    frames = int(p["frames"])
    vf = [
        f"setpts=(PTS-STARTPTS)/{p['speed']:.6f}",
        f"fps={p['rate']}",
        f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=bicubic",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black",
        "setsar=1",
        f"format={p['pix_fmt']}",
        "tpad=stop_mode=clone:stop=-1",
    ]
    if p.get("label"):
        vf.insert(-1, "subtitles=label.srt:force_style='FontSize=14,Alignment=7,MarginL=8,MarginV=6,Outline=1'")
    if p["has_video"]:
        inp = ["-ss", f"{p['start']:.6f}", "-i", ffmpeg.fpath(p["path"]), "-map", "0:v:0"]
    else:
        inp = ["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={p['rate']}", "-map", "0:v:0"]
    if p.get("label"):
        (out_dir / "label.srt").write_text(f"1\n00:00:00,000 --> 99:00:00,000\n{p['label']}\n", encoding="utf-8")
    args = [
        "-y", *inp, "-an", "-sn", "-dn", "-vf", ",".join(vf), "-frames:v", str(frames),
        "-c:v", "libx264", "-preset", p["x264_preset"], "-crf", str(p["crf"]), "-bf", "0", "-g", "250",
        "-pix_fmt", p["pix_fmt"], "-video_track_timescale", str(timescale(p["rate"])), "-color_range", "tv",
        ffmpeg.fpath(out),
    ]
    ffmpeg.run(args, log_name=f"segment-{node.label}", cwd=out_dir if p.get("label") else None)
    return out.name


# --------------------------------------------------------------------------- lane


def run_lane(node: Node, out_dir: Path, inputs: list[Path]) -> str:
    p = {**node.params, **node.extra}
    out = out_dir / "lane.wav"
    n = int(p["samples"])
    fade = p["fade_ms"] / 1000.0
    out_dur = n / SAMPLE_RATE
    af = []
    if p["has_audio"]:
        inp = ["-ss", f"{p['start']:.6f}", "-t", f"{p['end'] - p['start']:.6f}", "-i", ffmpeg.fpath(p["path"]), "-map", f"0:a:{p['stream']}"]
        af += ["asetpts=PTS-STARTPTS", *atempo_chain(p["speed"])]
        if p.get("gain_db"):
            af.append(f"volume={p['gain_db']}dB")
    else:
        inp = ["-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo", "-map", "0:a:0"]
    af += [f"aresample={SAMPLE_RATE}", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
    if fade > 0 and p["has_audio"]:
        f = min(fade, out_dur / 2)
        af += [f"afade=t=in:st=0:d={f:.4f}", f"afade=t=out:st={max(0.0, out_dur - f):.4f}:d={f:.4f}"]
    af += [f"apad=whole_len={n}", f"atrim=end_sample={n}"]
    args = ["-y", *inp, "-vn", "-sn", "-af", ",".join(af), "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), ffmpeg.fpath(out)]
    ffmpeg.run(args, log_name=f"lane-{node.label}")
    return out.name


# --------------------------------------------------------------------------- picture


def _concat_line(path: Path) -> str:
    return "file '" + str(path).replace("'", "'\\''") + "'"


def run_picture(node: Node, out_dir: Path, inputs: list[Path]) -> str:
    lst = out_dir / "list.txt"
    lst.write_text("ffconcat version 1.0\n" + "\n".join(_concat_line(entry_file(d)) for d in inputs) + "\n", encoding="utf-8")
    out = out_dir / "picture.mp4"
    ffmpeg.run(
        ["-y", "-f", "concat", "-safe", "0", "-i", ffmpeg.fpath(lst), "-map", "0:v:0", "-c", "copy",
         "-video_track_timescale", str(timescale(node.params["rate"])), ffmpeg.fpath(out)],
        log_name="picture",
    )
    lst.unlink(missing_ok=True)
    return out.name


# --------------------------------------------------------------------------- mix


def _read_block(w: wave.Wave_read, start: int, count: int) -> np.ndarray:
    w.setpos(start)
    raw = w.readframes(count)
    return np.frombuffer(raw, dtype="<i2").astype(np.float32).reshape(-1, CHANNELS) / 32768.0


def mix_to_raw(placements: list[tuple[Path, int, float]], total: int, out_raw: Path, block: int = SAMPLE_RATE * 10) -> float:
    """Sum lane files at sample offsets into a raw f32le stereo file. Returns the peak."""
    handles = []
    for path, offset, gain in placements:
        w = wave.open(str(path), "rb")
        handles.append((w, offset, w.getnframes(), gain))
    peak = 0.0
    try:
        with open(out_raw, "wb") as fh:
            for b0 in range(0, total, block):
                b1 = min(total, b0 + block)
                buf = np.zeros((b1 - b0, CHANNELS), dtype=np.float32)
                for w, off, frames, gain in handles:
                    lo, hi = max(b0, off), min(b1, off + frames)
                    if hi <= lo:
                        continue
                    data = _read_block(w, lo - off, hi - lo)
                    buf[lo - b0: lo - b0 + len(data)] += data * gain
                peak = max(peak, float(np.abs(buf).max()) if len(buf) else 0.0)
                fh.write(buf.astype("<f4").tobytes())
    finally:
        for w, *_ in handles:
            w.close()
    return peak


_JSON_BLOCK = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


def loudnorm_measure(raw: Path, target: float, tp: float) -> dict[str, Any] | None:
    res = ffmpeg.run(
        ["-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-i", ffmpeg.fpath(raw),
         "-af", f"loudnorm=I={target}:TP={tp}:LRA=11:print_format=json", "-f", "null", "-"],
        log_name="loudnorm-measure", capture=True,
    )
    m = _JSON_BLOCK.search(res.stderr)
    if not m:
        raise RuntimeFailure("RENDER_FAILED", "loudnorm did not report measurements.", details={"log": str(res.log)})
    data = json.loads(m.group(0))
    try:
        if float(data["input_i"]) < -70:  # silence: nothing to normalize
            return None
    except ValueError:
        return None
    return data


def run_mix(node: Node, out_dir: Path, inputs: list[Path]) -> str:
    p = {**node.params, **node.extra}
    total = int(p["samples"])
    raw = out_dir / "mix.f32"
    placements = [(entry_file(d), int(off), float(gain)) for d, (off, gain) in zip(inputs, p["placements"], strict=False)]
    mix_to_raw(placements, total, raw)
    out = out_dir / "sound.wav"
    af: list[str] = []
    measured = None
    if p["normalize"]:
        measured = loudnorm_measure(raw, p["target_lufs"], p["true_peak_db"])
    if measured:
        af.append(
            f"loudnorm=I={p['target_lufs']}:TP={p['true_peak_db']}:LRA=11:measured_I={measured['input_i']}:"
            f"measured_TP={measured['input_tp']}:measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']}:linear=true"
        )
    af += [f"aresample={SAMPLE_RATE}", f"apad=whole_len={total}", f"atrim=end_sample={total}"]
    ffmpeg.run(
        ["-y", "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-i", ffmpeg.fpath(raw),
         "-af", ",".join(af), "-c:a", "pcm_s24le", "-ar", str(SAMPLE_RATE), ffmpeg.fpath(out)],
        log_name="mix",
    )
    raw.unlink(missing_ok=True)
    (out_dir / "mix.json").write_text(json.dumps({"measured": measured, "samples": total}), encoding="utf-8")
    return out.name
