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

ASPECTS = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0, "4:5": 0.8, "4:3": 4 / 3}


def video_chain(speed: float, rate: str, w: int, h: int, pix_fmt: str, *, crop: list | None = None, hold: bool = False) -> list[str]:
    """Conform one picture input to the timeline: speed, rate, (hold), reframe crop or fit, format, pad frames."""
    vf = [f"setpts=(PTS-STARTPTS)/{speed:.6f}", f"fps={rate}"]
    if hold:
        vf.append("trim=end_frame=1")
    if crop:
        ar = ASPECTS[crop[0]]
        cx = float(crop[1])
        vf += [
            f"crop=w='min(iw,ih*{ar:.6f})':h='min(ih,iw/{ar:.6f})':x='(iw-ow)*{cx:.4f}':y='(ih-oh)/2'",
            f"scale={w}:{h}:flags=bicubic",
        ]
    else:
        vf += [f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=bicubic", f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"]
    vf += ["setsar=1", f"format={pix_fmt}", "tpad=stop_mode=clone:stop=-1"]
    return vf


def picture_input(p: dict, inputs: list[Path], start: float, w: int, h: int) -> list[str]:
    if p.get("baked") is not None and inputs:
        return ["-ss", f"{max(0.0, start - p['offset']):.6f}", "-i", ffmpeg.fpath(entry_file(inputs[0]))]
    if p["has_video"]:
        return ["-ss", f"{start:.6f}", "-i", ffmpeg.fpath(p["path"])]
    return ["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={p.get('rate', '25/1')}"]


def encode_args(p: dict) -> list[str]:
    return ["-c:v", "libx264", "-preset", p["x264_preset"], "-crf", str(p["crf"]), "-bf", "0", "-g", "250",
            "-pix_fmt", p["pix_fmt"], "-video_track_timescale", str(timescale(p["rate"])), "-color_range", "tv"]


def run_segment(node: Node, out_dir: Path, inputs: list[Path]) -> str:
    p = {**node.params, **node.extra}
    w, h = p["size"]
    out = out_dir / "seg.mp4"
    frames = int(p["frames"])
    vf = video_chain(p["speed"], p["rate"], w, h, p["pix_fmt"], crop=p.get("crop"), hold=bool(p.get("hold")))
    if p.get("label"):
        vf.insert(-1, "subtitles=label.srt:force_style='FontSize=14,Alignment=7,MarginL=8,MarginV=6,Outline=1'")
    if p["has_video"] or p.get("baked") is not None:
        inp = [*picture_input(p, inputs, p["start"], w, h), "-map", "0:v:0"]
    else:
        inp = ["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={p['rate']}", "-map", "0:v:0"]
    if p.get("label"):
        (out_dir / "label.srt").write_text(f"1\n00:00:00,000 --> 99:00:00,000\n{p['label']}\n", encoding="utf-8")
    args = ["-y", *inp, "-an", "-sn", "-dn", "-vf", ",".join(vf), "-frames:v", str(frames), *encode_args(p), ffmpeg.fpath(out)]
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
    fin = float(p.get("fade_in", fade))
    fout = float(p.get("fade_out", fade))
    if (fin > 0 or fout > 0) and p["has_audio"]:
        fi = min(fin, out_dur / 2)
        fo = min(fout, out_dur / 2)
        if fi > 0:
            af.append(f"afade=t=in:st=0:d={fi:.4f}")
        if fo > 0:
            af.append(f"afade=t=out:st={max(0.0, out_dur - fo):.4f}:d={fo:.4f}")
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


def run_music(node: Node, out_dir: Path, inputs: list[Path]) -> str:
    """Music, effects or room tone: decoded, looped or trimmed to length, faded; raw s16le stereo."""
    p = {**node.params, **node.extra}
    n = int(p["samples"])
    out = out_dir / "music.s16"
    inp = []
    if p.get("loop") and not p.get("pingpong"):
        inp += ["-stream_loop", "-1"]
    inp += ["-ss", f"{p['start']:.6f}"]
    if p.get("end") is not None and not p.get("loop") or p.get("end") is not None and p.get("pingpong"):
        inp += ["-t", f"{p['end'] - p['start']:.6f}"]
    inp += ["-i", ffmpeg.fpath(p["path"])]
    pre = f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"
    if p.get("pingpong"):
        seg = round((p["end"] - p["start"]) * SAMPLE_RATE) * 2
        graph = f"[0:a]{pre},asplit[x][y];[y]areverse[r];[x][r]concat=n=2:v=0:a=1,aloop=loop=-1:size={seg}"
    elif p.get("loop") and p.get("end") is not None:
        seg = round((p["end"] - p["start"]) * SAMPLE_RATE)
        graph = f"[0:a]{pre},atrim=end_sample={seg},aloop=loop=-1:size={seg}"
    else:
        graph = f"[0:a]{pre}"
    out_dur = n / SAMPLE_RATE
    fade = min(float(p.get("fade") or 0.0), out_dur / 2)
    graph += f",atrim=end_sample={n},apad=whole_len={n}"
    if fade > 0:
        graph += f",afade=t=in:st=0:d={fade:.4f},afade=t=out:st={max(0.0, out_dur - fade):.4f}:d={fade:.4f}"
    graph += "[a]"
    ffmpeg.run(["-y", *inp, "-filter_complex", graph, "-map", "[a]", "-f", "s16le", "-ac", "2", "-ar", str(SAMPLE_RATE), ffmpeg.fpath(out)],
               log_name=f"music-{node.label}")
    return out.name


