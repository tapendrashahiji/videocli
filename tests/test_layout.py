"""Layout: screen text, faces and free space; placement from it; validators."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from conftest import ff
from videocli.layout import geometry as geo

FONT = Path(__file__).resolve().parents[1] / "src" / "videocli" / "render" / "bakers" / "fonts"
rapidocr = pytest.importorskip("rapidocr", reason="layout tests need the perception group (rapidocr)")


@pytest.fixture(scope="session")
def screen_clip(media_dir) -> Path:
    """640x360, 6 s: 'Helpful keymaps' list bottom-left for 0-3 s, 'Tab management' top-right for 3-6 s."""
    p = media_dir / "screen.mp4"
    if not p.exists():
        bold, reg = FONT / "NotoSans-Bold.ttf", FONT / "NotoSans-Regular.ttf"
        vf = (f"drawtext=fontfile={bold}:text='Helpful keymaps':x=30:y=250:fontsize=26:fontcolor=white:enable='lt(t,3)',"
              f"drawtext=fontfile={reg}:text='r to reload':x=40:y=290:fontsize=20:fontcolor=white:enable='lt(t,3)',"
              f"drawtext=fontfile={bold}:text='Tab management':x=380:y=40:fontsize=26:fontcolor=white:enable='gte(t,3)'")
        ff("-f", "lavfi", "-i", "color=c=0x101010:s=640x360:r=25:d=6", "-f", "lavfi", "-i", "sine=f=300:sample_rate=48000:d=6",
           "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p))
    return p


# --------------------------------------------------------------------------- geometry


def test_geometry_basics():
    assert geo.covered_share([0, 0, 0.2, 0.2], [0.1, 0, 0.5, 0.5]) == pytest.approx(0.5)
    assert geo.expand([0.4, 0.4, 0.2, 0.2], 2) == [0.3, 0.3, 0.4, 0.4]
    busy = np.zeros((18, 32), bool)
    busy[:, :16] = True
    rects = geo.free_rects(busy)
    assert rects[0] == [0.5, 0.0, 0.5, 1.0]
    # 16:9 source into a 9:16 centre crop: the middle third survives
    assert geo.map_box([0.0, 0.1, 0.1, 0.1], (1280, 720), (360, 640), type("R", (), {"mode": "center"})(), 0.5) is None
    m = geo.map_box([0.45, 0.4, 0.1, 0.1], (1280, 720), (360, 640), type("R", (), {"mode": "center"})(), 0.5)
    assert m is not None and m[0] == pytest.approx(0.342, abs=0.005) and m[2] == pytest.approx(0.316, abs=0.005)


def test_split_windows_ignores_talking_face():
    from videocli.layout.analyzer import split_windows

    times = np.arange(6.0)
    frames = np.zeros((6, 90, 160), np.uint8)
    frames[:, 60:80, 130:150] = np.random.default_rng(0).integers(0, 255, (6, 20, 20))  # moving "face"
    frames[3:, 10:20, 10:60] = 255  # the screen changes at t=3
    faces = [[[130, 60, 20, 20]]] * 6
    wins = split_windows(times, frames, faces)
    assert [(a, b) for a, b, _k in wins] == [(0.0, 3.0), (3.0, 6.0)]


# --------------------------------------------------------------------------- analyzer and command


def test_layout_reads_text_and_finds_it(cli, screen_clip):
    d = cli.ok("layout", str(screen_clip))
    wins = d["windows"]
    assert len(wins) == 2 and wins[0]["end"] == pytest.approx(3.0, abs=1.01)
    first = " ".join(b["text"] for b in wins[0]["blocks"])
    assert "Helpful keymaps" in first and "reload" in first
    blk = wins[0]["blocks"][0]["box"]
    assert blk[0] < 0.12 and blk[1] > 0.6  # bottom-left
    assert wins[0]["free"] and all(geo.covered_share(f["box"], blk) < 0.05 for f in wins[0]["free"])
    hit = cli.ok("layout", str(screen_clip), "--find", "tab management")["hits"][0]
    assert hit["start"] == pytest.approx(3.0, abs=1.01) and hit["box"][0] > 0.5 and hit["box"][1] < 0.25
    assert "text" in cli.ok("layout", str(screen_clip), "--lines", "--at", "1")["windows"][0]


def _edl(cli, clip, name):
    cli.ok("analyze", str(clip), "--skip", "transcript")
    cli.ok("analyze", str(clip), "--only", "layout")
    cli.ok("plan", str(clip), "-o", name, "--keep-only", "0-6", "--no-snap")


def test_gfx_place_auto_moves_off_text(cli, screen_clip):
    _edl(cli, screen_clip, "pl")
    d = cli.ok("gfx", "add", "pl", "-t", "lower-third", "--param", "title=Shift + ?", "--on", "c0001", "--offset", "0.5", "--duration", "2")
    pl = d["placement"]
    assert pl["moved"] and "Helpful" in pl["reason"] and pl["covers"] == []
    # later, the text is top-right: the default bottom-left spot is clear
    d2 = cli.ok("gfx", "add", "pl", "-t", "lower-third", "--param", "title=Next", "--on", "c0001", "--offset", "3.5", "--duration", "2")
    assert d2["placement"]["moved"] is False and "clear" in d2["placement"]["reason"]
    keep = cli.ok("gfx", "add", "pl", "-t", "lower-third", "--param", "title=Keep", "--on", "c0001", "--offset", "0.5", "--place", "keep")
    assert keep["placement"] == {}
    s = cli.ok("gfx", "set", "pl", keep["item"], "--place", "auto")
    assert s["placement"]["moved"] and "box_x" in s["params"]


def test_box_xy_moves_the_rendered_box():
    from videocli.graphics import templates as tp
    from videocli.graphics.titles import layout

    t = tp.get(None, "lower-third")
    base = layout(t, tp.resolve_params(t, {"title": "Hi"}, tp.Brand()), tp.Brand(), 640, 360)
    moved = layout(t, tp.resolve_params(t, {"title": "Hi", "box_x": 0.6, "box_y": 0.1}, tp.Brand()), tp.Brand(), 640, 360)
    assert moved.box[0] == 384 and moved.box[1] == 36
    assert (moved.box[2] - moved.box[0]) == (base.box[2] - base.box[0])
    a = np.asarray(moved.layer)[:, :, 3]
    assert a[36:60, 384:420].max() > 0 and a[300:, :100].max() == 0


def test_captions_avoid_on_screen_text(cli, screen_clip, monkeypatch):
    from videocli.core.workspace import Workspace
    from videocli.edl import io
    from videocli.layout.place import caption_avoid

    _edl(cli, screen_clip, "cav")
    ranges = caption_avoid(io.load(Workspace.open(), "cav"), Workspace.open())
    assert ranges and ranges[0][0] == 0.0 and ranges[0][1] <= 3.5  # bottom text only in the first window


def test_stack_face_auto_uses_layout_faces(cli, media_dir, tmp_path, monkeypatch):
    from videocli.layout import analyzer

    p = media_dir / "bubble2.mp4"
    if not p.exists():
        ff("-f", "lavfi", "-i", "color=c=blue:s=640x360:r=25:d=3", "-f", "lavfi", "-i", "sine=f=300:sample_rate=48000:d=3",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p))
    # a fake detector: one face at the lower right in every frame
    monkeypatch.setattr(analyzer, "detect_faces", lambda g, min_frac=0.04: [[int(g.shape[1] * 0.85), int(g.shape[0] * 0.7), int(g.shape[1] * 0.08), int(g.shape[0] * 0.14)]])
    cli.ok("analyze", str(p), "--skip", "transcript")
    cli.ok("analyze", str(p), "--only", "layout")
    cli.ok("plan", str(p), "-o", "sfa", "--keep-only", "0-3", "--no-snap")
    d = cli.ok("edl", "reframe", "sfa", "--aspect", "9:16", "--mode", "stack", "--face", "auto")
    fx, fy, fw, fh = d["face"]
    assert 0.78 < fx < 0.85 and 0.6 < fy < 0.7 and 0.14 < fw < 0.18


# --------------------------------------------------------------------------- validators


def _issues(cli, name):
    code, env = cli("gfx", "lint", name)
    d = env["data"] if env["ok"] else env["error"]["details"]
    return d["issues"]


def test_lint_flags_label_on_screen_text(cli, screen_clip):
    _edl(cli, screen_clip, "lt")
    d = cli.ok("gfx", "add", "lt", "-t", "lower-third", "--param", "title=Shift + ?", "--on", "c0001", "--offset", "0.5",
               "--duration", "2", "--place", "keep")
    rules = {i["rule"]: i for i in _issues(cli, "lt")}
    assert "covers_screen_text" in rules and rules["covers_screen_text"]["item"] == d["item"]
    assert rules["covers_screen_text"]["fix"] == f"videocli gfx set lt {d['item']} --place auto"
    assert "Helpful" in rules["covers_screen_text"]["covers"][0]["text"]
    cli.ok("gfx", "set", "lt", d["item"], "--place", "auto")
    assert not [i for i in _issues(cli, "lt") if i["rule"] == "covers_screen_text"]


def test_lint_crop_cuts_text(cli, screen_clip):
    _edl(cli, screen_clip, "cc")
    cli.ok("edl", "reframe", "cc", "--aspect", "9:16")
    rules = [i for i in _issues(cli, "cc") if i["rule"] == "crop_cuts_text"]
    assert rules and rules[0]["clip"] == "c0001" and "--clip c0001 --x" in rules[0]["fix"]


def test_verify_layout_pass_and_warn(cli, screen_clip, tmp_path):
    _edl(cli, screen_clip, "vg")
    good = cli.ok("gfx", "add", "vg", "-t", "lower-third", "--param", "title=Shift Help", "--on", "c0001", "--offset", "0.3", "--duration", "2.2")
    assert good["placement"]["moved"]
    out = tmp_path / "vg.mp4"
    cli.ok("render", "vg", "-o", str(out))
    d = cli.ok("verify", str(out), "--edl", "vg", "--checks", "layout")
    c = d["checks"][0]
    assert c["status"] == "pass" and c["measured"]["graphics"] == 1 and c["measured"]["unreadable"] == 0

    cli.ok("gfx", "set", "vg", good["item"], "--place", "keep")
    out2 = tmp_path / "vg2.mp4"
    cli.ok("render", "vg", "-o", str(out2))
    d2 = cli.ok("verify", str(out2), "--edl", "vg", "--checks", "layout")  # a warning does not fail verify
    c2 = d2["checks"][0]
    assert c2["status"] == "warn" and c2["items"][0]["problem"] == "graphic_covers_screen"
    assert c2["items"][0]["fix"].endswith("--place auto")


def test_verify_layout_fails_when_label_unreadable(cli, screen_clip, tmp_path, monkeypatch):
    from videocli.layout import analyzer

    _edl(cli, screen_clip, "vu")
    cli.ok("gfx", "add", "vu", "-t", "lower-third", "--param", "title=Readable", "--on", "c0001", "--offset", "3.2", "--duration", "2")
    out = tmp_path / "vu.mp4"
    cli.ok("render", "vu", "-o", str(out))

    class Blind:
        def read(self, rgb):
            return []

    monkeypatch.setattr(analyzer, "READER_FACTORY", lambda backend, lang: Blind())
    code, env = cli("verify", str(out), "--edl", "vu", "--checks", "layout")
    assert code == 5 and env["error"]["code"] == "VERIFY_FAILED"
    item = env["error"]["details"]["checks"][0]["items"][0]
    assert item["problem"] == "not_readable" and "scale=" in item["fix"]


def test_objects_from_reader_reach_windows_and_lint(cli, screen_clip, monkeypatch):
    from videocli.layout import analyzer

    class WithObject:
        provenance = "fake"

        def read(self, rgb):
            return [{"kind": "object", "label": "laptop", "box": [0.02, 0.6, 0.4, 0.35]}]

    monkeypatch.setattr(analyzer, "READER_FACTORY", lambda backend, lang: WithObject())
    _edl(cli, screen_clip, "ob")
    w = cli.ok("layout", str(screen_clip))["windows"][0]
    assert w["objects"][0]["label"] == "laptop"
    cli.ok("gfx", "add", "ob", "-t", "lower-third", "--param", "title=Hi", "--on", "c0001", "--offset", "0.5", "--place", "keep")
    assert any(i["rule"] == "covers_object" for i in _issues(cli, "ob"))
