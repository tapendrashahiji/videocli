"""Phase 7: grade ops, LUT compiler, balance and match, input transforms, graded renders, exports."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from conftest import ff
from videocli.color import lut as lutmod
from videocli.color import ops as cops
from videocli.color.match import balance_ops, match_cdl
from videocli.color.scopes import measure, white_balance

RNG = np.random.default_rng(7)


def test_ops_hand_computed():
    x = np.array([[0.5, 0.25, 0.1]])
    out = cops.op_cdl(x, slope=[2, 1, 1], offset=[0, 0.1, 0], power=[1, 1, 2])
    assert np.allclose(out, [[1.0, 0.35, 0.01]])
    y = cops.luma(np.array([[0.4, 0.4, 0.4]]))[0]
    assert np.allclose(cops.op_cdl(np.array([[0.4, 0.4, 0.4]]), sat=0.0), [[y, y, y]])
    assert np.allclose(cops.op_exposure(x, stops=1), 2 * x)
    assert np.allclose(cops.op_contrast(np.array([[0.6, 0.6, 0.6]]), amount=2, pivot=0.5), [[0.7] * 3])
    g = cops.op_temperature(np.array([[0.5, 0.5, 0.5]]), kelvin_shift=1000)
    assert g[0, 0] > g[0, 2] and abs(cops.luma(g)[0] - 0.5) < 1e-9
    assert np.allclose(cops.op_curves(np.array([[0.5, 0.5, 0.5]]), master=[[0, 0], [1, 0.5]]), [[0.25] * 3])
    r = cops.op_rolloff(np.array([[0.7, 0.9, 1.5]]), threshold=0.8)
    assert r[0, 0] == 0.7 and 0.8 < r[0, 1] < r[0, 2] < 1.0


def test_parse_op_and_ranges():
    from videocli.core.errors import UsageError

    op = cops.parse_op("cdl slope=1.05,1,0.95 sat=0.9")
    assert op == {"op": "cdl", "slope": [1.05, 1.0, 0.95], "sat": 0.9}
    with pytest.raises(UsageError):
        cops.parse_op("exposure stops=12")
    with pytest.raises(UsageError):
        cops.parse_op("sparkle amount=1")


def test_qualifier_only_touches_its_range():
    red = np.array([[0.8, 0.1, 0.1]])
    blue = np.array([[0.1, 0.1, 0.8]])
    q = {"hue": [340, 20], "soft": 0.05, "ops": [{"op": "cdl", "sat": 0.0}]}
    assert not np.allclose(cops.op_qualifier(red, **q), red)
    assert np.allclose(cops.op_qualifier(blue, **q), blue)


def test_lut_matches_direct_evaluation():
    ops = [{"op": "cdl", "slope": [1.1, 1.0, 0.9], "offset": [0.01, 0, -0.01], "power": [1, 1.1, 1], "sat": 0.85},
           {"op": "exposure", "stops": 0.2}, {"op": "contrast", "amount": 1.1, "pivot": 0.45}]
    table = lutmod.compile_stack(ops, 33)
    pts = RNG.random((2000, 3))
    direct = np.clip(cops.apply_stack(pts, ops), 0, 1)
    via = lutmod.apply_cube(pts, table, 33)
    assert np.abs(direct - via).max() < 0.015  # 33-point trilinear


def test_cube_round_trip(tmp_path):
    ops = [{"op": "temperature", "kelvin_shift": 600, "tint": 0}]
    p = tmp_path / "t.cube"
    lutmod.write_cube(p, lutmod.compile_stack(ops, 17), 17)
    size, table = lutmod.read_cube(p)
    assert size == 17 and np.allclose(table, lutmod.compile_stack(ops, 17), atol=1e-6)


@pytest.mark.parametrize("ops", [
    [{"op": "cdl", "slope": [1.04, 1.0, 0.96], "sat": 0.92}],
    [{"op": "contrast", "amount": 1.3}, {"op": "saturation", "amount": 1.2}],
    [{"op": "rolloff", "threshold": 0.8}, {"op": "exposure", "stops": 0.5}],
])
def test_grey_ramp_monotonic(ops):
    assert lutmod.check_table(lutmod.compile_stack(ops, 33), 33) == []


def test_broken_stack_rejected():
    bad = [{"op": "curves", "master": [[0, 1], [1, 0]]}]
    assert "grey ramp is not monotonic" in lutmod.check_table(lutmod.compile_stack(bad, 17), 17)


def test_balance_neutralises_cast():
    grey = np.clip(RNG.normal(0.45, 0.12, (5000, 1)), 0.05, 0.95) * np.ones((1, 3))
    cast = np.clip(grey * np.array([1.12, 1.0, 0.85]), 0, 1)
    assert white_balance(cast)["kelvin_shift"] > 500
    fixed = np.clip(cops.apply_stack(cast, balance_ops(cast)), 0, 1)
    assert abs(white_balance(fixed)["kelvin_shift"]) < 150


def test_match_reduces_difference_and_respects_strength():
    a = np.clip(RNG.normal([0.5, 0.45, 0.4], 0.1, (4000, 3)), 0, 1)
    b = np.clip(a * [0.9, 0.95, 1.1] + 0.03, 0, 1)

    def dist(x, y):
        return float(np.linalg.norm(x.mean(axis=0) - y.mean(axis=0)))

    full = np.clip(cops.apply_stack(b, [match_cdl(b, a, 1.0)]), 0, 1)
    half = np.clip(cops.apply_stack(b, [match_cdl(b, a, 0.5)]), 0, 1)
    assert dist(full, a) < dist(half, a) < dist(b, a)


def test_skin_constraint_clamps():
    a = np.clip(RNG.normal([0.5, 0.45, 0.4], 0.05, (2000, 3)), 0, 1)
    green = np.clip(a * [0.5, 1.6, 0.5], 0, 1)  # an absurd target that would turn skin green
    op = match_cdl(a, green, 1.0)
    assert op.get("_clamped")


def test_measure_reports_numbers():
    m = measure(np.full((100, 3), 0.5))
    assert m["luma"]["p50"] == pytest.approx(0.5) and m["saturation"] == 0


# --------------------------------------------------------------------------- CLI and render


def _two_cams(cli, media_dir, name="gc"):
    a, b = media_dir / "camA.mp4", media_dir / "camB.mp4"
    if not a.exists():
        ff("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:d=4", "-f", "lavfi", "-i", "sine=f=300:sample_rate=48000:d=4",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(a))
        ff("-i", str(a), "-vf", "colorchannelmixer=rr=1.15:bb=0.8,eq=brightness=-0.06", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "copy", str(b))
    cli.ok("probe", str(a))
    sid_b = cli.ok("probe", str(b))["source_id"]
    cli.ok("plan", str(a), "-o", name, "--keep-only", "0-2")
    p = Path(f".videocli/edits/{name}.edl.json")
    d = json.loads(p.read_text())
    d["sources"][sid_b] = {"path": str(b.resolve()), "duration": 4.0, "fps": "25/1"}
    d["tracks"]["main"].append({"id": "c0002", "source": sid_b, "start": 0.0, "end": 2.0})
    p.write_text(json.dumps(d))
    return a, b


def test_grade_auto_reduces_shot_difference(cli, media_dir):
    _two_cams(cli, media_dir)
    before = cli.ok("scopes", "gc")["shot_difference"]
    d = cli.ok("grade", "auto", "gc", "--match", "--strength", "1.0")
    assert d["shot_difference"]["after"] < before
    assert len(d["groups"]) == 2


def test_graded_render_matches_exported_cube(cli, media_dir, tmp_path):
    a, _ = _two_cams(cli, media_dir, "gl")
    cli.ok("grade", "set", "gl", "--look", "--op", "cdl slope=1.2,1.0,0.8 sat=0.7")
    out = tmp_path / "gl.mp4"
    r = cli.ok("render", "gl", "-o", str(out))
    assert r["frames"] == 100
    cli.ok("lut", "build", "gl", "--look", "-o", str(tmp_path / "look.cube"))
    # applying the exported .cube in ffmpeg directly reproduces the rendered colours
    ref = tmp_path / "ref.png"
    ff("-ss", "0.5", "-i", str(a), "-frames:v", "1", "-vf",
       f"scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=gbrp16le,lut3d=file={tmp_path / 'look.cube'},format=rgb24", str(ref))
    got = tmp_path / "got.png"
    ff("-ss", "0.5", "-i", str(out), "-frames:v", "1", "-vf", "scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=rgb24", str(got))
    import cv2

    diff = np.abs(cv2.imread(str(ref)).astype(int) - cv2.imread(str(got)).astype(int)).mean()
    assert diff < 6
    code, env = cli("verify", str(out), "--edl", "gl", "--checks", "legal_range,added_clipping,duration")
    assert code == 0, env


def test_shared_grade_rerenders_only_its_clips(cli, media_dir, tmp_path):
    _two_cams(cli, media_dir, "gs")
    cli.ok("grade", "set", "gs", "--clip", "c0002", "--group", "camb", "--op", "exposure stops=0.2")
    out = tmp_path / "gs.mp4"
    cli.ok("render", "gs", "-o", str(out))
    cli.ok("grade", "set", "gs", "--group", "camb", "--op", "saturation amount=0.9")
    d = cli.ok("render", "gs", "-o", str(out))
    assert d["nodes"]["ran"] == 2  # c0002's segment and the picture join


def test_ten_bit_intermediates_only_when_graded(cli, media_dir):
    from videocli.core.workspace import Workspace, resolve_root
    from videocli.edl import io
    from videocli.render import presets
    from videocli.render.timeline import compile_timeline

    _two_cams(cli, media_dir, "tb")
    ws = Workspace.open(resolve_root())
    comp = compile_timeline(io.load(ws, "tb"), presets.get("source"), crf=18, x264_preset="veryfast", grades_dir=ws.dir("grades"))
    assert comp.pix_fmt == "yuv420p"
    cli.ok("grade", "set", "tb", "--clip", "c0001", "--op", "exposure stops=0.1")
    comp = compile_timeline(io.load(ws, "tb"), presets.get("source"), crf=18, x264_preset="veryfast", grades_dir=ws.dir("grades"))
    assert comp.pix_fmt == "yuv420p10le" and comp.chunks[0].params["grade"]["lut"]


def test_hlg_input_transform(cli, hlg_clip, tmp_path):
    from videocli.media import ffmpeg as f

    if "zscale" not in f.capabilities().filters:
        pytest.skip("ffmpeg lacks zscale")
    cli.ok("probe", str(hlg_clip))
    cli.ok("plan", str(hlg_clip), "-o", "hlg", "--keep-only", "0-1")
    sid = next(iter(json.loads(Path(".videocli/edits/hlg.edl.json").read_text())["sources"]))
    d = cli.ok("grade", "input", "hlg", "--source", sid, "--transform", "auto")
    assert d["transform"] == "hlg_to_rec709"
    out = tmp_path / "hlg.mp4"
    cli.ok("render", "hlg", "-o", str(out))
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)], capture_output=True, text=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert v["pix_fmt"] == "yuv420p"


def test_log_source_requires_explicit_lut():
    from types import SimpleNamespace

    from videocli.color.input import choose_auto
    from videocli.core.errors import UsageError

    pr = SimpleNamespace(video=SimpleNamespace(color=SimpleNamespace(hdr=None, log_guess="s-log", primaries=None, transfer=None, matrix=None)))
    with pytest.raises(UsageError) as ei:
        choose_auto(pr)
    assert "LUT" in ei.value.message and "grade input" in ei.value.hint


def test_export_cdl_lists_impure(cli, media_dir, tmp_path):
    _two_cams(cli, media_dir, "cd")
    cli.ok("grade", "set", "cd", "--clip", "c0001", "--op", "cdl slope=1.1,1,0.9 sat=0.9")
    cli.ok("grade", "set", "cd", "--clip", "c0002", "--op", "exposure stops=0.3")
    code, env = cli("export", "cd", "--to", "cdl", "-o", str(tmp_path / "cd.ccc"))
    assert code == 0 and env["data"]["clips"] == ["c0001"] and env["data"]["not_pure_cdl"] == ["c0002"]
    assert "<Slope>1.100000 1.000000 0.900000</Slope>" in (tmp_path / "cd.ccc").read_text()
