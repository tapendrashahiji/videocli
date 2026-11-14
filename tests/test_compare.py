"""compare: original and render side by side, synced on the original's clock."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from test_eyes_verify import _plan_and_render, talk_clip  # noqa: F401 - fixture
from videocli.media.probe import probe_path


def _frame(path: Path, t: float) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    pr = probe_path(path)
    assert pr.video is not None
    return np.frombuffer(raw, np.uint8).reshape(pr.video.height, pr.video.width, 3).astype(int)


def test_compare_sync_freezes_edit_during_cuts(cli, talk_clip, tmp_path):  # noqa: F811
    out = _plan_and_render(cli, talk_clip, tmp_path)
    d = cli.ok("compare", str(out), "--edl", "v1")
    assert d["mode"] == "sync" and d["audio"] == "original"
    assert d["duration"] == pytest.approx(8.0, abs=0.05)
    assert [round(c["from"]) for c in d["cuts"]] == [2, 5]  # tone gaps 2-3 and 5-6
    path = Path(d["output"])
    assert path.name == "v1-compare.mp4"
    pr = probe_path(path)
    assert pr.video is not None and pr.video.width == 640 and pr.video.height == 240
    assert pr.audio and pr.duration == pytest.approx(8.0, abs=0.1)

    cut = d["cuts"][0]
    a, b = cut["from"] + 0.15, cut["to"] - 0.15
    fa, fb = _frame(path, a), _frame(path, b)
    left, right = slice(0, 320), slice(320, 640)
    top = slice(40, 160)  # clear of labels and badge
    assert np.abs(fa[top, left] - fb[top, left]).mean() > 3  # original keeps moving
    assert np.abs(fa[top, right] - fb[top, right]).mean() < 1.5  # edit holds its frame

    # Red CUT badge at the bottom centre of the edited half only while a cut plays.
    def red_bottom(fr):
        area = fr[200:236, 400:560]
        return ((area[..., 0] > 150) & (area[..., 1] < 120) & (area[..., 2] < 120)).mean()

    assert red_bottom(fa) > 0.1
    assert red_bottom(_frame(path, 1.0)) < 0.02


def test_compare_drift_plays_both(cli, talk_clip, tmp_path):  # noqa: F811
    out = _plan_and_render(cli, talk_clip, tmp_path)
    d = cli.ok("compare", str(out), "--edl", "v1", "--drift", "-o", str(tmp_path / "d.mp4"))
    assert d["mode"] == "drift" and d["cuts"] == [] and d["audio"] == "edited"
    assert probe_path(tmp_path / "d.mp4").duration == pytest.approx(8.0, abs=0.1)


def test_compare_refuses_to_sync_speed_changes(cli, talk_clip, tmp_path):  # noqa: F811
    from videocli.core.errors import UsageError
    from videocli.core.workspace import Workspace
    from videocli.edl import io
    from videocli.judge.compare import sync_plan

    _plan_and_render(cli, talk_clip, tmp_path)
    edl = io.load(Workspace.open(), "v1")
    edl.tracks.main[0].speed = 2.0
    with pytest.raises(UsageError) as exc:
        sync_plan(edl, 8.0)
    assert exc.value.code == "COMPARE_NOT_SYNCABLE" and "--drift" in (exc.value.hint or "")


def test_compare_missing_file(cli):
    code, env = cli("compare", "nope.mp4", "--edl", "v1")
    assert code != 0 and env["error"]["code"] == "FILE_NOT_FOUND"
