"""Phase 8: templates, titles baker, captions, timing, lint and bake caching."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from videocli.graphics import templates as tp
from videocli.graphics.titles import ease_out, frame, layout

TRANSCRIPT_WORDS = [("Today", 0.2, 0.6), ("we", 0.7, 0.85), ("look", 0.9, 1.2), ("at", 1.25, 1.35), ("binary", 1.4, 1.9), ("search.", 1.95, 2.5),
                    ("It", 3.0, 3.1), ("is", 3.15, 3.3), ("fast.", 3.35, 3.9)]


def _edl_with_transcript(cli, av_clip, name="gx"):
    cli.ok("probe", str(av_clip))
    cli.ok("plan", str(av_clip), "-o", name, "--keep-only", "0-4")
    sid = next(iter(json.loads(Path(f".videocli/edits/{name}.edl.json").read_text())["sources"]))
    tr = {"schema": "videocli.transcript/v1", "key": "x", "created": "", "tool_version": "", "params": {}, "source_id": sid,
          "segments": [{"id": 0, "start": 0.2, "end": 3.9, "text": " ".join(w for w, _, _ in TRANSCRIPT_WORDS),
                        "words": [{"start": a, "end": b, "word": w, "p": 0.9} for w, a, b in TRANSCRIPT_WORDS]}]}
    Path(f".videocli/sources/{sid}/transcript.json").write_text(json.dumps(tr))
    return sid


def test_builtin_manifests_validate():
    ts = tp.discover(None)
    assert {"lower-third", "title-card", "end-card", "chapter-marker", "callout", "counter", "quote", "progress-bar", "logo-bug"} <= set(ts)


def test_missing_required_param_rejected_before_baking(cli, av_clip):
    from videocli.core.errors import UsageError

    with pytest.raises(UsageError):
        tp.resolve_params(tp.get(None, "lower-third"), {}, tp.Brand())
    _edl_with_transcript(cli, av_clip)
    code, env = cli("gfx", "add", "gx", "-t", "lower-third", "--on", "c0001")
    assert code == 2 and "title" in env["error"]["message"]
    code, env = cli("gfx", "add", "gx", "-t", "lower-third", "--on", "c0001", "--param", "title=x", "--param", "accent=red")
    assert code == 2


def test_titles_bake_contract(tmp_path):
    from videocli.graphics.titles import bake
    from videocli.media.probe import probe_path

    t = tp.get(None, "lower-third")
    out = bake(t, tp.resolve_params(t, {"title": "Hello"}, tp.Brand()), tp.Brand(), (320, 180), "25/1", 50, tmp_path)
    pr = probe_path(out)
    assert pr.video.width == 320 and pr.video.alpha and pr.video.fps == "25/1"
    assert pr.duration == pytest.approx(2.0, abs=0.05)


def test_easing_positions_follow_curve():
    t = tp.get(None, "lower-third")
    p = tp.resolve_params(t, {"title": "Ease"}, tp.Brand())
    W, H = 640, 360
    lay = layout(t, p, tp.Brand(), W, H)
    x0 = lay.box[0]
    for tt in (0.1, 0.2, 0.3):
        img = np.asarray(frame(t, p, tp.Brand(), W, H, tt, 4.0))
        cols = np.where(img[:, :, 3].max(axis=0) > 0)[0]
        expected_left = x0 + (1 - ease_out(tt / t.duration.in_)) * lay.extra["slide_from"]
        right = lay.box[2] + (1 - ease_out(tt / t.duration.in_)) * lay.extra["slide_from"]
        assert abs(cols.max() - right) <= 2, (tt, cols.max(), right, expected_left)
    steady = np.asarray(frame(t, p, tp.Brand(), W, H, 2.0, 4.0))
    assert steady[:, :, 3].max() == 255


def test_devanagari_shapes():
    from PIL import ImageFont, features

    if not features.check("raqm"):
        pytest.skip("Pillow built without raqm")
    f = ImageFont.truetype(tp.font_file(tp.Brand(), text="नमस्ते"), 48)
    shaped = f.getlength("नमस्ते", features=["-liga"])  # any shaping run
    assert f.getlength("नमस्ते") > 0 and shaped > 0
    t = tp.get(None, "title-card")
    p = tp.resolve_params(t, {"title": "नमस्ते दुनिया", "background": "#000000"}, tp.Brand())
    lay = layout(t, p, tp.Brand(), 640, 360)
    region = np.asarray(lay.layer)[lay.box[1]:lay.box[3], lay.box[0]:lay.box[2]]
    assert (region[:, :, :3] > 200).any()  # glyphs drawn (not tofu boxes of zero ink)


def test_karaoke_highlight_timing():
    from videocli.graphics.captions_ass import build, cue_times

    words = [{"start": a, "end": b, "word": w} for w, a, b in TRANSCRIPT_WORDS]
    ass = build(words, preset="karaoke", size=(1280, 720), duration=5.0, brand=tp.Brand())
    line = next(x for x in ass.splitlines() if x.startswith("Dialogue:"))
    start = cue_times(ass)[0][0]
    import re

    for (_w, _a, _b), _k in zip(TRANSCRIPT_WORDS, re.findall(r"\\k(\d+)\}([^{]*)", line), strict=False):
        pass
    # walk the \k tags: each word's highlight must begin within one frame (40 ms) of its timestamp
    tags = re.findall(r"\{\\k(\d+)\}([^{]*)", line)
    cur = start
    idx = 0
    for dur, text in tags:
        if text.strip():
            assert abs(cur - TRANSCRIPT_WORDS[idx][1]) <= 0.04, (text, cur)
            idx += 1
        cur += int(dur) / 100
    bold = build(words, preset="bold-words", size=(720, 1280), duration=5.0, brand=tp.Brand())
    assert "BINARY SEARCH." in bold or "AT BINARY SEARCH." in bold


def test_at_word_land_end(cli, av_clip):
    _edl_with_transcript(cli, av_clip, "aw")
    d = cli.ok("gfx", "add", "aw", "-t", "lower-third", "--param", "title=Binary search", "--at-word", "binary search", "--land", "end")
    t = tp.get(None, "lower-third")
    reveal_done = d["offset"] + t.duration.in_
    assert reveal_done == pytest.approx(d["timing"]["word_end"], abs=0.04)
    code, env = cli("gfx", "add", "aw", "-t", "lower-third", "--param", "title=x", "--at-word", "linked list")
    assert code == 4 and env["error"]["code"] == "PHRASE_NOT_FOUND"


def test_anchor_follows_clip_and_lint_flags_removal(cli, av_clip):
    _edl_with_transcript(cli, av_clip, "an")
    cli.ok("edl", "split", "an", "c0001", "--at", "2")
    cli.ok("gfx", "add", "an", "-t", "callout", "--param", "text=Look", "--on", "c0002", "--offset", "0.5", "--duration", "1.2")
    cli.ok("edl", "move", "an", "c0002", "--before", "c0001")
    lst = cli.ok("gfx", "list", "an")["graphics"][0]
    assert lst["anchor"] == "c0002" and lst["at"] == pytest.approx(0.5)
    cli.ok("edl", "cut", "an", "--from", "2", "--to", "4")
    code, env = cli("gfx", "lint", "an")
    assert code == 5 and any(i["rule"] == "orphan_anchor" for i in env["error"]["details"]["issues"])


def test_gfx_lint_rules(cli, av_clip):
    _edl_with_transcript(cli, av_clip, "gl")
    cli.ok("gfx", "add", "gl", "-t", "title-card", "--param", "title=Ok", "--on", "c0001", "--duration", "2.5")
    assert cli.ok("gfx", "lint", "gl")["passed"]
    cli.ok("gfx", "add", "gl", "-t", "quote", "--param", "text=" + "very long words " * 6, "--on", "c0001", "--offset", "0.1", "--duration", "1.0")
    cli.ok("gfx", "add", "gl", "-t", "callout", "--param", "text=late", "--on", "c0001", "--offset", "3.5", "--duration", "2")
    code, env = cli("gfx", "lint", "gl")
    assert "issues" in env["error"]["details"], env["error"]
    rules = {i["rule"] for i in env["error"]["details"]["issues"]}
    assert {"too_short_to_read", "simultaneous_reveals", "past_timeline_end"} <= rules


def test_graphic_render_and_bake_cache(cli, av_clip, tmp_path):
    _edl_with_transcript(cli, av_clip, "gr")
    cli.ok("gfx", "add", "gr", "-t", "lower-third", "--param", "title=One", "--on", "c0001", "--offset", "0.5", "--duration", "2")
    cli.ok("gfx", "add", "gr", "-t", "chapter-marker", "--param", "title=Two", "--on", "c0001", "--offset", "1.0", "--duration", "2", "--layer", "1")
    out = tmp_path / "gr.mp4"
    first = cli.ok("render", "gr", "-o", str(out))
    assert first["chunks"]["composite"] >= 2
    cli.ok("gfx", "set", "gr", "x0001", "--param", "title=Uno")
    second = cli.ok("render", "gr", "-o", str(out))
    bakes_ran = second["nodes"]["ran"]
    # one bake re-runs plus the composites it covers and the picture join; the other bake is reused
    assert bakes_ran < first["nodes"]["ran"]
    code, env = cli("verify", str(out), "--edl", "gr", "--checks", "duration,av_sync")
    assert code == 0


def test_heavy_bakers_report_not_installed(cli, av_clip, tmp_path, monkeypatch):
    from videocli.setup import profiles

    _edl_with_transcript(cli, av_clip, "hv")
    d = tmp_path / "eq"
    cli.ok("gfx", "new-template", str(d), "--baker", "manim")
    proj = Path(".videocli/project/templates")
    proj.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copytree(d, proj / "eq")
    monkeypatch.setattr(profiles, "module_present", lambda m: m != "manim")
    cli.ok("gfx", "add", "hv", "-t", "eq", "--param", "equation=E=mc^2", "--on", "c0001", "--duration", "1")
    code, env = cli("render", "hv", "-o", str(tmp_path / "hv.mp4"))
    assert code == 3 and env["error"]["code"] == "FEATURE_NOT_INSTALLED" and "--with manim" in env["error"]["hint"]


def test_burned_karaoke_captions_render(cli, av_clip, tmp_path):
    _edl_with_transcript(cli, av_clip, "kc")
    cli.ok("captions", "style", "kc", "--preset", "karaoke")
    out = tmp_path / "kc.mp4"
    d = cli.ok("render", "kc", "-o", str(out), "--burn-captions")
    assert d["captions_burned"] is True


def test_brand_change_updates_graphics(cli, av_clip):
    from videocli.core.workspace import Workspace, resolve_root
    from videocli.edl import io
    from videocli.render import presets
    from videocli.render.timeline import compile_timeline

    _edl_with_transcript(cli, av_clip, "br")
    cli.ok("gfx", "add", "br", "-t", "lower-third", "--param", "title=Brand", "--on", "c0001", "--duration", "2")
    ws = Workspace.open(resolve_root())
    k1 = next(n for n in compile_timeline(io.load(ws, "br"), presets.get("source"), crf=18, x264_preset="veryfast", ws=ws).nodes
              if n.kind == "bake")
    cli.ok("brand", "set", "--colors", "#000000,#ffffff,#00aa55")
    from videocli.render.graph import tool_versions

    k2 = next(n for n in compile_timeline(io.load(ws, "br"), presets.get("source"), crf=18, x264_preset="veryfast", ws=ws).nodes
              if n.kind == "bake")
    tv = tool_versions()
    assert k1.compute_key(tv) != k2.compute_key(tv)


def test_caption_layout_params():
    from videocli.graphics.captions_ass import build

    words = [{"start": i * 0.3, "end": i * 0.3 + 0.25, "word": f"word{i:02d}"} for i in range(40)]

    def dialogue_lens(ass):
        return [len(re.sub(r"\{[^}]*\}", "", x.split(",", 9)[9]).strip()) for x in ass.splitlines() if x.startswith("Dialogue:")]

    import re

    wide = build(words, preset="karaoke", size=(640, 360), duration=20, brand=tp.Brand())
    narrow = build(words, preset="karaoke", size=(640, 360), duration=20, brand=tp.Brand(),
                   params={"max_chars": "20", "lines": "1", "position": "top", "margin": "0.05"})
    assert max(dialogue_lens(narrow)) <= 20 < max(dialogue_lens(wide))
    style = next(x for x in narrow.splitlines() if x.startswith("Style:")).split(",")
    assert style[18] == "8" and style[21] == str(int(360 * 0.05))  # top-centre, margin 5% of height
    assert next(x for x in wide.splitlines() if x.startswith("Style:")).split(",")[18] == "2"


def test_caption_layout_params_validated(cli, av_clip):
    _edl_with_transcript(cli, av_clip, "cl")
    code, env = cli("captions-style", "cl", "--preset", "karaoke", "--param", "lines=3")
    assert code == 2 and "lines" in env["error"]["message"]
    code, env = cli("captions-style", "cl", "--param", "position=left")
    assert code == 2
    d = cli.ok("captions-style", "cl", "--preset", "karaoke", "--param", "max_chars=32", "--param", "lines=1")
    assert d["params"] == {"max_chars": "32", "lines": "1"}


def test_caption_two_lines_break_balanced():
    import re

    from videocli.graphics.captions_ass import break_index, build

    assert break_index(["short"], 18, 2) is None
    assert break_index(["mark", "le", "help", "and", "d", "ra", "yu"], 18, 1) is None
    i = break_index(["mark", "le", "help,", "and", "d", "ra", "yu,", "and", "j", "k,"], 18, 2)
    assert i is not None
    words = [{"start": i * 0.3, "end": i * 0.3 + 0.25, "word": w} for i, w in enumerate(["mark", "le", "help,", "and", "d", "ra", "yu,", "and", "j", "k,"])]
    for preset in ("karaoke", "bold-words", "clean"):
        ass = build(words, preset=preset, size=(360, 640), duration=10, brand=tp.Brand(), params={"max_chars": "18", "lines": "2"})
        lines = [x.split(",", 9)[9] for x in ass.splitlines() if x.startswith("Dialogue:")]
        for text in lines:
            for part in re.sub(r"\{[^}]*\}", "", text).split("\\N"):
                assert len(part.strip()) <= 18, (preset, text)
        if preset != "bold-words":  # bold-words shows 3 words at a time
            assert any("\\N" in t for t in lines), preset


def test_scale_param_shrinks_lower_third():
    from videocli.graphics.titles import layout

    t = tp.get(None, "lower-third")

    def box(scale):
        p = tp.resolve_params(t, {"title": "Shift + ?", "subtitle": "Show all shortcuts", "scale": scale}, tp.Brand())
        b = layout(t, p, tp.Brand(), 640, 360).box
        return b[2] - b[0], b[3] - b[1], b[0]

    w1, h1, x1 = box(1.0)
    w6, h6, x6 = box(0.6)
    assert w6 < w1 * 0.7 and h6 < h1 * 0.7 and x6 == x1  # smaller, same anchor
    from videocli.core.errors import UsageError

    with pytest.raises(UsageError):
        tp.resolve_params(t, {"title": "x", "scale": 5}, tp.Brand())


def test_captions_auto_move_to_top_over_lower_third():
    from videocli.graphics.captions_ass import build

    words = [{"start": i * 0.5, "end": i * 0.5 + 0.4, "word": f"w{i}."} for i in range(12)]
    ass = build(words, preset="karaoke", size=(640, 360), duration=10, brand=tp.Brand(), avoid=[(2.0, 4.0)])
    dl = [x for x in ass.splitlines() if x.startswith("Dialogue:")]
    top = [x for x in dl if "{\\an8}" in x]
    assert top and len(top) < len(dl)
    from videocli.graphics.captions_ass import cue_times

    for x in dl:
        a, b = cue_times(x + "\n")[0]
        assert ("{\\an8}" in x) == (a < 4.0 and b > 2.0)
    fixed = build(words, preset="karaoke", size=(640, 360), duration=10, brand=tp.Brand(), avoid=[(2.0, 4.0)], params={"position": "bottom"})
    assert "{\\an8}" not in fixed


def test_bottom_graphics_from_edl(cli, av_clip):
    from videocli.core.workspace import Workspace
    from videocli.edl import io
    from videocli.graphics.captions_ass import bottom_graphics

    _edl_with_transcript(cli, av_clip, "bg")
    cli.ok("gfx", "add", "bg", "-t", "lower-third", "--param", "title=Hi", "--at-word", "binary search", "--duration", "2")
    cli.ok("gfx", "add", "bg", "-t", "chapter-marker", "--param", "title=Top", "--at-word", "binary search")
    rngs = bottom_graphics(io.load(Workspace.open(), "bg"))
    assert len(rngs) == 1 and rngs[0][1] - rngs[0][0] == pytest.approx(2.0)


def test_reading_time_equal_after_rounding_is_enough(cli, av_clip):
    from videocli.core.timeutil import ms
    from videocli.graphics.timing import reading_time

    _edl_with_transcript(cli, av_clip, "rt")
    title = "Browse Without a Mouse"
    need = ms(reading_time(title))
    cli.ok("gfx", "add", "rt", "-t", "lower-third", "--param", f"title={title}", "--at-word", "binary search", "--duration", str(need))
    d = cli.ok("gfx", "lint", "rt")
    assert not [i for i in d["issues"] if i["rule"] == "too_short_to_read"]


def test_captions_style_changes_only_what_is_given(cli, av_clip):
    _edl_with_transcript(cli, av_clip, "cs")
    cli.ok("captions-style", "cs", "--preset", "karaoke", "--param", "max_chars=28")
    d = cli.ok("captions-style", "cs", "--param", "box=1")
    assert d["preset"] == "karaoke" and d["params"] == {"max_chars": "28", "box": "1"} and d["mode"] == "burn"
    d = cli.ok("captions-style", "cs", "--unset", "box", "--no-burn")
    assert d["preset"] == "karaoke" and d["params"] == {"max_chars": "28"} and d["mode"] == "sidecar"
    assert cli.ok("captions-style", "cs", "--preset", "bold-words")["mode"] == "sidecar"
