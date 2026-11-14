"""Phase 10: critique, review page, notes, variants, tournament, direct loop, preference evaluation."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from conftest import ff


@pytest.fixture
def cut(cli, media_dir):
    p = media_dir / "dir_src.mp4"
    if not p.exists():
        ff("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:d=12", "-f", "lavfi", "-i", "sine=f=220:sample_rate=48000:d=12",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p))
    cli.ok("probe", str(p))
    cli.ok("plan", str(p), "-o", "cut", "--keep-only", "0-3", "--keep-only", "4-7", "--keep-only", "8-11", "--no-snap")
    return p


def _submit(cli, oid, lines, tmp_path):
    f = tmp_path / f"{oid}.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in lines))
    cli.ok("orders", "submit", oid, str(f))


def test_critique_pack_and_schema(cli, cut, tmp_path):
    code, env = cli("critique", "cut", "--fresh")
    assert code == 6
    oid = env["error"]["details"]["order"]
    show = cli.ok("orders", "show", oid)
    item = show["items"][0]
    assert "separate sub-agent" in item["question"]
    pack_dir = Path(json.loads(Path(f".videocli/orders/{oid}.json").read_text())["meta"]["pack"])
    pack = json.loads((pack_dir / "pack.json").read_text())
    blob = json.dumps(pack)
    assert "edl history" in pack["excluded"] and "reason" not in blob.replace("planner reasons", "")
    assert len(pack["strips"]) == 2 and Path(pack["preview"]).is_file()
    from videocli.providers.orders import validate_answer

    bad = {"scores": {k: 3 for k in ("story", "pacing", "shot_choice", "sound", "picture", "technical")},
           "problems": [{"category": "pacing", "evidence": "drags", "fix": "cut"}]}
    assert "missing 'at'" in validate_answer(item["answer_schema"], bad)
    good = {**bad, "problems": [{"at": 3.0, "category": "pacing", "evidence": "the second shot drags", "fix": "trim c0002 tail"}]}
    _submit(cli, oid, [{"id": item["id"], "answer": good}], tmp_path)
    d = cli.ok("critique", "cut", "--fresh")
    assert d["weighted"] == 3.0 and d["problems"][0]["at"] == 3.0 and d["fresh"] is True


def test_review_page_offline(cli, cut):
    d = cli.ok("review", "build", "cut")
    html = Path(d["page"]).read_text()
    assert not re.search(r"(src|href)=\"https?://", html) and "<script src" not in html
    assert (Path(d["page"]).parent / "preview.mp4").is_file()
    assert "Export notes.json" in html


def test_review_variants_blind(cli, cut):
    cli.ok("edl", "copy", "cut", "cut2")
    d = cli.ok("review", "build", "cut", "--variants", "cut2", "-o", "rv")
    html = Path(d["page"]).read_text()
    assert "Version A" in html and "Version B" in html and "cut2" not in html
    key = json.loads(Path(".rv-key.json").read_text())
    assert set(key["labels"].values()) == {"cut", "cut2"}


def test_notes_ingest_text_with_line_errors(cli, cut, tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("3.5 pacing high: hold longer on the hands\n1:02-1:10 shot: wrong take\nnonsense here\n")
    code, env = cli("notes", "ingest", str(f), "--edl", "cut")
    assert code == 4 and "line 3" in env["error"]["details"]["errors"][0]
    f.write_text("3.5 pacing high: hold longer on the hands\n0:05-0:06 shot: wrong take\n")
    d = cli.ok("notes", "ingest", str(f), "--edl", "cut")
    assert d["ingested"] == 2
    lst = cli.ok("notes", "list", "cut", "--open")
    assert lst["total"] == 2 and lst["notes"][1]["from"] == 5.0


def test_notes_apply_scoped_and_questions(cli, cut, tmp_path):
    notes = {"schema": "videocli.notes/v1", "edl": "cut", "notes": [
        {"at": 4.0, "category": "pacing", "priority": "high", "text": "the middle shot is too long, tighten its end"},
        {"at": 8.5, "category": "story", "text": "something feels off here"}]}
    nf = tmp_path / "notes.json"
    nf.write_text(json.dumps(notes))
    cli.ok("notes", "ingest", str(nf))
    code, env = cli("notes", "apply", "cut", "-o", "fine")
    assert code == 6
    oid = env["error"]["details"]["order"]
    _submit(cli, oid, [
        {"id": "n0001", "answer": {"action": "apply", "ops": ["videocli edl trim fine c0002 --end 6.5", "videocli edl trim fine c0001 --end 2.0"]}},
        {"id": "n0002", "answer": {"action": "question", "ops": [], "question": "What feels off: the shot or what is said?"}}], tmp_path)
    dry = cli.ok("notes", "apply", "cut", "-o", "fine", "--dry-run")
    p1 = dry["plan"][0]
    assert p1["ops"] == ["videocli edl trim fine c0002 --end 6.5"] and "outside the note" in p1["rejected"][0]["reason"]
    d = cli.ok("notes", "apply", "cut", "-o", "fine")
    statuses = {r["note"]: r["status"] for r in d["results"]}
    assert statuses == {"n0001": "applied", "n0002": "question"}
    a, b = cli.ok("edl", "show", "cut", "--clips")["clips"], cli.ok("edl", "show", "fine", "--clips")["clips"]
    assert a[0] == b[0] and a[2]["start"] == b[2]["start"]  # untouched clips
    assert b[1]["end"] == pytest.approx(6.5, abs=0.04)
    assert cli.ok("notes", "list", "cut", "--open")["total"] == 0


def test_variants_one_axis_and_shared_nodes(cli, cut):
    cli.ok("grade", "set", "cut", "--look", "--op", "saturation amount=1.0")
    d = cli.ok("variants", "cut", "--vary", "look", "-n", "2")
    names = [v["name"] for v in d["variants"]]
    base = json.loads(Path(".videocli/edits/cut.edl.json").read_text())
    for n in names:
        v = json.loads(Path(f".videocli/edits/{n}.edl.json").read_text())
        assert v["tracks"]["main"] == base["tracks"]["main"] and v["grades"] != base["grades"]
    o = cli.ok("variants", "cut", "--vary", "open", "-n", "2")
    first = json.loads(Path(f".videocli/edits/{o['variants'][0]['name']}.edl.json").read_text())
    assert first["tracks"]["main"][0]["id"] == "c0002"
    assert sorted(c["id"] for c in first["tracks"]["main"]) == sorted(c["id"] for c in base["tracks"]["main"])


def test_tournament_ranking_both_orders():
    from videocli.judge.loop import rank

    # judge prefers C > B > A consistently, whichever side it sees first
    pref = {"A": 0, "B": 1, "C": 2}
    answers = {}
    for x, y in (("A", "B"), ("A", "C"), ("B", "C")):
        for tag, (first, second) in (("ab", (x, y)), ("ba", (y, x))):
            answers[f"{x}|{y}|{tag}"] = {"answer": {"winner": "X" if pref[first] > pref[second] else "Y"}}
    table = rank(["A", "B", "C"], answers)
    assert [r["edl"] for r in table] == ["C", "B", "A"] and table[0]["wins"] == 4


def test_direct_stop_conditions():
    from videocli.judge.loop import HARD_ROUND_CAP, stop_reason

    st = {"round": 1, "rounds": [], "started_ts": 0}
    assert stop_reason({**st, "approved": True}, 3, None) == "approved"
    assert stop_reason({**st, "round": 3}, 3, None) == "round limit"
    assert stop_reason({**st, "round": HARD_ROUND_CAP}, 99, None) == "round limit"
    assert stop_reason(st, 3, 1.0) == "time budget"
    flat = [{"critic": 3.0, "audit_fails": 2}] * 3
    assert stop_reason({**st, "started_ts": 9e12, "rounds": flat}, 9, None) == "no improvement in two rounds"


def test_direct_human_gate_resumes_and_approves(cli, cut, tmp_path):
    cli.ok("edl", "copy", "cut", "film")
    code, env = cli("direct", "-o", "film", "--gate", "human")
    assert code == 6 and env["error"]["code"] == "ORDERS_PENDING"  # the critique order
    oid = env["error"]["details"]["order"]
    item = cli.ok("orders", "show", oid)["items"][0]
    _submit(cli, oid, [{"id": item["id"], "answer": {"scores": {k: 3 for k in ("story", "pacing", "shot_choice", "sound", "picture", "technical")},
                                                        "problems": [{"at": 1.0, "category": "pacing", "evidence": "slow open", "fix": "trim"}]}}], tmp_path)
    code, env = cli("direct", "-o", "film", "--gate", "human")
    assert code == 6 and env["error"]["code"] == "REVIEW_PENDING"
    state = json.loads(Path(".videocli/direct/film.json").read_text())
    assert state["rounds"][0]["critic"] == 3.0 and state["stage"] == "apply"
    nf = tmp_path / "ok.json"
    nf.write_text(json.dumps({"schema": "videocli.notes/v1", "edl": "film", "approved": True, "notes": []}))
    cli.ok("notes", "ingest", str(nf))
    d = cli.ok("direct", "-o", "film")
    assert d["status"] == "stopped: approved"


def test_win_or_tie_hand_computed():
    from eval.preference import categorise, wilson, win_or_tie

    js = [{"winner": w} for w in ("tool", "human", "human", "tie", "human")]
    r = win_or_tie(js)
    assert r["win_or_tie"] == 0.4 and r["n"] == 5
    lo, hi = wilson(2, 5)
    assert lo == pytest.approx(0.1176, abs=1e-3) and hi == pytest.approx(0.7693, abs=1e-3)
    assert r["ci95"] == [lo, hi]
    assert categorise("felt slow in the middle") == ["pacing"]


def test_preference_build_collect_report(tmp_path, media_dir):
    from eval import preference

    clip = media_dir / "pref.mp4"
    if not clip.exists():
        ff("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip))
    tasks = tmp_path / "tasks"
    for t in ("t1", "t2"):
        d = tasks / t
        d.mkdir(parents=True)
        (d / "human.mp4").write_bytes(clip.read_bytes())
        (d / "tool.mp4").write_bytes(clip.read_bytes())
        (d / "task.json").write_text(json.dumps({"human": "human.mp4", "tool": "tool.mp4", "brief": "b"}))
    pages = tmp_path / "pages"
    preference.build(tasks, pages, seed=1)
    key = json.loads((pages / ".key.json").read_text())
    tool_label = {t: next(lab for lab, who in v.items() if who == "tool") for t, v in key.items()}
    j = [{"task": "t1", "choice": tool_label["t1"], "reason": "", "judge": "a", "role": "editor"},
         {"task": "t2", "choice": "none", "reason": "", "judge": "a", "role": "editor"},
         {"task": "t2", "choice": "A" if tool_label["t2"] == "B" else "B", "reason": "too slow", "judge": "b", "role": "viewer"}]
    (tmp_path / "j.json").write_text(json.dumps(j))
    preference.collect(pages, [tmp_path / "j.json"])
    rep = preference.report(pages)
    assert rep["overall"]["win_or_tie"] == pytest.approx(2 / 3, abs=1e-3)
    assert rep["losses_by_category"] == {"pacing": 1} and rep["judge_mix"] == {"editor": 2, "viewer": 1}
