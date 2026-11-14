"""Phase 0: envelope, exit codes, config, workspace, source identity, cache and probe."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from videocli.core import config
from videocli.core.cache import Store
from videocli.core.errors import (
    CheckFailed,
    DependencyError,
    InputError,
    Pending,
    RuntimeFailure,
    UsageError,
)

VIDEOCLI = [sys.executable, "-m", "videocli"]


# --------------------------------------------------------------------------- envelope & exit codes


def test_success_envelope_shape(cli, av_clip):
    code, env = cli("probe", str(av_clip))
    assert code == 0
    assert set(env) == {"ok", "command", "version", "data", "artifacts", "warnings", "next"}
    assert env["command"] == "probe" and env["ok"] is True
    assert env["artifacts"][0]["kind"] == "probe"
    assert env["next"]


@pytest.mark.parametrize(
    "exc,code",
    [
        (RuntimeFailure("FFMPEG_FAILED", "x"), 1),
        (UsageError("BAD_ARGUMENT", "x"), 2),
        (DependencyError("MODEL_NOT_FOUND", "x"), 3),
        (InputError("FILE_NOT_FOUND", "x"), 4),
        (CheckFailed("VERIFY_FAILED", "x"), 5),
        (Pending("ORDERS_PENDING", "x"), 6),
        (KeyboardInterrupt(), 130),
        (ValueError("boom"), 1),
    ],
)
def test_error_classes_map_to_exit_codes(cli, capsys, exc, code):
    from videocli.cli.app import app
    from videocli.cli.registry import command

    @command(app, "_raise_test", summary="test only")
    def _raise() -> None:
        raise exc

    try:
        from videocli.cli.app import main

        capsys.readouterr()
        rc = main(["_raise_test"])
        out = capsys.readouterr().out.strip()
        assert rc == code
        if code != 130:
            env = json.loads(out)
            assert env["ok"] is False
            assert set(env["error"]) == {"code", "message", "hint", "retryable", "details"}
            if isinstance(exc, ValueError):
                assert env["error"]["code"] == "INTERNAL_ERROR"
                assert Path(env["error"]["details"]["log"]).is_file()
                assert "Traceback" not in out
    finally:
        app.registered_commands = [c for c in app.registered_commands if c.name != "_raise_test"]


def test_usage_error_wrapped_in_envelope(cli):
    code, env = cli("probe", "--no-such-option")
    assert code == 2
    assert env["error"]["code"] == "BAD_ARGUMENT"


def test_missing_file_is_input_error(cli):
    code, env = cli("probe", "missing.mp4")
    assert code == 4 and env["error"]["code"] == "FILE_NOT_FOUND"


def test_global_options_anywhere(cli, av_clip):
    code, env = cli("probe", str(av_clip), "--json", "--progress", "none")
    assert code == 0


def test_json_mode_auto_when_piped(tmp_path, av_clip):
    env = {k: v for k, v in os.environ.items() if k != "VIDEOCLI_OUTPUT"}
    res = subprocess.run([*VIDEOCLI, "probe", str(av_clip)], capture_output=True, text=True, env=env, cwd=tmp_path)
    doc = json.loads(res.stdout)
    assert doc["ok"] and doc["command"] == "probe"


def test_version(cli):
    data = cli.ok("--version")
    assert data["version"]


def test_help_startup_budget(tmp_path):
    budget = float(os.environ.get("VIDEOCLI_STARTUP_BUDGET_MS", "300"))
    best = min(_timed([*VIDEOCLI, "--help"]) for _ in range(5))
    assert best * 1000 < budget, f"--help took {best * 1000:.0f} ms"


def _timed(cmd):
    t = time.perf_counter()
    subprocess.run(cmd, capture_output=True, check=True)
    return time.perf_counter() - t


# --------------------------------------------------------------------------- config


def test_config_precedence(tmp_path):
    user = tmp_path / "user.toml"
    proj = tmp_path / "proj.toml"
    key = "silence.threshold_db"
    assert config.load(proj, user_file=user, environ={}).get(key) == -35.0
    config.write_toml(user, {key: -30.0})
    loaded = config.load(proj, user_file=user, environ={})
    assert loaded.get(key) == -30.0 and loaded.sources[key] == "user"
    config.write_toml(proj, {key: -28.0})
    loaded = config.load(proj, user_file=user, environ={})
    assert loaded.get(key) == -28.0 and loaded.sources[key] == "project"
    env = {"VIDEOCLI_SILENCE_THRESHOLD_DB": "-25"}
    loaded = config.load(proj, user_file=user, environ=env)
    assert loaded.get(key) == -25.0 and loaded.sources[key] == "env"
    loaded = config.load(proj, {key: -20.0}, user_file=user, environ=env)
    assert loaded.get(key) == -20.0 and loaded.sources[key] == "flag"


def test_config_rejects_unknown_and_invalid(tmp_path):
    with pytest.raises(UsageError):
        config.coerce_value("no.such", "1")
    with pytest.raises(UsageError):
        config.coerce_value("jobs", "zero")
    assert config.coerce_value("jobs", "3") == 3


def test_config_cli_set_and_show(cli, tmp_path):
    data = cli.ok("config", "set", "silence.threshold_db", "-40")
    assert data["value"] == -40.0
    shown = cli.ok("config", "show", "silence.threshold_db")["settings"][0]
    assert shown["value"] == -40.0 and shown["source"] == "project"


# --------------------------------------------------------------------------- source identity & workspace


def test_source_id_stable_across_rename_and_changes_with_content(tmp_path, av_clip):
    from videocli.core.source import compute_source_id

    a = tmp_path / "a.mp4"
    shutil.copy(av_clip, a)
    sid = compute_source_id(a)
    b = tmp_path / "moved" / "b.mp4"
    b.parent.mkdir()
    a.rename(b)
    assert compute_source_id(b) == sid
    with open(b, "r+b") as fh:
        fh.seek(100)
        fh.write(b"\x00\x01\x02")
    assert compute_source_id(b) != sid


def test_big_file_hash_uses_three_blocks(tmp_path):
    from videocli.core.source import compute_source_id

    p = tmp_path / "big.bin"
    p.write_bytes(b"a" * (5 * 1024 * 1024))
    sid = compute_source_id(p)
    data = bytearray(p.read_bytes())
    data[1024 * 1024 + 512 * 1024] = ord("b")  # outside the three hashed blocks
    p.write_bytes(bytes(data))
    assert compute_source_id(p) == sid
    data[len(data) // 2] = ord("c")  # inside the middle block
    p.write_bytes(bytes(data))
    assert compute_source_id(p) != sid


def test_source_changed_detected(cli, tmp_path, av_clip, video_only):
    f = tmp_path / "clip.mp4"
    shutil.copy(av_clip, f)
    first = cli.ok("probe", str(f))["source_id"]
    shutil.copy(video_only, f)
    code, env = cli("probe", str(f))
    assert code == 0
    assert env["data"]["source_id"] != first
    assert any(w["code"] == "SOURCE_CHANGED" for w in env["warnings"])
    from videocli.core.source import check_unchanged

    with pytest.raises(InputError) as ei:
        check_unchanged(first, f)
    assert ei.value.code == "SOURCE_CHANGED"


def test_probe_is_cached(cli, av_clip):
    assert cli.ok("probe", str(av_clip))["cached"] is False
    assert cli.ok("probe", str(av_clip))["cached"] is True
    assert cli.ok("probe", str(av_clip), "--force")["cached"] is False


def test_workspace_lock_is_exclusive(tmp_path):
    from videocli.core.workspace import Workspace

    ws = Workspace(tmp_path / ".videocli")
    ws.create()
    code = (
        "import sys,time; from pathlib import Path; from videocli.core.workspace import Workspace;"
        "ws=Workspace(Path(sys.argv[1]));\nwith ws.lock():\n    print('locked', flush=True); time.sleep(3)"
    )
    proc = subprocess.Popen([sys.executable, "-c", code, str(ws.root)], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "locked"
    with pytest.raises(InputError) as ei, ws.lock(timeout=0.3):
        pass
    assert ei.value.code == "WORKSPACE_LOCKED"
    proc.wait()
    with ws.lock(timeout=2):
        pass


# --------------------------------------------------------------------------- store & jobs


def test_store_build_get_prune(tmp_path):
    st = Store(tmp_path / "graph")
    assert st.get("k1") is None
    with st.build("k1") as d:
        (d / "out.bin").write_bytes(b"x" * 1000)
    assert st.has("k1") and (st.get("k1") / "out.bin").is_file()
    with pytest.raises(RuntimeError), st.build("k2") as d:
        (d / "x").write_text("partial")
        raise RuntimeError("crash")
    assert not st.has("k2") and not any(p.name.startswith(".tmp") for p in st.root.iterdir())
    with st.build("k3") as d:
        (d / "out.bin").write_bytes(b"y" * 1000)
    (st.path("k1") / ".used").write_text(str(time.time() - 100))
    removed = st.prune(max_bytes=1500)
    assert [e.key for e in removed] == ["k1"]
    assert st.has("k3")


def test_jobs_list_interrupted(cli, tmp_path):
    from videocli.core import jobs
    from videocli.core.workspace import Workspace

    ws = Workspace(tmp_path / ".videocli")
    ws.create()
    job = jobs.start(ws, "render-x", "render", ["render", "cut1", "-o", "out.mp4"])
    job.pid = 999999
    jobs.update(ws, job, done=3)
    data = cli.ok("jobs")
    assert data["total"] == 1 and data["jobs"][0]["status"] == "interrupted"
    jobs.finish(ws, job)
    assert cli.ok("jobs")["total"] == 0


def test_cache_status_and_clear(cli, av_clip):
    cli.ok("probe", str(av_clip))
    st = cli.ok("cache", "status")
    assert any(a["area"] == "sources" and a["bytes"] > 0 for a in st["areas"])
    dry = cli.ok("cache", "clear", "--dry-run")
    assert dry["dry_run"] and dry["freed_bytes"] > 0
    cli.ok("cache", "clear")
    assert cli.ok("probe", str(av_clip))["cached"] is False


# --------------------------------------------------------------------------- probe


def test_probe_av(cli, av_clip):
    d = cli.ok("probe", str(av_clip))
    assert d["schema"] == "videocli.probe/v1"
    assert d["duration"] == pytest.approx(4.0, abs=0.1)
    assert d["video"]["width"] == 320 and d["video"]["fps"] == "25/1" and not d["video"]["vfr"]
    assert d["audio"][0]["sample_rate"] == 48000
    assert d["video"]["color"]["hdr"] is None


def test_probe_audio_only(cli, audio_only):
    d = cli.ok("probe", str(audio_only))
    assert d["video"] is None and d["audio"][0]["sample_rate"] == 16000


def test_probe_video_only_warns(cli, video_only):
    code, env = cli("probe", str(video_only))
    assert env["data"]["audio"] == []
    assert any(w["code"] == "NO_AUDIO_STREAM" for w in env["warnings"])


def test_probe_rotated(cli, rotated_clip):
    code, env = cli("probe", str(rotated_clip))
    v = env["data"]["video"]
    assert v["rotation"] in (90, 270)
    assert (v["display_width"], v["display_height"]) == (120, 160)
    assert any(w["code"] == "ROTATED_SOURCE" for w in env["warnings"])


def test_probe_10bit(cli, tenbit_clip):
    v = cli.ok("probe", str(tenbit_clip))["video"]
    assert v["bit_depth"] == 10 and v["pix_fmt"] == "yuv420p10le"


def test_probe_hlg(cli, hlg_clip):
    code, env = cli("probe", str(hlg_clip))
    col = env["data"]["video"]["color"]
    assert col["hdr"] == "hlg" and col["primaries"] == "bt2020"
    assert any(w["code"] == "HDR_SOURCE" for w in env["warnings"])


def test_probe_not_media(cli, tmp_path):
    p = tmp_path / "notes.mp4"
    p.write_text("not a video")
    code, env = cli("probe", str(p))
    assert code == 4 and env["error"]["code"] == "NOT_A_MEDIA_FILE"


@pytest.mark.parametrize("name", ["with space.mp4", "has:colon.mp4", "-leading-dash.mp4"])
def test_awkward_paths(cli, tmp_path, av_clip, name):
    if sys.platform == "win32" and ":" in name:
        pytest.skip("':' is not allowed in Windows file names")
    p = tmp_path / name
    shutil.copy(av_clip, p)
    # A name with a leading dash is passed after "--", as with any Unix tool; ffmpeg still
    # sees it through the file: prefix, never as an option.
    code, env = cli("probe", "--", name) if name.startswith("-") else cli("probe", str(p))
    assert code == 0, env
    assert env["data"]["duration"] > 3


# --------------------------------------------------------------------------- ffmpeg wrapper


def test_ffmpeg_failure_has_last_line_and_log(tmp_path):
    from videocli.media import ffmpeg

    with pytest.raises(RuntimeFailure) as ei:
        ffmpeg.run(["-f", "lavfi", "-i", "nosuchfilter_xyz", "-f", "null", "-"], log_name="t")
    err = ei.value
    assert err.code == "FFMPEG_FAILED"
    assert "Filter not found" in err.message or "No such filter" in err.message
    log = Path(err.details["log"])
    assert log.is_file() and "nosuchfilter_xyz" in log.read_text()


def test_ffmpeg_rejects_string_args():
    from videocli.media import ffmpeg

    with pytest.raises(TypeError):
        ffmpeg.run("-version", log_name="x")  # type: ignore[arg-type]


def test_ffmpeg_not_found(cli, monkeypatch, av_clip):
    monkeypatch.setenv("VIDEOCLI_FFPROBE", "/nonexistent/ffprobe")
    code, env = cli("probe", str(av_clip))
    assert code == 3 and env["error"]["code"] == "FFMPEG_NOT_FOUND"


def test_parse_version():
    from videocli.media.ffmpeg import parse_version

    assert parse_version("ffmpeg version 6.1.1-3ubuntu5 Copyright") == (6, 1, 1)
    assert parse_version("ffmpeg version n7.1 Copyright") == (7, 1)
    assert parse_version("ffmpeg version N-118000-gabc Copyright")[0] == 999


def test_capabilities_detects_base_features():
    from videocli.media import ffmpeg

    caps = ffmpeg.capabilities()
    assert "silencedetect" in caps.filters and "libx264" in caps.encoders and "concat" in caps.demuxers


# --------------------------------------------------------------------------- setup & doctor


def test_profiles_resolve():
    from videocli.setup import profiles

    assert profiles.resolve("lecture") == ["base"]
    assert profiles.resolve("finishing") == ["base", "ocio", "html", "otio"]
    assert set(profiles.resolve("artistic")) >= {"perception", "music", "diarize", "ocio"}
    assert profiles.resolve(None, ["otio"]) == ["base", "otio"]
    with pytest.raises(UsageError):
        profiles.resolve("nope")


def test_missing_group_hint(monkeypatch):
    from videocli.setup import profiles

    monkeypatch.setattr(profiles, "module_present", lambda m: False)
    with pytest.raises(DependencyError) as ei:
        profiles.require_group("otio", "Editor export")
    assert ei.value.code == "FEATURE_NOT_INSTALLED"
    assert ei.value.hint == "Run: videocli setup --with otio"


def test_doctor_shape(cli):
    d = cli.ok("doctor")
    for key in ("python", "platform", "ffmpeg", "ffprobe", "models", "groups", "licences", "gpu", "ready", "fix"):
        assert key in d
    assert d["ffmpeg"]["ok"] is True
    if not d["ready"]:
        assert d["fix"]


def test_setup_offline_check(cli):
    d = cli.ok("setup", "--profile", "lecture", "--offline-check")
    assert d["groups"] == ["base"] and d["offline_check"] is True
