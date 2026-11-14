"""Phase 4: skill, self-description, schemas, contract and evaluation metrics."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from conftest import ff

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "src" / "videocli" / "agent" / "skill" / "SKILL.md"


def test_skill_front_matter_and_length():
    text = SKILL.read_text()
    assert text.startswith("---\nname: videocli\ndescription: ")
    assert len(text.splitlines()) < 300


def test_every_command_in_skill_exists(cli):
    names = {c["name"] for c in cli.ok("commands")["commands"]}
    used = set()
    for m in re.finditer(r"`videocli ((?:[a-z][a-z-]*)(?: [a-z][a-z-]*)?)", SKILL.read_text()):
        words = m.group(1).split()
        two = " ".join(words[:2])
        used.add(two if two in names else words[0])
    groups = {n.split()[0] for n in names if " " in n}
    missing = sorted(u for u in used if u not in names and u not in groups)
    assert len(used) > 15
    assert not missing, missing


def test_commands_describes_options_and_errors(cli):
    d = cli.ok("commands", "--name", "edl cut")
    cmd = d["commands"][0]
    opts = {o["name"]: o for o in cmd["options"]}
    assert opts["--from"]["type"] == "time" and opts["--from"]["required"]
    assert cmd["writes"] and 4 in cmd["exit_codes"]
    assert "EDL_INVALID" in d["error_codes"]
    full = cli.ok("commands")
    assert full["total"] >= 25
    assert {g["name"] for g in full["global_options"]} >= {"--json", "--dry-run", "--estimate", "--workspace"}


def test_agent_guide_and_topics(cli):
    d = cli.ok("agent-guide")
    assert d["topic"] == "core" and "## Workflow" in d["text"]
    code, env = cli("agent-guide", "--topic", "nope")
    assert code == 2


def test_skill_install_idempotent(cli, tmp_path):
    first = cli.ok("skill", "install", "--dir", str(tmp_path))
    p = Path(first["path"])
    assert p == tmp_path / ".claude" / "skills" / "videocli" / "SKILL.md" and p.is_file()
    assert first["changed"] is True
    second = cli.ok("skill", "install", "--dir", str(tmp_path))
    assert second["changed"] is False and second["path"] == first["path"]
    assert "videocli-skill version=" in p.read_text()
    gen = cli.ok("skill", "install", "--target", "generic", "--dir", str(tmp_path))
    agents = Path(gen["path"])
    agents.write_text("# My project\n\n" + agents.read_text() + "\nmore notes\n")
    cli.ok("skill", "install", "--target", "generic", "--dir", str(tmp_path))
    text = agents.read_text()
    assert text.count("<!-- videocli:start -->") == 1 and text.startswith("# My project") and "more notes" in text


def test_doctor_warns_on_stale_skill(cli, tmp_path, monkeypatch):
    from videocli.agent import guide

    cli.ok("skill", "install")
    p = Path(".claude/skills/videocli/SKILL.md")
    p.write_text(p.read_text().replace(guide.__version__ if hasattr(guide, "__version__") else "0.", "0.0.0-old", 1))
    code, env = cli("doctor")
    assert env["data"]["skill"]["stale"] is True
    assert any(w["code"] == "SKILL_STALE" for w in env["warnings"]) and "videocli skill install" in env["data"]["fix"]


def test_schema_export(cli, tmp_path):
    names = cli.ok("schema")["names"]
    assert {"envelope", "edl", "probe", "verify", "command:render"} <= set(names)
    edl = cli.ok("schema", "edl")["schema"]
    assert "tracks" in edl["properties"]
    d = cli.ok("schema", "--all", "-o", str(tmp_path / "schemas"))
    assert d["total"] == len(names) and (tmp_path / "schemas" / "edl.schema.json").is_file()


# --------------------------------------------------------------------------- contract


def test_contract_envelopes_validate(cli, tmp_path, media_dir):
    """Every envelope from a full lecture workflow validates against its exported schema."""
    from videocli.agent.schemas import Envelope, ErrorEnvelope, validate_data

    clip = media_dir / "contract.mp4"
    if not clip.exists():
        ff("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:d=6", "-f", "lavfi", "-i", "sine=f=300:sample_rate=48000:d=6",
           "-filter_complex", "[1]volume='if(between(t,2,3.5),0,1)':eval=frame[a]", "-map", "0:v", "-map", "[a]",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip))
    out = tmp_path / "out.mp4"
    steps = [
        ["doctor"], ["probe", str(clip)], ["analyze", str(clip), "--skip", "transcript"], ["silences", str(clip)],
        ["plan", str(clip), "--cut-silence", "min=0.5,pad=0.1", "-o", "k1"], ["edl", "show", "k1", "--clips"],
        ["edl", "lint", "k1"], ["edl", "cut", "k1", "--from", "0.5", "--to", "1.0"], ["render", "k1", "-o", str(out)],
        ["verify", str(out), "--edl", "k1"], ["sheet", str(clip), "--every", "2"], ["edl", "show", "nope"],
    ]
    for args in steps:
        code, env = cli(*args)
        if env["ok"]:
            Envelope.model_validate(env)
            validate_data(env["command"], env["data"])
        else:
            ErrorEnvelope.model_validate(env)
            assert code != 0


# --------------------------------------------------------------------------- eval


def test_metrics_hand_computed():
    from eval.metrics import agreement, boundaries, precision_recall

    human = [(0.0, 10.0), (12.0, 20.0)]  # boundaries 10, 12
    tool = [(0.0, 10.1), (12.5, 18.0), (19.0, 20.0)]  # boundaries 10.1, 12.5, 18, 19
    assert boundaries(human, 20.0) == [10.0, 12.0]
    pr = precision_recall(tool, human, 20.0, tol=0.25)
    assert pr["matched"] == 1 and pr["precision"] == 0.25 and pr["recall"] == 0.5
    # disagreement: 10.0-10.1 (0.1), 12.0-12.5 (0.5), 18-19 (1.0) → 1.6 of 20 s
    assert agreement(tool, human, 20.0) == pytest.approx(1 - 1.6 / 20)


def test_eval_harness_end_to_end(tmp_path, media_dir):
    clip = media_dir / "evalclip.wav"
    if not clip.exists():
        ff("-f", "lavfi", "-i", "sine=f=300:sample_rate=16000:d=8", "-af", "volume='if(between(t,3,5),0,1)':eval=frame", str(clip))
    task = tmp_path / "tasks" / "t1"
    task.mkdir(parents=True)
    (task / "clip.wav").write_bytes(clip.read_bytes())
    (task / "task.json").write_text(json.dumps({
        "source": "clip.wav", "human": [[0, 3.1], [4.9, 8.0]],
        "plan_args": ["--cut-silence", "min=1.0,pad=0.1"], "analyze_args": ["--skip", "transcript"], "licence": "test",
    }))
    res = subprocess.run([sys.executable, "-m", "eval", "run", str(tmp_path / "tasks")], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    data = json.loads((tmp_path / "tasks" / "results.json").read_text())
    row = data["tasks"][0]
    assert row["precision"] == 1.0 and row["recall"] == 1.0 and row["agreement"] > 0.98


def test_skill_in_wheel(tmp_path):
    import shutil

    uv = shutil.which("uv")
    if uv:
        cmd = [uv, "build", "--wheel", "-o", str(tmp_path), str(ROOT)]
    else:
        cmd = [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(tmp_path), str(ROOT)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        pytest.skip(f"wheel build unavailable: {res.stderr[-200:]}")
    wheel = next(tmp_path.glob("videocli-*.whl"))
    names = zipfile.ZipFile(wheel).namelist()
    assert "videocli/agent/skill/SKILL.md" in names
