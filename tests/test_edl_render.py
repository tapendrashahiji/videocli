"""Phase 2: planners, EDL schema and ops, lint, render graph, captions."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from conftest import ff
from videocli.core.timeutil import TimeMap
from videocli.edl import io, ops
from videocli.edl import lint as lintmod
from videocli.edl.model import EDL, Clip, Overlay, SourceRef
from videocli.edl.planners import Cut, PlanOptions, merge_cuts, plan, silence, smooth, snap

# --------------------------------------------------------------------------- planners


def test_silence_pad_larger_than_half_leaves_nothing():
    assert silence.plan([(1.0, 1.4)], min_len=0.3, pad=0.25) == []
    cuts = silence.plan([(1.0, 3.0), (5.0, 5.2)], min_len=0.5, pad=0.15)
    assert [(c.start, c.end) for c in cuts] == [(1.15, 2.85)]


def test_merge_keeps_highest_priority_reason():
    m = merge_cuts([Cut(0, 2, "silence"), Cut(1.5, 3, "filler"), Cut(5, 6, "silence")])
    assert [(c.start, c.end, c.reason) for c in m] == [(0, 3, "filler"), (5, 6, "silence")]


def test_smooth_merges_tiny_cuts_and_drops_tiny_clips():
    cuts, info = smooth.apply([Cut(1.0, 1.1, "silence"), Cut(2.0, 4.0, "silence"), Cut(4.2, 6.0, "silence")], 10.0, min_cut=0.2, min_clip=0.3)
    # the 0.1 s cut disappears; the 0.2 s clip between 4.0 and 4.2 becomes a cut
    assert [(c.start, c.end) for c in cuts] == [(2.0, 6.0)]
    assert info == {"merged_cuts": 1, "dropped_clips": 1}


WORDS = [{"start": 0.5, "end": 1.0, "word": "So"}, {"start": 1.1, "end": 1.4, "word": "um,"},
         {"start": 1.5, "end": 2.2, "word": "binary"}, {"start": 2.25, "end": 2.9, "word": "search"}]


def test_fillers_cut_with_pad_and_neighbours_untouched():
    res = plan(3.0, [], WORDS, PlanOptions(cut_fillers=[], filler_pad=0.04, min_cut=0.0, min_clip=0.0))
    assert [(c.start, c.end, c.reason) for c in res.cuts] == [(1.06, 1.44, "filler")]
    assert res.steps["fillers"]["words"] == ["um"]


def test_fillers_user_override():
    res = plan(3.0, [], WORDS, PlanOptions(cut_fillers=["binary"], min_cut=0.0, min_clip=0.0))
    assert res.cuts[0].start == pytest.approx(1.46) and res.cuts[0].end == pytest.approx(2.24)


def test_snap_moves_boundary_out_of_word():
    cuts, moved = snap.apply([Cut(1.8, 2.5, "manual")], WORDS, 3.0)
    assert moved == 1
    assert (cuts[0].start, cuts[0].end) == (1.5, 2.25) or (cuts[0].start, cuts[0].end) == (2.2, 2.9)


def test_snap_never_moves_into_locked_range():
    cuts, _ = snap.apply([Cut(1.8, 2.0, "manual")], WORDS, 3.0, locked=[(1.4, 1.7)])
    assert not (1.4 < cuts[0].start < 1.7)


def test_keep_only_and_cut_range():
    res = plan(10.0, [], [], PlanOptions(keep_only=[(2, 5), (7, 8)], cut_ranges=[(3, 4)], min_cut=0, min_clip=0))
    assert [(a, b) for a, b, _ in res.keep] == [(2, 3), (4, 5), (7, 8)]


# --------------------------------------------------------------------------- schema


SPEC_EDL = {
    "schema": "videocli.edl/v1",
    "name": "doc-rough-03",
    "rate": "25/1",
    "resolution": [1920, 1080],
    "color": {"working": "rec709", "output": "rec709"},
    "sources": {
        "a1b2c3d4e5f6": {"path": "interview.mov", "duration": 3612.48, "fps": "25/1", "input_transform": "hlg_to_rec709"},
        "0f9e8d7c6b5a": {"path": "broll_workshop.mp4", "duration": 240.0, "fps": "25/1"},
        "77aa88bb99cc": {"path": "score.wav", "duration": 190.0},
    },
    "tracks": {
        "main": [{"id": "c0001", "source": "a1b2c3d4e5f6", "start": 512.20, "end": 531.04, "speed": 1.0,
                  "audio": {"gain_db": 0.0, "lead": 0.0, "lag": 0.6}, "grade": "g_interview", "reason": "beat:hook", "locked": False}],
        "overlay": [{"id": "o0001", "source": "0f9e8d7c6b5a", "start": 40.0, "end": 46.5, "anchor": "c0001", "offset": 6.0,
                     "mode": "cover", "mute": True, "grade": "g_broll"}],
        "graphics": [{"id": "x0001", "baker": "titles", "template": "lower-third", "params": {"title": "Maya Shrestha", "subtitle": "Potter"},
                      "anchor": "c0001", "offset": 1.0, "duration": 4.0}],
        "audio": [{"id": "m0001", "source": "77aa88bb99cc", "role": "music", "start": 0.0, "at": 0.0, "gain_db": -18.0, "duck_db": -10.0, "fade": 1.5}],
    },
    "transitions": [{"after": "c0001", "type": "dissolve", "duration": 0.4}],
    "grades": {
        "look": {"ops": [{"op": "cdl", "slope": [1.04, 1.0, 0.96], "offset": [0.0, 0.0, 0.01], "power": [1.0, 1.0, 1.0], "sat": 0.92}]},
        "g_interview": {"ops": [{"op": "exposure", "stops": 0.3}, {"op": "temperature", "kelvin_shift": -250}]},
        "g_broll": {"ops": [{"op": "match", "to": "g_interview", "strength": 0.8}]},
    },
    "captions": {"mode": "none", "style": "default"},
    "mix": {"target_lufs": -14.0, "true_peak_db": -1.0, "fade_ms": 15},
    "markers": [{"at": 12.4, "kind": "note", "text": "Hold longer on the hands."}],
    "output": {"preset": "1080p", "container": "mp4", "vcodec": "h264", "crf": 18, "acodec": "aac", "audio_bitrate": "192k"},
    "history": [{"op": "assemble", "at": "2026-11-14T10:15:00Z"}],
}


def test_spec_edl_validates_and_round_trips():
    edl = io.validate(json.loads(json.dumps(SPEC_EDL)))
    first = io.dumps(io.snap(edl))
    again = io.dumps(io.snap(io.parse(first, "x")))
    assert first == again
    assert io.parse(first, "x").tracks.graphics[0].params["title"] == "Maya Shrestha"


@pytest.mark.parametrize(
    "mutate,needle",
    [
        (lambda d: d["tracks"]["main"][0].update(end=500.0), "end"),
        (lambda d: d["tracks"]["main"][0].update(source="nope"), "unknown source"),
        (lambda d: d["tracks"]["overlay"][0].update(id="c0001"), "duplicate"),
        (lambda d: d["tracks"]["main"][0].update(end=9999.0), "past the source duration"),
        (lambda d: d.update(resolution=[1921, 1080]), "even"),
    ],
)
def test_edl_validation_errors(mutate, needle):
    from videocli.core.errors import InputError

    d = json.loads(json.dumps(SPEC_EDL))
    mutate(d)
    with pytest.raises(InputError) as ei:
        io.validate(d)
    assert ei.value.code == "EDL_INVALID"
    assert needle in ei.value.message


# --------------------------------------------------------------------------- ops


def _edl(*ranges, duration=100.0) -> EDL:
    e = EDL(name="t", rate="25/1", resolution=(320, 240), sources={"aaaaaaaaaaaa": SourceRef(path="x.mp4", duration=duration)})
    e.tracks.main = [Clip(id=f"c{i + 1:04d}", source="aaaaaaaaaaaa", start=a, end=b) for i, (a, b) in enumerate(ranges)]
    return e


def spans(e: EDL):
    return [(c.id, c.start, c.end) for c in e.tracks.main]


def test_cut_splits_clip_and_records_reason():
    e = _edl((0, 10), (20, 30))
    s = ops.cut(e, 4, 6, reason="tangent")
    assert spans(e) == [("c0001", 0, 4), ("c0003", 6, 10), ("c0002", 20, 30)]
    assert s["added"] == ["c0003"] and s["after"]["output_duration"] == 18.0
    shown = ops.show(e)
    assert shown["by_reason"]["tangent"] == 2.0


def test_cut_at_clip_edges_and_whole_clip():
    e = _edl((0, 10), (20, 30))
    ops.cut(e, 0, 2)
    ops.cut(e, 18, 31)
    assert spans(e) == [("c0001", 2, 10)]


def test_keep_and_restore_merge_back():
    e = _edl((0, 4), (6, 10))
    s = ops.restore(e, 4, 6)
    assert spans(e) == [("c0001", 0, 10)]
    assert s["merged"]
    e = _edl((0, 4))
    ops.keep(e, 8, 9, reason="callback")
    assert spans(e) == [("c0001", 0, 4), ("c0002", 8, 9)]


def test_trim_split_move_lock():
    e = _edl((0, 10), (20, 30))
    ops.trim(e, "c0001", start=1, end=9)
    assert spans(e)[0] == ("c0001", 1, 9)
    ops.split(e, "c0002", 25)
    assert spans(e) == [("c0001", 1, 9), ("c0002", 20, 25), ("c0003", 25, 30)]
    ops.move(e, "c0003", before="c0001")
    assert [c.id for c in e.tracks.main] == ["c0003", "c0001", "c0002"]
    ops.lock(e, "c0001")
    assert e.clip("c0001").locked
    from videocli.core.errors import UsageError

    with pytest.raises(UsageError):
        ops.split(e, "c0002", 20)  # at an edge


def test_anchor_follows_clip_through_split_trim_move_and_orphans_on_removal():
    e = _edl((0, 10), (20, 30))
    e.sources["bbbbbbbbbbbb"] = SourceRef(path="b.mp4", duration=50)
    e.tracks.overlay = [Overlay(id="o0001", source="bbbbbbbbbbbb", start=0, end=2, anchor="c0002", offset=7.0)]  # source 27
    ops.split(e, "c0002", 25)
    ov = e.tracks.overlay[0]
    assert (ov.anchor, ov.offset) == ("c0003", 2.0)
    ops.trim(e, "c0003", start=26)
    assert (ov.anchor, ov.offset) == ("c0003", 1.0)
    ops.move(e, "c0003", before="c0001")
    assert ov.anchor == "c0003" and e.timemap().span("c0003").out_start == 0.0
    ops.cut(e, 26, 30)
    res = lintmod.lint(e, check_files=False)
    assert any(i["rule"] == "orphan_anchor" and i["item"] == "o0001" for i in res["issues"])
    assert not res["passed"]


# --------------------------------------------------------------------------- lint


def test_lint_rules_fail_and_pass():
    tr = {"segments": [{"id": 0, "start": 0, "end": 10, "text": "", "words": [{"start": 4.8, "end": 5.4, "word": "algorithm"}]}]}
    bad = _edl((0, 5.0), (5.1, 5.2), (5.3, 9.0), (8.0, 9.5))
    res = lintmod.lint(bad, transcripts={"aaaaaaaaaaaa": tr}, check_files=False, min_clip=0.3, min_cut=0.2)
    rules = {i["rule"] for i in res["issues"]}
    assert {"clipped_word", "tiny_clip", "tiny_cut", "overlap"} <= rules
    cw = next(i for i in res["issues"] if i["rule"] == "clipped_word")
    assert cw["fix"] == "videocli edl trim t c0001 --end 5.4"
    good = _edl((0, 4.5), (6.0, 9.0))
    assert lintmod.lint(good, transcripts={"aaaaaaaaaaaa": tr}, check_files=False)["passed"]
    oob = _edl((0, 5), duration=4.0)
    oob.tracks.main[0].end = 5.0
    assert any(i["rule"] == "out_of_bounds" for i in lintmod.lint(oob, check_files=False)["issues"])


def test_lint_source_changed(tmp_path, av_clip, video_only):
    import shutil

    from videocli.core.source import compute_source_id

    p = tmp_path / "s.mp4"
    shutil.copy(av_clip, p)
    sid = compute_source_id(p)
    e = EDL(name="t", sources={sid: SourceRef(path=str(p), duration=4.0)})
    e.tracks.main = [Clip(id="c0001", source=sid, start=0, end=2)]
    assert lintmod.lint(e)["passed"]
    shutil.copy(video_only, p)
    assert any(i["rule"] == "source_changed" for i in lintmod.lint(e)["issues"])


# --------------------------------------------------------------------------- render


@pytest.fixture(scope="session")
def counter_clip(media_dir) -> Path:
    """5 s at 25 fps; frame N carries N as 8 black/white stripes (bit k = stripe k), plus a 440 Hz tone."""
    p = media_dir / "counter.mkv"
    if not p.exists():
        ff(
            "-f", "lavfi", "-i", "color=c=black:s=64x64:r=25:d=5,format=yuv420p,geq=lum='if(bitand(N,pow(2,floor(X/8))),235,16)':cb=128:cr=128",
            "-f", "lavfi", "-i", "sine=f=440:sample_rate=48000:d=5",
            "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", "-g", "10", "-c:a", "pcm_s16le", str(p),
        )
    return p


def frame_values(path: Path) -> list[int]:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf", "scale=64:64,format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    ).stdout
    frames = np.frombuffer(out, dtype=np.uint8).reshape(-1, 64, 64)
    vals = []
    for f in frames:
        stripes = [f[16:48, k * 8 + 2: k * 8 + 6].mean() > 128 for k in range(8)]
        vals.append(sum(1 << k for k, on in enumerate(stripes) if on))
    return vals


def stream_durations(path: Path) -> dict[str, float]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(path)],
                         capture_output=True, check=True, text=True).stdout
    return {s["codec_type"]: float(s["duration"]) for s in json.loads(out)["streams"]}


def _counter_edl(cli, counter_clip, name="fc", ranges=((0.4, 1.0), (2.0, 2.6), (3.2, 4.0))) -> str:
    cli.ok("probe", str(counter_clip))
    cli.ok("plan", str(counter_clip), "-o", name, *sum((["--keep-only", f"{a}-{b}"] for a, b in ranges), []), "--min-clip", "0", "--no-snap")
    p = Path(".videocli/edits") / f"{name}.edl.json"
    d = json.loads(p.read_text())
    d["output"]["crf"] = 10
    p.write_text(json.dumps(d))
    return name


def test_render_frame_accurate_joins(cli, counter_clip, tmp_path):
    name = _counter_edl(cli, counter_clip)
    out = tmp_path / "fc.mp4"
    d = cli.ok("render", name, "-o", str(out), "--progress", "none")
    assert d["frames"] == 15 + 15 + 20
    vals = frame_values(out)
    expected = list(range(10, 25)) + list(range(50, 65)) + list(range(80, 100))
    assert vals == expected
    durs = stream_durations(out)
    assert abs(durs["video"] - durs["audio"]) < 1 / 25
    assert durs["video"] == pytest.approx(2.0, abs=1 / 25)


def test_rerender_after_one_change_runs_one_segment_and_lane(cli, counter_clip, tmp_path):
    name = _counter_edl(cli, counter_clip, "fc2")
    cli.ok("render", name, "-o", str(tmp_path / "a.mp4"))
    again = cli.ok("render", name, "-o", str(tmp_path / "b.mp4"))
    assert again["nodes"]["ran"] == 0
    cli.ok("edl", "trim", name, "c0002", "--end", "2.4")
    third = cli.ok("render", name, "-o", str(tmp_path / "c.mp4"))
    # one segment + one lane + picture + mix
    assert third["nodes"]["ran"] == 4


def test_interrupted_render_resumes(cli, counter_clip, tmp_path, monkeypatch):
    from videocli.render.nodes import media

    name = _counter_edl(cli, counter_clip, "fc3")
    real = media.run_mix

    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(media, "run_mix", boom)
    code, _ = cli("render", name, "-o", str(tmp_path / "x.mp4"), "--jobs", "1")
    assert code == 130
    jobs = cli.ok("jobs")
    assert jobs["total"] == 1
    monkeypatch.setattr(media, "run_mix", real)
    d = cli.ok("render", name, "-o", str(tmp_path / "x.mp4"))
    assert 0 < d["nodes"]["cached"] < d["nodes"]["total"]
    assert cli.ok("jobs")["total"] == 0
    ref = cli.ok("render", name, "-o", str(tmp_path / "y.mp4"))
    assert ref["nodes"]["ran"] == 0
    assert (tmp_path / "x.mp4").read_bytes() == (tmp_path / "y.mp4").read_bytes()


def test_render_dry_run_estimate_and_preview(cli, counter_clip, tmp_path):
    name = _counter_edl(cli, counter_clip, "fc4")
    dry = cli.ok("render", name, "-o", str(tmp_path / "z.mp4"), "--dry-run")
    kinds = [n["kind"] for n in dry["nodes"]]
    assert kinds.count("segment") == 3 and kinds.count("lane") == 3 and kinds[-1] == "encode"
    est = cli.ok("render", name, "-o", str(tmp_path / "z.mp4"), "--estimate")["estimate"]
    assert est["output_duration"] == pytest.approx(2.0) and est["seconds"] > 0
    assert not (tmp_path / "z.mp4").exists()
    rev = cli.ok("render", name, "-o", str(tmp_path / "rev.mp4"), "--around-cuts", "0.2")
    assert rev["clips"] == 4 and stream_durations(tmp_path / "rev.mp4")["video"] == pytest.approx(0.8, abs=0.05)


def test_render_refuses_source_and_unrenderable(cli, counter_clip):
    name = _counter_edl(cli, counter_clip, "fc5")
    code, env = cli("render", name, "-o", str(counter_clip))
    assert code == 4 and env["error"]["code"] == "OUTPUT_IS_SOURCE"
    p = Path(".videocli/edits/fc5.edl.json")
    d = json.loads(p.read_text())
    d["transitions"] = [{"after": "c0001", "type": "dissolve", "duration": 0.2}]
    p.write_text(json.dumps(d))
    code, env = cli("render", name, "-o", "t.mp4")
    assert code == 2 and env["error"]["code"] == "FEATURE_NOT_AVAILABLE" and env["error"]["details"]["phase"] == 6


def test_edl_invalid_on_hand_edit(cli, counter_clip):
    name = _counter_edl(cli, counter_clip, "fc6")
    p = Path(".videocli/edits/fc6.edl.json")
    p.write_text(p.read_text().replace('"end": 1.0', '"end": 0.1', 1))
    code, env = cli("edl", "show", name)
    assert code == 4 and env["error"]["code"] == "EDL_INVALID" and "tracks.main" in env["error"]["message"]


def test_many_clips_no_drift(cli, counter_clip, tmp_path):
    cli.ok("probe", str(counter_clip))
    ranges = [(round(i * 0.08, 3), round(i * 0.08 + 0.04, 3)) for i in range(60)]
    _counter_edl(cli, counter_clip, "many", ranges=ranges)
    d = cli.ok("render", "many", "-o", str(tmp_path / "many.mp4"), "--preview")
    durs = stream_durations(tmp_path / "many.mp4")
    assert d["frames"] == 60
    assert abs(durs["video"] - durs["audio"]) < 1 / 25
    assert durs["video"] == pytest.approx(60 / 25, abs=1 / 25)


# --------------------------------------------------------------------------- captions


def test_captions_drop_cut_words_and_stay_in_bounds():
    from videocli.render import captions

    e = _edl((0, 3), (6, 9), duration=10)
    tr = {"segments": [
        {"id": 0, "start": 0, "end": 9, "text": "", "words": [
            {"start": 0.2, "end": 0.6, "word": "Hello"}, {"start": 0.7, "end": 1.2, "word": "world."},
            {"start": 3.5, "end": 4.0, "word": "CUTWORD"}, {"start": 6.2, "end": 6.8, "word": "Back"},
            {"start": 6.9, "end": 8.95, "word": "again."}]}]}
    cues = captions.build(e, {"aaaaaaaaaaaa": tr})
    text = " ".join(c[2] for c in cues)
    assert "CUTWORD" not in text and "Back" in text
    assert all(0 <= a < b <= e.output_duration() for a, b, _ in cues)
    words = captions.map_words(e, {"aaaaaaaaaaaa": tr})
    back = next(w for w in words if w["word"] == "Back")
    assert back["start"] == pytest.approx(3.2)


def test_timemap_matches_edl():
    e = _edl((0, 2), (5, 6))
    tm: TimeMap = e.timemap()
    assert tm.duration == 3.0 and tm.to_output("aaaaaaaaaaaa", 5.5) == [2.5]