def _env_from_ranges(ranges: list[list[int]], total: int, duck_db: float, rate: int = 100,
                     attack: float = 0.08, release: float = 0.4) -> np.ndarray:
    """Gain envelope at ``rate`` Hz: ``duck_db`` during ranges (smoothed attack/release), 0 dB elsewhere."""
    n = total * rate // SAMPLE_RATE + 2
    target = np.ones(n, dtype=np.float32)
    duck = 10 ** (duck_db / 20)
    for a, b in ranges:
        target[max(0, a * rate // SAMPLE_RATE): min(n, b * rate // SAMPLE_RATE + 1)] = duck
    env = np.empty_like(target)
    ka = 1 - np.exp(-1 / (attack * rate))
    kr = 1 - np.exp(-1 / (release * rate))
    g = 1.0
    for i, t in enumerate(target):
        k = ka if t < g else kr
        g += (t - g) * k
        env[i] = g
    return env


def _speech_from_energy(dialogue: np.ndarray, total: int, thresh_db: float = -40.0) -> list[list[int]]:
    """Sidechain fallback: speech where the dialogue's 20 ms RMS exceeds a threshold."""
    hop = SAMPLE_RATE // 50
    out: list[list[int]] = []
    start = None
    for i in range(0, total, hop):
        blk = np.asarray(dialogue[i:i + hop])
        on = blk.size and 20 * np.log10(float(np.sqrt(np.mean(blk.astype(np.float64) ** 2))) + 1e-9) > thresh_db
        if on and start is None:
            start = i
        elif not on and start is not None:
            out.append([start, i])
            start = None
    if start is not None:
        out.append([start, total])
    return out


def _cleanup(raw: Path, cleanup: dict) -> Path:
    chain = []
    if cleanup.get("highpass"):
        chain.append(f"highpass=f={cleanup['highpass']}")
    if cleanup.get("denoise"):
        chain.append(f"afftdn=nr={6 + 24 * float(cleanup['denoise']):.1f}:nf=-50")
    if cleanup.get("level"):
        chain.append("dynaudnorm=f=250:g=15:p=0.9")
    if cleanup.get("compress"):
        chain.append("acompressor=threshold=-20dB:ratio=3:attack=10:release=200:makeup=2")
    if not chain:
        return raw
    out = raw.with_name("dialogue-clean.f32")
    ffmpeg.run(["-y", "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-i", ffmpeg.fpath(raw), "-af", ",".join(chain),
                "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), ffmpeg.fpath(out)], log_name="cleanup")
    return out


def _write_wav(raw: Path, out: Path, total: int, af: list[str] | None = None) -> None:
    chain = [*(af or []), f"aresample={SAMPLE_RATE}", f"apad=whole_len={total}", f"atrim=end_sample={total}"]
    ffmpeg.run(["-y", "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-i", ffmpeg.fpath(raw),
                "-af", ",".join(chain), "-c:a", "pcm_s24le", "-ar", str(SAMPLE_RATE), ffmpeg.fpath(out)], log_name="mix-write")


def run_mix(node: Node, out_dir: Path, inputs: list[Path]) -> str:
    p = node.params
    total = int(p["samples"])
    n_d = len(p["placements"])
    raw = out_dir / "mix.f32"
    placements = [(entry_file(d), int(off), float(gain)) for d, (off, gain) in zip(inputs[:n_d], p["placements"], strict=False)]
    others = p.get("others") or []
    extra_in = inputs[n_d:]
    cleanup = p.get("cleanup") or {}
    stems = bool(p.get("stems"))
    if not others and not cleanup and not stems:
        # Phase-2 path: dialogue lanes only (kept byte-identical for spine-only edits).
        mix_to_raw(placements, total, raw)
        return _finish(p, raw, out_dir, total)

    # Overlay sound (wav lanes) joins the dialogue stem; music/sfx/tone are raw s16 files.
    d_place = list(placements)
    music_items = []
    for d, o in zip(extra_in, others, strict=False):
        f = entry_file(d)
        if f.suffix == ".wav":
            d_place.append((f, int(o["offset"]), 10 ** (o.get("gain_db", 0.0) / 20)))
        else:
            music_items.append((f, o))
    d_raw = out_dir / "dialogue.f32"
    mix_to_raw(d_place, total, d_raw)
    d_raw = _cleanup(d_raw, cleanup)
    dialogue = np.memmap(d_raw, dtype="<f4", mode="r").reshape(-1, CHANNELS)
    mono = dialogue.mean(axis=1) if len(dialogue) else np.zeros(0, dtype=np.float32)
    speech = p.get("speech")
    if speech is None and any(o.get("duck_db") for _, o in music_items):
        speech = _speech_from_energy(mono, min(total, len(mono)))
    stems_buf = {"dialogue": None, "music": None, "effects": None}
    m_raw = out_dir / "music.f32"
    e_raw = out_dir / "effects.f32"
    with open(raw, "wb") as fh, open(m_raw, "wb") as fm, open(e_raw, "wb") as fe:
        envs = {}
        for f, o in music_items:
            if o.get("duck_db"):
                envs[o["id"]] = _env_from_ranges(speech or [], total, float(o["duck_db"]))
        maps = [(np.memmap(f, dtype="<i2", mode="r").reshape(-1, CHANNELS), o) for f, o in music_items]
        block = SAMPLE_RATE * 10
        for b0 in range(0, total, block):
            b1 = min(total, b0 + block)
            dbuf = np.zeros((b1 - b0, CHANNELS), dtype=np.float32)
            if b0 < len(dialogue):
                part = np.asarray(dialogue[b0:min(b1, len(dialogue))])
                dbuf[: len(part)] = part
            mbuf = np.zeros_like(dbuf)
            ebuf = np.zeros_like(dbuf)
            for arr, o in maps:
                off = int(o["offset"])
                lo, hi = max(b0, off), min(b1, off + len(arr))
                if hi <= lo:
                    continue
                seg = np.asarray(arr[lo - off: hi - off]).astype(np.float32) / 32768.0
                seg *= 10 ** (float(o.get("gain_db", 0.0)) / 20)
                env = envs.get(o["id"])
                if env is not None:
                    t = np.arange(lo, hi) * 100.0 / SAMPLE_RATE
                    seg *= np.interp(t, np.arange(len(env)), env)[:, None].astype(np.float32)
                target = mbuf if o.get("role") == "music" else (dbuf if o.get("role") == "tone" else ebuf)
                target[lo - b0: hi - b0] += seg
            fh.write((dbuf + mbuf + ebuf).astype("<f4").tobytes())
            fm.write(mbuf.astype("<f4").tobytes())
            fe.write(ebuf.astype("<f4").tobytes())
            if stems:
                stems_buf["dialogue"] = True
        del dialogue
    # dialogue stem includes room tone: rebuild it as total - music - effects at write time
    name = _finish(p, raw, out_dir, total)
    if stems:
        info = json.loads((out_dir / "mix.json").read_text(encoding="utf-8"))
        gain = info.get("gain_db") or 0.0
        vol = [f"volume={gain:.3f}dB"] if gain else []
        d_only = out_dir / "dialogue-stem.f32"
        tot = np.memmap(raw, dtype="<f4", mode="r")
        mm = np.memmap(m_raw, dtype="<f4", mode="r")
        ee = np.memmap(e_raw, dtype="<f4", mode="r")
        with open(d_only, "wb") as fd:
            step = SAMPLE_RATE * CHANNELS * 10
            for i in range(0, len(tot), step):
                fd.write((np.asarray(tot[i:i + step]) - np.asarray(mm[i:i + step]) - np.asarray(ee[i:i + step])).astype("<f4").tobytes())
        del tot, mm, ee
        for stem, src in (("dialogue", d_only), ("music", m_raw), ("effects", e_raw)):
            _write_wav(src, out_dir / f"stem-{stem}.wav", total, vol)
        d_only.unlink(missing_ok=True)
    for f in (raw, m_raw, e_raw, out_dir / "dialogue.f32", out_dir / "dialogue-clean.f32"):
        f.unlink(missing_ok=True)
    return name


def _finish(p: dict, raw: Path, out_dir: Path, total: int) -> str:
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
    gain = None
    if measured:
        try:
            gain = round(float(p["target_lufs"]) - float(measured["input_i"]), 3)
        except ValueError:
            gain = None
    if not (p.get("others") or p.get("cleanup") or p.get("stems")):
        raw.unlink(missing_ok=True)
    (out_dir / "mix.json").write_text(json.dumps({"measured": measured, "samples": total, "gain_db": gain}), encoding="utf-8")
    return out.name
