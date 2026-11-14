"""Phase 6: chunked render graph, overlays, transitions, split edits, sound, reframe, bakes, export."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from conftest import ff, frame_values, stream_durations


@pytest.fixture(scope="session")
def tones(media_dir) -> tuple[Path, Path]:
    """Two 4 s sources: A = gray picture + 440 Hz tone; B = white picture + silence."""
    a, b = media_dir / "toneA.mkv", media_dir / "silentB.mkv"
    if not a.exists():
        ff("-f", "lavfi", "-i", "color=c=gray:s=160x120:r=25:d=4", "-f", "lavfi", "-i", "sine=f=440:sample_rate=48000:d=4",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(a))
    if not b.exists():
        ff("-f", "lavfi", "-i", "color=c=white:s=160x120:r=25:d=4", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=4",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-t", "4", str(b))
    return a, b


def _two_clip_edl(cli, a: Path, b: Path, name: str) -> None:
    cli.ok("probe", str(a))
    cli.ok("probe", str(b))
    cli.ok("plan", str(a), "-o", name, "--keep-only", "0-2", "--no-snap")
    p = Path(f".videocli/edits/{name}.edl.json")
    d = json.loads(p.read_text())
    from videocli.core.source import compute_source_id

    sid_b = compute_source_id(b)
    d["sources"][sid_b] = {"path": str(b.resolve()), "duration": 4.0, "fps": "25/1", "has_video": True, "has_audio": True}
    d["tracks"]["main"].append({"id": "c0002", "source": sid_b, "start": 0.0, "end": 2.0})
    d["mix"]["normalize"] = False
    d["mix"]["fade_ms"] = 0
    p.write_text(json.dumps(d))


def pcm(path: Path) -> np.ndarray:
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype="<f4")


def rms_db(x: np.ndarray) -> float:
    return 20 * np.log10(float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) + 1e-12)


# --------------------------------------------------------------------------- compiler


def test_chunking_two_overlays_and_a_transition(cli, counter_clip, tones):
    from videocli.edl import io
    from videocli.render import presets
    from videocli.render.timeline import compile_timeline

    a, b = tones
    _two_clip_edl(cli, a, b, "ch")
    cli.ok("edl", "overlay", "ch", "--source", str(counter_clip), "--from", "0", "--to", "0.4", "--on", "c0001", "--offset", "0.2")
    cli.ok("edl", "overlay", "ch", "--source", str(counter_clip), "--from", "1", "--to", "1.4", "--on", "c0002", "--offset", "1.0",
           "--mode", "pip")
    cli.ok("edl", "transition", "ch", "c0001", "--duration", "0.4")
    edl = io.load(cli_ws(), "ch")
    comp = compile_timeline(edl, presets.get("source"), crf=18, x264_preset="veryfast")
    spans = [(n.kind, int(n.label.split("@")[1]), n.params["frames"]) for n in comp.chunks]
    # frames: c0001 0-50, transition 40-50, c0002 40-90; overlays 5-15 and 65-75
    assert spans == [("segment", 0, 5), ("composite", 5, 10), ("segment", 15, 25), ("composite", 40, 10),
                     ("segment", 50, 15), ("composite", 65, 10), ("segment", 75, 15)]
    assert comp.frames == 90


def cli_ws():
    from videocli.core.workspace import Workspace, resolve_root

    return Workspace.open(resolve_root())


def test_spine_only_segments_keep_phase2_parameters(cli, counter_clip):
    from videocli.edl import io
    from videocli.render import presets
    from videocli.render.timeline import compile_timeline

    cli.ok("probe", str(counter_clip))
    cli.ok("plan", str(counter_clip), "-o", "sp", "--keep-only", "0-1", "--keep-only", "2-3", "--no-snap")
    comp = compile_timeline(io.load(cli_ws(), "sp"), presets.get("source"), crf=18, x264_preset="veryfast")
    assert [n.kind for n in comp.chunks] == ["segment", "segment"]
    assert set(comp.chunks[0].params) == {"source", "start", "speed", "rate", "size", "frames", "pix_fmt", "crf", "x264_preset", "has_video"}
    assert set(comp.mix.params) == {"samples", "placements", "normalize", "target_lufs", "true_peak_db"}


# --------------------------------------------------------------------------- picture


def test_overlay_frame_at_window_start_and_rerender_scope(cli, counter_clip, tones, tmp_path):
    a, b = tones
    _two_clip_edl(cli, a, b, "ov")
    cli.ok("edl", "overlay", "ov", "--source", str(counter_clip), "--from", "1.0", "--to", "1.4", "--on", "c0001", "--offset", "0.4")
    out = tmp_path / "ov.mp4"
    d = cli.ok("render", "ov", "-o", str(out))
    vals = frame_values(out)
    assert vals[10:20] == list(range(25, 35))  # window 0.4 s = frame 10 shows counter frame 25 (1.0 s)
    cli.ok("edl", "overlay", "ov", "--source", str(counter_clip), "--from", "2.0", "--to", "2.2", "--on", "c0002", "--offset", "0.2")
    d2 = cli.ok("render", "ov", "-o", str(out))
    # only the c0002 region is re-chunked: two new chunks (composite + following segment) plus a changed preceding
    # segment, picture and mix; c0001's chunks and the lanes are reused
    assert d2["nodes"]["ran"] <= 5 and d2["nodes"]["cached"] >= d["nodes"]["total"] - 3


def test_pip_opacity_blends(cli, tones, tmp_path):
    a, b = tones
    _two_clip_edl(cli, a, b, "op")
    cli.ok("edl", "overlay", "op", "--source", str(b), "--from", "0", "--to", "1", "--on", "c0001", "--mode", "cover", "--opacity", "0.5")
    out = tmp_path / "op.mp4"
    cli.ok("render", "op", "-o", str(out))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(out), "-frames:v", "1", "-vf", "format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    y = np.frombuffer(raw, dtype=np.uint8).mean()
    # gray (≈128) under white (≈235) at half opacity ≈ 181
    assert 165 < y < 195


def test_dissolve_shortens_by_overlap_and_blends(cli, tones, tmp_path):
    a, b = tones
    _two_clip_edl(cli, a, b, "dz")
    d0 = cli.ok("edl", "show", "dz")["output_duration"]
    cli.ok("edl", "transition", "dz", "c0001", "--type", "dissolve", "--duration", "0.4")
    out = tmp_path / "dz.mp4"
    r = cli.ok("render", "dz", "-o", str(out))
    assert r["output_duration"] == pytest.approx(d0 - 0.4)
    assert stream_durations(out)["video"] == pytest.approx(d0 - 0.4, abs=0.041)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-vf", "format=gray", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    lum = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 120 * 160).mean(axis=1)
    mid = lum[40:50]
    assert lum[30] < mid.mean() < lum[60] and np.all(np.diff(mid) > 0)  # monotonic ramp gray → white


def test_transition_without_handles_is_linted(cli, tones):
    a, b = tones
    _two_clip_edl(cli, a, b, "th")
    cli.ok("edl", "transition", "th", "c0001", "--duration", "1.5")
    code, env = cli("edl", "lint", "th")
    assert code == 5 and any(i["rule"] == "transition_handles" for i in env["error"]["details"]["issues"])


def test_reframe_vertical_dimensions(cli, av_clip, tmp_path):
    cli.ok("probe", str(av_clip))
    cli.ok("plan", str(av_clip), "-o", "vert", "--keep-only", "0-2")
    d = cli.ok("edl", "reframe", "vert", "--aspect", "9:16")
    assert d["resolution"] == [240, 426]
    out = tmp_path / "v.mp4"
    cli.ok("render", "vert", "-o", str(out))
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)], capture_output=True, text=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (240, 426)


def test_speed_freeze_and_ramp_durations(cli, counter_clip, tmp_path):
    cli.ok("probe", str(counter_clip))
    cli.ok("plan", str(counter_clip), "-o", "spd", "--keep-only", "0-1", "--keep-only", "2-3", "--no-snap")
    cli.ok("edl", "speed", "spd", "c0001", "--rate", "2")
    cli.ok("edl", "speed", "spd", "c0002", "--freeze", "0.4")
    out = tmp_path / "spd.mp4"
    r = cli.ok("render", "spd", "-o", str(out))
    assert r["frames"] == 12 + 25 + 10  # 1 s at 2x snaps to 12 frames
    vals = frame_values(out)
    assert vals[0] == 0 and vals[5] == 10 and vals[-1] == vals[-10] == 74  # frozen last frame
    cli.ok("edl", "speed", "spd", "c0001", "--ramp", "0:1,1:3")
    show = cli.ok("edl", "show", "spd")
    assert 0.4 < show["output_duration"] - 1.4 < 0.8


# --------------------------------------------------------------------------- sound


def test_split_edit_l_cut_audible_after_picture_cut(cli, tones, tmp_path):
    a, b = tones
    _two_clip_edl(cli, a, b, "lc")
    cli.ok("edl", "split-edit", "lc", "c0001", "--lag", "0.6")
    out = tmp_path / "lc.mp4"
    cli.ok("render", "lc", "-o", str(out))
    x = pcm(out)
    sr = 48000
    assert rms_db(x[int(2.1 * sr):int(2.3 * sr)]) > -40  # A's tone continues over B's picture
    assert rms_db(x[int(2.9 * sr):int(3.5 * sr)]) < -60  # and is gone after the lag


def test_music_ducks_under_speech(cli, tones, media_dir, tmp_path):
    a, b = tones
    music = media_dir / "music.wav"
    if not music.exists():
        ff("-f", "lavfi", "-i", "sine=f=1200:sample_rate=48000:d=10", str(music))
    _two_clip_edl(cli, a, b, "dk")
    cli.ok("analyze", str(a), "--skip", "transcript,scenes")
    cli.ok("analyze", str(b), "--skip", "transcript,scenes")
    cli.ok("edl", "audio", "dk", "--source", str(music), "--gain", "-6", "--duck", "-12", "--fit")
    out = tmp_path / "dk.mp4"
    cli.ok("render", "dk", "-o", str(out), "--stems")
    m = pcm(out.with_name("dk.music.wav"))
    sr = 48000
    under = rms_db(m[int(0.8 * sr):int(1.6 * sr)])  # speech (A's tone)
    clear = rms_db(m[int(3.0 * sr):int(3.8 * sr)])  # B is silent
    assert clear - under == pytest.approx(12, abs=1.5)


def test_room_tone_fills_cuts(cli, media_dir, tmp_path):
    p = media_dir / "roomy.wav"
    if not p.exists():
        ff("-f", "lavfi", "-i", "sine=f=300:sample_rate=48000:d=6", "-f", "lavfi", "-i", "anoisesrc=a=0.003:r=48000:d=6",
           "-filter_complex", "[0]volume='if(between(t,2,4),0,1)':eval=frame[s];[s][1]amix=inputs=2:normalize=0", str(p))
    cli.ok("analyze", str(p), "--skip", "transcript")
    cli.ok("plan", str(p), "-o", "rt", "--cut-silence", "min=1,pad=0.3")
    cli.ok("edl", "sound", "rt", "--room-tone", "auto")
    out = tmp_path / "rt.mkv"
    cli.ok("render", "rt", "-o", str(out), "--no-normalize")
    x = pcm(out)
    win = 480
    frames = np.abs(x[: len(x) // win * win]).reshape(-1, win).max(axis=1)
    assert frames.min() > 0  # never digital silence, including across the cut


def test_everything_combined_masters_equal_and_verify(cli, tones, counter_clip, media_dir, tmp_path):
    a, b = tones
    music = media_dir / "music.wav"
    if not music.exists():
        ff("-f", "lavfi", "-i", "sine=f=1200:sample_rate=48000:d=10", str(music))
    _two_clip_edl(cli, a, b, "all")
    p = Path(".videocli/edits/all.edl.json")
    d = json.loads(p.read_text())
    d["mix"]["normalize"] = True
    p.write_text(json.dumps(d))
    cli.ok("edl", "overlay", "all", "--source", str(counter_clip), "--from", "0", "--to", "0.8", "--on", "c0002", "--mode", "pip")
    cli.ok("edl", "transition", "all", "c0001", "--type", "fade", "--duration", "0.4")
    cli.ok("edl", "split-edit", "all", "c0002", "--lead", "0.2")
    cli.ok("edl", "audio", "all", "--source", str(music), "--gain", "-12", "--fade", "0.5", "--fit")
    out = tmp_path / "all.mp4"
    cli.ok("render", "all", "-o", str(out))
    durs = stream_durations(out)
    assert abs(durs["video"] - durs["audio"]) < 1 / 25
    code, env = cli("verify", str(out), "--edl", "all", "--skip", "frozen_frames,black_frames")
    assert code == 0, env


# --------------------------------------------------------------------------- bakes and export


def test_bake_contract_rejects_wrong_duration_and_missing_alpha(tmp_path):
    from videocli.core.errors import RuntimeFailure
    from videocli.render.bakers import check_contract

    clip = tmp_path / "b.mov"
    ff("-f", "lavfi", "-i", "color=c=red@0.5:s=64x64:r=25:d=1,format=argb", "-c:v", "qtrle", str(clip))
    assert check_contract(clip, duration=1.0, size=(64, 64), fps="25/1", alpha=True).alpha
    with pytest.raises(RuntimeFailure) as ei:
        check_contract(clip, duration=2.0, size=(64, 64), fps="25/1", alpha=True)
    assert ei.value.code == "BAKE_FAILED" and "duration" in ei.value.message
    opaque = tmp_path / "o.mp4"
    ff("-f", "lavfi", "-i", "color=c=red:s=64x64:r=25:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(opaque))
    with pytest.raises(RuntimeFailure) as ei:
        check_contract(opaque, duration=1.0, size=(64, 64), fps="25/1", alpha=True)
    assert "transparency" in ei.value.message


def test_stabilize_bake(cli, media_dir, tmp_path):
    from videocli.media import ffmpeg as f

    if "vidstabdetect" not in f.capabilities().filters:
        pytest.skip("ffmpeg lacks vid.stab")
    shaky = media_dir / "shaky.mp4"
    if not shaky.exists():
        ff("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:d=2", "-vf", "crop=280:200:20+12*sin(t*20):20+10*cos(t*17),scale=320:240",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(shaky))
    cli.ok("probe", str(shaky))
    cli.ok("plan", str(shaky), "-o", "stab", "--keep-only", "0-2")
    cli.ok("edl", "stabilize", "stab", "c0001", "--strength", "0.6")
    out = tmp_path / "stab.mp4"
    d = cli.ok("render", "stab", "-o", str(out))
    assert d["frames"] == 50 and stream_durations(out)["video"] == pytest.approx(2.0, abs=0.05)


@pytest.mark.parametrize("fmt", ["otio", "fcpxml", "edl"])
def test_export_round_check(cli, tones, counter_clip, tmp_path, fmt):
    if fmt == "otio":
        pytest.importorskip("opentimelineio")
    a, b = tones
    _two_clip_edl(cli, a, b, "ex")
    cli.ok("edl", "overlay", "ex", "--source", str(counter_clip), "--from", "0", "--to", "0.5", "--on", "c0002")
    cli.ok("edl", "transition", "ex", "c0001", "--duration", "0.4")
    out = tmp_path / f"ex.{fmt}"
    code, env = cli("export", "ex", "--to", fmt, "-o", str(out), "--relative-to", str(tmp_path))
    assert code == 0, env
    d = env["data"]
    assert d["round_check"] == "pass" and d["clips"] == 2 and d["duration"] == pytest.approx(3.6, abs=0.05)
    if fmt == "otio":
        import opentimelineio as otio

        tl = otio.adapters.read_from_file(str(out))
        assert len(tl.tracks) == 2 and tl.tracks[0].duration().to_seconds() == pytest.approx(3.6, abs=0.05)
    else:
        assert any(w["code"] == "EXPORT_PARTIAL" for w in env["warnings"])


@pytest.fixture(scope="session")
def bubble_clip(media_dir):
    """Blue 'screen' with a red 'webcam bubble' in the lower-right corner (x .78-.98, y .58-.96)."""
    p = media_dir / "bubble.mp4"
    if not p.exists():
        ff("-f", "lavfi", "-i", "color=c=blue:s=640x360:r=25:d=3", "-f", "lavfi", "-i", "sine=f=300:sample_rate=48000:d=3",
           "-vf", "drawbox=x=499:y=209:w=128:h=137:color=red:t=fill",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p))
    return p


def _mid_frame(path):
    import numpy as np

    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)], capture_output=True, text=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(v["height"], v["width"], 3).astype(int), (v["width"], v["height"])


def test_reframe_stack_screen_over_face(cli, bubble_clip, tmp_path):
    cli.ok("probe", str(bubble_clip))
    cli.ok("plan", str(bubble_clip), "-o", "stk", "--keep-only", "0-2", "--no-snap")
    code, env = cli("edl", "reframe", "stk", "--aspect", "9:16", "--mode", "stack")
    assert code == 2 and "--face" in env["error"]["message"]
    d = cli.ok("edl", "reframe", "stk", "--aspect", "9:16", "--mode", "stack", "--face", "0.78,0.58,0.2,0.38", "--split", "0.6")
    assert d["mode"] == "stack" and d["resolution"] == [360, 640] and d["split"] == 0.6
    cli.ok("edl", "reframe", "stk", "--aspect", "9:16", "--clip", "c0001", "--x", "0.3")  # screen crop moves left
    out = tmp_path / "stk.mp4"
    cli.ok("render", "stk", "-o", str(out))
    fr, size = _mid_frame(out)
    assert size == (360, 640)
    top, bot = fr[: int(640 * 0.6) - 4], fr[int(640 * 0.6) + 4:]
    is_red = lambda a: ((a[..., 0] > 150) & (a[..., 2] < 100)).mean()  # noqa: E731
    is_blue = lambda a: ((a[..., 2] > 150) & (a[..., 0] < 100)).mean()  # noqa: E731
    assert is_blue(top) > 0.95  # x=0.3 crop keeps the bubble out of the screen panel
    assert is_red(bot) > 0.95  # the face panel is filled by the bubble

    # composite path (a graphic on top) renders the same layout
    cli.ok("gfx", "add", "stk", "-t", "lower-third", "--param", "title=Hi", "--on", "c0001", "--duration", "1.5")
    out2 = tmp_path / "stk2.mp4"
    cli.ok("render", "stk", "-o", str(out2))
    fr2, size2 = _mid_frame(out2)
    assert size2 == (360, 640) and is_red(fr2[int(640 * 0.6) + 4:]) > 0.5


def test_reframe_stack_needs_tall_aspect(cli, bubble_clip):
    cli.ok("probe", str(bubble_clip))
    cli.ok("plan", str(bubble_clip), "-o", "stw", "--keep-only", "0-2")
    code, env = cli("edl", "reframe", "stw", "--aspect", "1:1", "--mode", "stack", "--face", "0.78,0.58,0.2,0.38")
    assert code == 2 and "tall" in env["error"]["message"]
