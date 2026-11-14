"""Phase 3: scenes, frames, sheets, strips and verify."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from conftest import ff, frame_values

# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="session")
def cuts_clip(media_dir) -> Path:
    """Three visually different shots with hard cuts at 2.0 s and 4.0 s."""
    p = media_dir / "hardcuts.mp4"
    if not p.exists():
        ff(
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:d=2",
            "-f", "lavfi", "-i", "color=c=red:s=320x240:r=25:d=2",
            "-f", "lavfi", "-i", "mandelbrot=size=320x240:rate=25",
            "-filter_complex", "[2]trim=duration=2,setpts=PTS-STARTPTS[m];[0][1][m]concat=n=3:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p),
        )
    return p


@pytest.fixture(scope="session")
def talk_clip(media_dir) -> Path:
    """8 s talking-head stand-in: moving picture, tone with gaps 2-3 and 5-6, black picture 6.5-7.5 in the source."""
    p = media_dir / "talk.mp4"
    if not p.exists():
        ff(
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:d=8",
            "-f", "lavfi", "-i", "sine=f=250:sample_rate=48000:d=8",
            "-filter_complex",
            "[0]drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,6.5,7.5)'[v];"
            "[1]volume='if(between(t,2,3)+between(t,5,6),0,1)':eval=frame[a]",
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p),
        )
    return p


def _plan_and_render(cli, clip: Path, tmp: Path, name: str = "v1") -> Path:
    cli.ok("analyze", str(clip), "--skip", "transcript,scenes")
    cli.ok("plan", str(clip), "--cut-silence", "min=0.5,pad=0.1", "-o", name)
    out = tmp / f"{name}.mp4"
    cli.ok("render", name, "-o", str(out))
    return out


# --------------------------------------------------------------------------- scenes & frames


def test_scene_detection_hard_cuts(cli, cuts_clip):
    d = cli.ok("scenes", str(cuts_clip), "--detector", "content")
    starts = [s["start"] for s in d["scenes"]]
    assert len(starts) == 3
    assert starts[1] == pytest.approx(2.0, abs=0.08) and starts[2] == pytest.approx(4.0, abs=0.08)
    again = cli.ok("scenes", str(cuts_clip), "--detector", "content")
    assert again["cached"] is True
    idx = cli.ok("index", str(cuts_clip))
    assert idx["summary"]["scene_count"] == 3


def test_frame_extraction_exact_frame(cli, counter_clip, tmp_path):
    d = cli.ok("frames", str(counter_clip), "--at", "1.0,2.48,0:03.2", "--width", "64", "--format", "png")
    vals = [frame_values(Path(f["path"]))[0] for f in d["frames"]]
    assert vals == [25, 62, 80]
    ev = cli.ok("frames", str(counter_clip), "--every", "0.4", "--from", "1", "--to", "3", "--width", "64", "--format", "png")
    assert [frame_values(Path(f["path"]))[0] for f in ev["frames"]] == [25, 35, 45, 55, 65]


def test_sheet_tile_map_and_pagination(cli, av_clip):
    d = cli.ok("sheet", str(av_clip), "--every", "0.25", "--cols", "4", "--max-tiles", "6", "--width", "800")
    assert d["tiles"] == 16
    assert [len(s["tiles"]) for s in d["sheets"]] == [6, 6, 4]
    sh = d["sheets"][0]
    img = cv2.imread(sh["path"])
    assert img.shape[1] == 800 and img.shape[0] == sh["height"]
    t = sh["tiles"]
    assert (t[0]["row"], t[0]["col"]) == (0, 0) and (t[4]["row"], t[4]["col"]) == (1, 0)
    assert t[1]["x"] > t[0]["x"] and t[4]["y"] > t[0]["y"]
    assert [x["time"] for x in d["sheets"][1]["tiles"]][0] == pytest.approx(1.5)
    # tiles really sit where the map says: the margin between tiles is background
    x_gap = t[0]["x"] + t[0]["w"] + 2
    assert all(abs(v - 24) <= 16 for v in img[t[0]["y"] + 5, x_gap].tolist())  # JPEG tolerance


def test_sheet_scenes_and_cuts(cli, cuts_clip, counter_clip, tmp_path):
    d = cli.ok("sheet", str(cuts_clip), "--scenes")
    assert d["tiles"] == 3 and d["sheets"][0]["tiles"][1]["shot"] == "s0002"
    cli.ok("probe", str(counter_clip))
    cli.ok("plan", str(counter_clip), "-o", "cc", "--keep-only", "0.4-1.0", "--keep-only", "2.0-2.6", "--min-clip", "0", "--no-snap")
    cuts = cli.ok("sheet", "--cuts", "cc")
    tiles = cuts["sheets"][0]["tiles"]
    assert [(t["clip"], t["side"]) for t in tiles] == [("c0001", "before"), ("c0002", "after")]
    assert tiles[0]["source_time"] == pytest.approx(0.96) and tiles[1]["source_time"] == pytest.approx(2.0)


def test_strip_time_axis_places_click(cli, media_dir):
    p = media_dir / "click.mkv"
    if not p.exists():
        ff("-f", "lavfi", "-i", "color=c=gray:s=160x120:r=25:d=4",
           "-f", "lavfi", "-i", "aevalsrc='if(between(t,2.5,2.52),0.95,0)':s=48000:d=4",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(p))
    d = cli.ok("strip", str(p), "--from", "2", "--to", "3", "--width", "1000")
    img = cv2.imread(d["path"])
    ax = d["axis"]
    expected_x = ax["x0"] + (2.5 - 2.0) / ax["seconds_per_pixel"]
    # find the waveform spike: the column with the most bright-green pixels
    green = (img[:, :, 1] > 150) & (img[:, :, 0] < 120) & (img[:, :, 2] < 160)
    col = int(np.argmax(green.sum(axis=0)))
    assert abs(col - expected_x) <= 6


# --------------------------------------------------------------------------- verify


def _checks(env: dict) -> dict[str, dict]:
    checks = env["data"]["checks"] if env["ok"] else env["error"]["details"]["checks"]
    return {c["name"]: c for c in checks}


def test_verify_passes_on_good_render(cli, talk_clip, tmp_path):
    out = _plan_and_render(cli, talk_clip, tmp_path)
    code, env = cli("verify", str(out), "--edl", "v1")
    assert code == 0, env
    c = _checks(env)
    assert c["duration"]["status"] == "pass" and c["av_sync"]["status"] == "pass"
    # black 6.5-7.5 is in the source: found but not reported
    assert c["black_frames"]["status"] == "pass" and c["black_frames"]["measured"]["ignored_as_in_source"] == 1
    assert c["loudness"]["status"] == "pass"
    assert Path(env["artifacts"][0]["path"]).is_file()


def test_verify_inserted_black_segment(cli, talk_clip, tmp_path):
    out = _plan_and_render(cli, talk_clip, tmp_path)
    bad = tmp_path / "black.mp4"
    ff("-i", str(out), "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,0.5,1.5)'", "-c:a", "copy", str(bad))
    code, env = cli("verify", str(bad), "--edl", "v1")
    assert code == 5 and env["error"]["code"] == "VERIFY_FAILED"
    item = _checks(env)["black_frames"]["items"][0]
    assert item["from"] == pytest.approx(0.5, abs=0.1) and item["clip"] == "c0001"
    assert item["fix"].startswith("videocli edl cut v1")


def test_verify_duration_mismatch_when_clip_missing(cli, talk_clip, tmp_path):
    out = _plan_and_render(cli, talk_clip, tmp_path)
    cli.ok("edl", "restore", "v1", "--from", "2", "--to", "3")
    code, env = cli("verify", str(out), "--edl", "v1", "--checks", "duration")
    assert code == 5 and _checks(env)["duration"]["status"] == "fail"


def test_verify_audio_shorter_than_video(cli, talk_clip, tmp_path):
    out = _plan_and_render(cli, talk_clip, tmp_path)
    bad = tmp_path / "short.mp4"
    ff("-i", str(out), "-c:v", "copy", "-af", "atrim=end=4.0", "-c:a", "aac", str(bad))
    code, env = cli("verify", str(bad), "--edl", "v1", "--checks", "av_sync,streams")
    assert code == 5 and _checks(env)["av_sync"]["status"] == "fail"


def test_verify_click_at_join(cli, talk_clip, tmp_path):
    out = _plan_and_render(cli, talk_clip, tmp_path)
    edl = json.loads(Path(".videocli/edits/v1.edl.json").read_text())
    rate = 25
    first = edl["tracks"]["main"][0]
    join = round((first["end"] - first["start"]) * rate) / rate
    bad = tmp_path / "click.mp4"
    ff("-i", str(out), "-f", "lavfi", "-i", f"aevalsrc='0.3*sin(2*PI*220*t)+if(gte(t,{join}),0.6,0)':s=48000:d=8",
       "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "pcm_s16le", "-shortest", str(bad.with_suffix(".mkv")))
    code, env = cli("verify", str(bad.with_suffix(".mkv")), "--edl", "v1", "--checks", "audio_clicks", "--sheet")
    assert code == 0  # clicks are warnings until calibrated on real renders
    c = _checks(env)["audio_clicks"]
    assert c["status"] == "warn"
    assert c["items"][0]["clips"] == ["c0001", "c0002"] and c["items"][0]["at"] == pytest.approx(join, abs=0.01)
    assert env["data"]["sheets"] and Path(env["data"]["sheets"][0]["path"]).is_file()


def test_verify_unknown_check(cli, talk_clip, tmp_path):
    out = _plan_and_render(cli, talk_clip, tmp_path)
    code, env = cli("verify", str(out), "--edl", "v1", "--checks", "nope")
    assert code == 2


def test_frozen_in_source_not_reported(cli, media_dir, tmp_path):
    p = media_dir / "slides.mp4"
    if not p.exists():
        ff("-f", "lavfi", "-i", "color=c=navy:s=160x120:r=25:d=6", "-f", "lavfi", "-i", "sine=f=300:sample_rate=48000:d=6",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p))
    cli.ok("probe", str(p))
    cli.ok("plan", str(p), "-o", "sl", "--keep-only", "0-5.5")
    out = tmp_path / "sl.mp4"
    cli.ok("render", "sl", "-o", str(out))
    code, env = cli("verify", str(out), "--edl", "sl", "--checks", "frozen_frames")
    assert code == 0
    c = _checks(env)["frozen_frames"]
    assert c["status"] == "pass" and c["measured"]["ignored_as_in_source"] >= 1


def test_click_score_unit():
    from videocli.judge.checks import click_score

    t = np.arange(4800) / 48000
    smooth = 0.3 * np.sin(2 * np.pi * 220 * t)
    r, step = click_score(smooth, 2400)
    assert step < 0.05
    jump = smooth.copy()
    jump[2400:] += 0.6
    r2, step2 = click_score(jump, 2400)
    assert step2 > 0.5 and r2 > 8
    _ = subprocess  # keep import for fixture helpers
