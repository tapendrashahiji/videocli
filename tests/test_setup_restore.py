"""Optional groups survive reinstalls: remembered, reported when lost, restored by setup --restore."""

from __future__ import annotations

import pytest

from videocli.setup import profiles


@pytest.fixture
def gpu_state(monkeypatch):
    present = {"gpu": True}
    real = profiles.check_group

    def fake(name, *, deep=True):
        if name == "gpu":
            return profiles.GroupStatus("gpu", present["gpu"], [] if present["gpu"] else ["nvidia.cublas"])
        return real(name, deep=deep)

    monkeypatch.setattr(profiles, "check_group", fake)
    return present


def test_record_and_detect_lost(gpu_state):
    assert profiles.recorded_groups() == []
    profiles.record_groups(["base", "gpu"])
    assert profiles.recorded_groups() == ["gpu"]
    assert profiles.lost_groups() == []
    gpu_state["gpu"] = False  # e.g. uv tool install --force
    assert profiles.lost_groups() == ["gpu"]
    profiles.record_groups(["base"])  # a later setup does not forget it
    assert profiles.recorded_groups() == ["gpu"]


def test_reinstall_command_keeps_groups():
    cmd = profiles.reinstall_command(["gpu"])
    assert cmd.startswith("uv tool install --force ")
    assert "--with nvidia-cublas-cu12" in cmd and '--with "nvidia-cudnn-cu12>=9,<10"' in cmd
    assert profiles.reinstall_command([]).count("--with") == 0


def test_doctor_reports_lost_and_setup_restore_plans_it(cli, gpu_state):
    profiles.record_groups(["gpu"])
    gpu_state["gpu"] = False
    code, env = cli("doctor")
    assert any(w["code"] == "GROUPS_LOST" and "gpu" in w["message"] for w in env["warnings"])
    assert "videocli setup --restore" in env["data"]["fix"]
    d = cli.ok("setup", "--restore", "--dry-run")
    assert "gpu" in d["groups"]
    assert any(a["kind"] == "pip" and a["group"] == "gpu" for a in d["actions"])
    assert "reinstall_command" in d
