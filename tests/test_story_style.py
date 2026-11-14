"""Phase 9: style metrics, selects, radio edit, idioms, solver and audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import ff
from videocli.editorial.style import compare, cut_on_beat_rate, pace_metrics


def test_pace_metrics_known_lengths():
    m = pace_metrics([2.0, 4.0, 6.0, 8.0], [True, False, True, True], 20.0)
    assert m["asl"] == 5.0 and m["median_shot"] == 5.0 and m["max_static_hold"] == 8.0
    assert m["cuts_per_min"] == pytest.approx(9.0)


def test_cut_on_beat_rate():
    assert cut_on_beat_rate([1.0, 2.05, 3.3], [1.0, 2.0, 3.0]) == pytest.approx(2 / 3, abs=1e-3)


def test_compare_weights_too_slow_over_too_fast():
    prof = {"pace": {"asl": 4.0}, "spread": {}}
    slow = compare(prof, {"pace": {"asl": 5.5}})[0]
    fast = compare(prof, {"pace": {"asl": 2.5}})[0]
    assert slow["status"] == "fail" and slow["direction"] == "too slow"
    assert fast["status"] == "pass"  # the same deviation in the fast direction is tolerated


# --------------------------------------------------------------------------- workspace fixtures


def _source_with_transcript(cli, media_dir, name: str, segments: list[tuple[float, float, str]], dur: float = 30.0) -> str:
    p = media_dir / f"{name}.mp4"
    if not p.exists():
        ff("-f", "lavfi", "-i", f"smptehdbars=size=320x180:rate=25:d={dur}", "-f", "lavfi", "-i", f"sine=f=220:sample_rate=48000:d={dur}",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p))
    sid = cli.ok("probe", str(p))["source_id"]
    segs = []
    for i, (a, b, text) in enumerate(segments):
        ws = text.split()
        step = (b - a) / len(ws)
        segs.append({"id": i, "start": a, "end": b, "text": text,
                     "words": [{"start": round(a + k * step, 3), "end": round(a + (k + 0.8) * step, 3), "word": w, "p": 0.95} for k, w in enumerate(ws)]})
    tr = {"schema": "videocli.transcript/v1", "key": "x", "created": "", "tool_version": "", "params": {}, "source_id": sid,
          "word_count": sum(len(s["words"]) for s in segs), "segments": segs}
    Path(f".videocli/sources/{sid}/transcript.json").write_text(json.dumps(tr))
    return sid


SEGMENTS = [
    (1.0, 4.0, "The kiln is three hundred years old"),
    (5.0, 8.0, "Um the the kiln is three hundred years um old"),
    (9.0, 12.0, "The kiln is three hundred years old."),
    (14.0, 18.0, "We fire it once a year in winter"),
    (20.0, 24.0, "My grandfather taught me how to read the flame"),
]


def _story(cli, media_dir, *, duration=8.0):
    sid = _source_with_transcript(cli, media_dir, "potter", SEGMENTS)
    cli.ok("brief", "set", "--logline", "A potter keeps an old kiln alive", "--duration", str(duration), "--must-include", "read the flame")
    cli.ok("story", "init", "--archetype", "custom", "--force")
    cli.ok("story", "set", "beat1", "--intent", "The age of the kiln", "--seconds", "3")
    cli.ok("story", "set", "beat2", "--intent", "How it is fired", "--seconds", "4", "--after", "beat1")
    return sid


def _answer_order(cli, env, picks: dict[str, list[int]], tmp_path) -> None:
    oid = env["error"]["details"]["order"]
    lines = [json.dumps({"id": f"beat:{b}", "answer": {"picks": p, "why": "fits"}}) for b, p in picks.items()]
    f = tmp_path / "ans.jsonl"
    f.write_text("\n".join(lines))
    cli.ok("orders", "submit", oid, str(f))


def test_selects_prefer_clean_take_and_explain(cli, media_dir, tmp_path):
    _story(cli, media_dir)
    code, env = cli("selects")
    assert code == 6 and env["error"]["code"] == "ORDERS_PENDING"
    # the judge ranks the stumbling take (1) first, the clean take (2) second: delivery should still lift the clean one
    _answer_order(cli, env, {"beat1": [1, 2, 0], "beat2": [3]}, tmp_path)
    d = cli.ok("selects")
    b1 = d["beats"][0]["selects"]
    assert b1[0]["text"].startswith("The kiln is three hundred years old") and b1[0]["why"]
    assert all(s["why"] for b in d["beats"] for s in b["selects"])


def test_radio_edit_fits_duration_and_reports_drops(cli, media_dir, tmp_path):
    _story(cli, media_dir, duration=8.0)
    code, env = cli("selects")
    _answer_order(cli, env, {"beat1": [2], "beat2": [3]}, tmp_path)
    cli.ok("selects")
    d = cli.ok("assemble", "-o", "rough")
    # must-include ("read the flame", 4 s) is locked; 3 s + 4 s + 4 s exceeds 8 s, so the lowest-scoring unlocked select goes
    assert d["dropped"] and d["duration"] <= 8.0 * 1.05 + 0.1
    show = cli.ok("edl", "show", "rough", "--clips")
    assert any(c["start"] == pytest.approx(20.0) for c in show["clips"])
    audit = cli.ok("audit", "rough")
    assert any(r["requirement"].startswith("must-include") and r["status"] == "pass" for r in audit["brief"])


def test_pacing_trims_long_silent_hold_and_locked_survive(cli, media_dir):
    _source_with_transcript(cli, media_dir, "hold", [(1.0, 3.0, "short line here")], dur=30)
    cli.ok("plan", str(media_dir / "hold.mp4"), "-o", "ph", "--keep-only", "0-3.5", "--keep-only", "5-25", "--no-snap")
    cli.ok("edl", "lock", "ph", "c0001")
    dry = cli.ok("refine", "ph", "--passes", "pacing", "--dry-run")
    assert [p["clip"] for p in dry["proposed"]] == ["c0002"]
    d = cli.ok("refine", "ph", "--passes", "pacing")
    clips = cli.ok("edl", "show", "ph", "--clips")["clips"]
    assert clips[0]["end"] == pytest.approx(3.5, abs=0.041)  # locked clip untouched (frame-snapped)
    assert clips[1]["end"] - clips[1]["start"] <= 8.05
    assert d["applied"][0]["idiom"] == "pacing"


def test_coverage_covers_jump_cut_with_matching_broll(cli, media_dir):
    sid = _source_with_transcript(cli, media_dir, "talk_jc", [(1.0, 9.0, "Hands press the clay into the wooden mould slowly")], dur=12)
    cli.ok("plan", str(media_dir / "talk_jc.mp4"), "-o", "jc", "--keep-only", "1-4", "--keep-only", "4.5-9", "--no-snap")
    br = media_dir / "broll_jc.mp4"
    if not br.exists():
        ff("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:d=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(br))
    bsid = cli.ok("probe", str(br))["source_id"]
    shots = {"schema": "videocli.shots/v1", "key": "x", "created": "", "tool_version": "", "params": {}, "source_id": bsid,
             "shots": [{"id": "s0001", "start": 0.0, "end": 3.0, "description": "Close up of hands pressing clay into a mould", "usable": True},
                       {"id": "s0002", "start": 3.0, "end": 6.0, "description": "A car drives past", "usable": True}]}
    Path(f".videocli/sources/{bsid}/shots.json").write_text(json.dumps(shots))
    audit_before = cli.ok("audit", "jc")
    assert any(i["kind"] == "jump_cut" for i in audit_before["issues"])
    d = cli.ok("refine", "jc", "--passes", "coverage")
    assert d["applied"] and "hands" in d["applied"][0]["reason"].lower()
    edl = json.loads(Path(".videocli/edits/jc.edl.json").read_text())
    assert edl["tracks"]["overlay"][0]["source"] == bsid and edl["tracks"]["overlay"][0]["start"] == 0.0
    assert not any(i["kind"] == "jump_cut" for i in cli.ok("audit", "jc")["issues"])
    _ = sid


def test_beats_pass_moves_cut_onto_beat(cli, media_dir):
    _source_with_transcript(cli, media_dir, "music_talk", [(10.0, 12.0, "later words here")], dur=20)
    cli.ok("plan", str(media_dir / "music_talk.mp4"), "-o", "bt", "--keep-only", "0-2.08", "--keep-only", "5-8", "--no-snap")
    clicks = media_dir / "beats.wav"
    if not clicks.exists():
        ff("-f", "lavfi", "-i", "aevalsrc='(lt(mod(t,0.5),0.02))*sin(2*PI*1000*t)':s=16000:d=12", str(clicks))
    cli.ok("analyze", str(clicks), "--only", "music")
    cli.ok("edl", "audio", "bt", "--source", str(clicks), "--fit")
    d = cli.ok("refine", "bt", "--passes", "beats")
    assert d["applied"], d
    assert cli.ok("edl", "show", "bt", "--clips")["clips"][0]["end"] == pytest.approx(2.0, abs=0.045)


def test_solver_deterministic(cli, media_dir):
    _source_with_transcript(cli, media_dir, "hold", [(1.0, 3.0, "short line here")], dur=30)
    cli.ok("plan", str(media_dir / "hold.mp4"), "-o", "d1", "--keep-only", "5-25", "--no-snap")
    a = cli.ok("refine", "d1", "--passes", "pacing,punch,split-edits", "--dry-run")["proposed"]
    b = cli.ok("refine", "d1", "--passes", "pacing,punch,split-edits", "--dry-run")["proposed"]
    assert a == b


def test_audit_flags_slow_edit_and_passes_profile_built(cli, media_dir, tmp_path):
    _source_with_transcript(cli, media_dir, "hold", [(1.0, 3.0, "short line here")], dur=30)
    cli.ok("analyze", str(media_dir / "hold.mp4"), "--only", "scenes,motion")
    from videocli.core.fsutil import write_json

    prof = {"schema": "videocli.style/v1", "name": "house", "from": [], "spread": {},
            "pace": {"asl": 3.0, "median_shot": 3.0, "p90_shot": 4.0, "max_static_hold": 4.0, "cuts_per_min": 19.0}}
    write_json(Path(".videocli/project/styles/house.style.json"), prof)
    cli.ok("plan", str(media_dir / "hold.mp4"), "-o", "slow", "--keep-only", "0-20", "--no-snap")
    code, env = cli("audit", "slow", "--style", "house")
    assert code == 5 and env["error"]["code"] == "AUDIT_FAILED"
    assert any(m["direction"] == "too slow" for m in env["error"]["details"]["metrics"] if m["status"] == "fail")
    ranges = sum((["--keep-only", f"{i * 3.5}-{i * 3.5 + 3}"] for i in range(5)), [])
    cli.ok("plan", str(media_dir / "hold.mp4"), "-o", "brisk", *ranges, "--no-snap")
    rep = cli.ok("audit", "brisk", "--style", "house", "--fail-on", "error")
    assert rep["passed"]


def test_musicfit_trims_at_downbeats(cli, media_dir):
    from videocli.core.source import lookup
    from videocli.core.workspace import Workspace, resolve_root
    from videocli.sound.musicfit import fit

    p = media_dir / "song.wav"
    if not p.exists():
        ff("-f", "lavfi", "-i", "aevalsrc='(lt(mod(t,0.5),0.02))*sin(2*PI*1000*t)*if(lt(mod(t,2),0.02),1.0,0.4)':s=16000:d=24", str(p))
    ws = Workspace.open(resolve_root())
    plan = fit(ws, lookup(ws, str(p)), 13.0)
    parts = plan["parts"]
    assert len(parts) == 2
    total = (parts[0]["end"] - parts[0]["start"]) + (parts[1]["end"] - parts[1]["start"]) - 0.5
    assert total == pytest.approx(13.0, abs=2.0)  # within one bar
    assert any("downbeats" in c for c in plan["compromises"])
