"""Phase 5: work orders, shot metrics, camera motion, faces clustering, prosody, music, slides, suggestions."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from conftest import ff

# --------------------------------------------------------------------------- work orders


def test_order_create_partial_submit_and_validation(tmp_path):
    from videocli.core.errors import InputError, Pending
    from videocli.core.workspace import Workspace
    from videocli.providers import orders as om

    ws = Workspace(tmp_path / ".videocli")
    ws.create()
    items = [{"id": f"s{i}", "images": [], "question": "q", "answer_schema": {"description": "string", "shot_size": "CU|WS", "usable": "boolean"}}
             for i in range(3)]
    o = om.create(ws, "shot_log", items)
    assert om.create(ws, "shot_log", items)["id"] == o["id"]  # re-running reuses the order
    with pytest.raises(Pending) as ei:
        om.provider("agent").answer(ws, o, "videocli log x")
    assert ei.value.code == "ORDERS_PENDING" and ei.value.exit_code == 6
    lines = [json.dumps({"id": "s0", "answer": {"description": "A potter", "shot_size": "CU", "usable": True}}),
             json.dumps({"id": "s1", "answer": {"description": "x", "shot_size": "XL", "usable": True}})]
    with pytest.raises(InputError) as ei:
        om.submit(ws, o["id"], lines, model="test-model")
    assert ei.value.code == "ANSWER_INVALID"
    assert ei.value.details["rejected"][0]["id"] == "s1" and ei.value.details["rejected"][0]["line"] == 2
    o = om.load(ws, o["id"])
    assert set(o["answers"]) == {"s0"} and o["answers"]["s0"]["provider"] == "agent" and o["answers"]["s0"]["model"] == "test-model"
    om.submit(ws, o["id"], [json.dumps({"id": "s1", "description": "Wide", "shot_size": "WS", "usable": False}),
                            json.dumps({"id": "s2", "answer": {"description": "Hands", "shot_size": "CU", "usable": True}})])
    assert om.load(ws, o["id"])["status"] == "answered"


def test_answer_schema_types():
    from videocli.providers.orders import validate_answer

    sch = {"n": "number:0-1", "opt?": "string", "tags": ["string"]}
    assert validate_answer(sch, {"n": 0.5, "tags": ["a"]}) is None
    assert "expected 0..1" in validate_answer(sch, {"n": 2, "tags": []})
    assert "missing" in validate_answer(sch, {"tags": []})


# --------------------------------------------------------------------------- synthetic footage


@pytest.fixture(scope="session")
def moves(media_dir) -> Path:
    """Three 2 s shots: static, a pan, a zoom (cuts at 2 and 4 s)."""
    p = media_dir / "moves.mp4"
    if not p.exists():
        ff("-f", "lavfi", "-i", "smptehdbars=size=640x360:rate=25:d=2",
           "-f", "lavfi", "-i", "rgbtestsrc=size=1280x360:rate=25:d=2,drawgrid=w=40:h=40:t=4:c=white",
           "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:d=2",
           "-filter_complex",
           "[0]setsar=1[a];"
           "[1]trim=duration=2,setpts=PTS-STARTPTS,crop=640:360:'t*130':0,setsar=1[b];"
           "[2]scale='640*(1+t*0.4)':-2:eval=frame,crop=640:360,setsar=1[c];"
           "[a][b][c]concat=n=3:v=1:a=0[v]",
           "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p))
    return p


def test_camera_motion_classes(cli, moves):
    cli.ok("analyze", str(moves), "--only", "scenes,motion,shots")
    doc = json.loads(next(Path(".videocli/sources").glob("*/motion.json")).read_text())
    cams = [s["camera"] for s in doc["shots"]]
    assert len(cams) == 3
    assert cams[0] == "static"
    assert cams[1] in ("pan", "tracking")
    assert cams[2] == "zoom"


def test_shot_metrics_static_dark_blur():
    from videocli.analyze.vision import shot_metrics

    rng = np.random.default_rng(0)
    sharp = (rng.random((3, 90, 160)) * 255).astype(np.uint8)
    blurred = np.full((3, 90, 160), 120, dtype=np.uint8)
    dark = np.zeros((3, 90, 160), dtype=np.uint8)
    assert shot_metrics(sharp)["blur"] > shot_metrics(blurred)["blur"]
    assert shot_metrics(dark)["black_ratio"] == 1.0


def test_face_clustering_keeps_identities_apart():
    from videocli.analyze.vision import cluster, face_signature

    rng = np.random.default_rng(1)
    base_a = (rng.random((64, 64)) * 255).astype(np.uint8)
    yy, xx = np.mgrid[0:64, 0:64]
    base_b = ((np.sin(xx / 3) * 0.5 + 0.5) * 255).astype(np.uint8)
    sigs, truth = [], []
    for _i in range(6):
        noise = rng.integers(-6, 6, (64, 64))
        a = np.clip(base_a.astype(int) + noise, 0, 255).astype(np.uint8)
        b = np.clip(base_b.astype(int) + noise, 0, 255).astype(np.uint8)
        sigs += [face_signature(a), face_signature(b)]
        truth += [0, 1]
    labels = cluster(sigs)
    assert len(set(labels)) == 2
    assert all((la == labels[0]) == (t == truth[0]) for la, t in zip(labels, truth, strict=False))


def test_people_names_only_when_supplied(cli, moves):
    cli.ok("analyze", str(moves), "--only", "faces")
    d = cli.ok("people", str(moves))
    assert all(p["name"] is None for p in d["people"])
    assert d["provenance"].startswith("local:")


def test_emphasis_rises_on_louder_higher_slower_word():
    from videocli.analyze.hearing import (
        ProsodyAnalyzer,  # noqa: F401 - registry import
        pitch_hz,
    )

    sr = 16000
    t = np.arange(int(0.3 * sr)) / sr
    assert abs(pitch_hz(0.3 * np.sin(2 * np.pi * 150 * t)) - 150) < 5
    assert pitch_hz(np.zeros(4000)) is None


def test_prosody_end_to_end(cli, media_dir):
    p = media_dir / "prosody.wav"
    if not p.exists():
        # five "words": 150 Hz quiet, the third is 250 Hz, loud and long
        ff("-f", "lavfi", "-i",
           "aevalsrc='if(between(t,1.0,1.6),0.8*sin(2*PI*250*t),if(lt(mod(t,0.5),0.3)*lt(t,2.6),0.15*sin(2*PI*150*t),0))':s=16000:d=3", str(p))
    cli.ok("analyze", str(p), "--only", "audio")
    sid = next(Path(".videocli/sources").iterdir()).name
    words = [("a", 0.0, 0.3), ("b", 0.5, 0.8), ("BIG", 1.0, 1.6), ("c", 2.0, 2.3), ("d", 2.5, 2.6)]
    tr = {"schema": "videocli.transcript/v1", "key": "x", "created": "", "tool_version": "", "params": {}, "source_id": sid, "word_count": 5,
          "segments": [{"id": 0, "start": 0, "end": 2.6, "text": "a b BIG c d", "words": [{"start": s, "end": e, "word": w, "p": 1} for w, s, e in words]}]}
    Path(f".videocli/sources/{sid}/transcript.json").write_text(json.dumps(tr))
    from videocli.analyze import base
    from videocli.core.source import lookup
    from videocli.core.workspace import Workspace, resolve_root

    ws = Workspace.open(resolve_root())
    src = lookup(ws, str(p))
    ctx = base.RunContext(ws=ws, settings=__import__("videocli.core.config", fromlist=["load"]).load(None))
    ctx.keys["transcript"] = "x"
    from videocli.media import probe as probemod

    ctx.probe = probemod.load(ws, src)
    out = base.get("prosody").run(src, {"_key": "k"}, ctx)
    doc = json.loads(out.read_text())
    emph = {w["word"]: w["emphasis"] for w in doc["words"]}
    assert emph["BIG"] == max(emph.values())


def test_music_click_track_tempo_and_bars(cli, media_dir):
    p = media_dir / "clicks.wav"
    if not p.exists():
        # 120 bpm clicks with an accent every 4 beats (downbeat), 16 s
        ff("-f", "lavfi", "-i",
           "aevalsrc='(lt(mod(t,0.5),0.02))*sin(2*PI*1000*t)*if(lt(mod(t,2),0.02),1.0,0.4)':s=16000:d=16", str(p))
    d = cli.ok("music", str(p))
    assert d["tempo"] == pytest.approx(120, abs=4)
    beats = np.diff(d["beats"])
    assert np.median(beats) == pytest.approx(0.5, abs=0.03)
    downs = np.array(d["downbeats"])
    assert np.all(np.abs(((downs + 0.05) % 2.0) - 0.05) < 0.08)


def test_slides_detected(cli, media_dir):
    p = media_dir / "slides3.mp4"
    if not p.exists():
        ff("-f", "lavfi", "-i", "color=c=white:s=320x180:r=10:d=3", "-f", "lavfi", "-i", "color=c=navy:s=320x180:r=10:d=4",
           "-f", "lavfi", "-i", "color=c=darkred:s=320x180:r=10:d=3", "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0[v]",
           "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p))
    d = cli.ok("slides", str(p))
    assert d["total"] == 3
    assert [round(s["start"]) for s in d["slides"]] == [0, 3, 7]


def test_log_through_work_orders(cli, moves, tmp_path):
    cli.ok("analyze", str(moves), "--only", "scenes,motion,shots")
    code, env = cli("log", str(moves))
    assert code == 6 and env["error"]["code"] == "ORDERS_PENDING"
    oid = env["error"]["details"]["order"]
    show = cli.ok("orders", "show", oid)
    assert show["open"] == 3 and Path(show["items"][0]["images"][0]).is_file()
    ans = tmp_path / "a.jsonl"
    ans.write_text("\n".join(json.dumps({"id": it["id"], "answer": {"description": f"Colour bars, shot {i}", "shot_size": "WS", "usable": True}})
                             for i, it in enumerate(show["items"])))
    cli.ok("orders", "submit", oid, str(ans))
    d = cli.ok("log", str(moves))
    assert d["described"] == 3
    shots = cli.ok("shots", str(moves), "--where", "size=WS", "--format", "compact")
    assert shots["total"] == 3 and '"Colour bars, shot 0"' in shots["text"]
    found = cli.ok("find", str(moves), "colour bars", "--mode", "visual")
    assert found["total"] == 3 and found["matches"][0]["kind"] == "shot"
    assert json.loads(next(Path(".videocli/sources").glob("*/shots.json")).read_text())["shots"][0]["provenance"]["description"] == "agent"


def test_visual_search_without_descriptions(cli, moves):
    cli.ok("analyze", str(moves), "--only", "scenes,motion,shots")
    code, env = cli("find", str(moves), "anything", "--mode", "visual")
    assert code == 3 and "videocli log" in env["error"]["hint"]


def test_suggest_retakes_and_chapters():
    from videocli.judge.suggest import chapters, retakes

    tr = {"segments": [
        {"id": 0, "start": 0, "end": 3, "text": "So the kiln was built in seventeen twenty", "words": []},
        {"id": 1, "start": 3.5, "end": 6, "text": "Sorry. So the kiln was built in seventeen twenty.", "words": []},
        {"id": 2, "start": 70, "end": 74, "text": "Now the firing itself takes three days", "words": []}]}
    r = retakes(tr)
    assert len(r) == 1 and r[0]["keep"]["start"] == 3.5 and r[0]["drop"]["start"] == 0
    ch = chapters(tr, {"silences": [{"start": 66, "end": 69.5, "duration": 3.5}]}, None, None)
    assert ch[0]["at"] == pytest.approx(69.5) and "firing" in ch[0]["text_after"]


def test_diarize_not_installed(cli, audio_only, monkeypatch):
    from videocli.setup import profiles

    monkeypatch.setattr(profiles, "module_present", lambda m: m != "pyannote.audio")
    code, env = cli("analyze", str(audio_only), "--only", "diarize")
    assert code == 3 and env["error"]["code"] == "FEATURE_NOT_INSTALLED" and "--with diarize" in env["error"]["hint"]
