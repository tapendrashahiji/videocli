"""Setup reports live progress: streamed installer lines and download meters."""

from __future__ import annotations

import io
import json
import sys
import time

from videocli.core import progress
from videocli.setup import live


def _json_events(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(progress, "_sink", progress.JsonSink(buf))
    return buf


def _parse(buf):
    return [json.loads(x) for x in buf.getvalue().splitlines() if x.strip()]


def test_parse_size():
    assert live.parse_size("Downloading nvidia-cudnn-cu12 (571.0MiB)") == int(571.0 * 1024**2)
    assert live.parse_size("Downloading x.whl (1.2 GB)") == int(1.2 * 1024**3)
    assert live.parse_size("Resolved 3 packages") is None


def test_run_streaming_reports_lines_and_heartbeat(monkeypatch):
    monkeypatch.setattr(live, "HEARTBEAT", 0.05)
    buf = _json_events(monkeypatch)
    script = "import time,sys\nprint('Downloading pkg (10.0MiB)', flush=True)\ntime.sleep(0.4)\nprint('Installed 1 package', flush=True)"
    with progress.step_ctx("pip:gpu"):
        code, tail = live.run_streaming([sys.executable, "-c", script])
    assert code == 0
    assert tail[-1] == "Installed 1 package"
    evs = [e for e in _parse(buf) if e["event"] == "progress"]
    assert len(evs) >= 3  # heartbeats while the command is silent
    assert any("Downloading pkg" in e.get("message", "") for e in evs)
    assert all("elapsed" in e for e in evs)


def test_run_streaming_failure_returns_tail(monkeypatch):
    monkeypatch.setattr(live, "HEARTBEAT", 0.05)
    _json_events(monkeypatch)
    code, tail = live.run_streaming([sys.executable, "-c", "import sys; print('ERROR: no such package'); sys.exit(3)"])
    assert code == 3 and tail == ["ERROR: no such package"]


def test_watch_download_reports_bytes_and_fraction(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "HEARTBEAT", 0.05)
    buf = _json_events(monkeypatch)

    def fetch():
        for i in range(5):
            (tmp_path / f"part{i}").write_bytes(b"x" * 200_000)
            time.sleep(0.06)
        return "ok"

    with progress.step_ctx("model:whisper-tiny"):
        assert live.watch_download(fetch, tmp_path, 1_000_000) == "ok"
    evs = [e for e in _parse(buf) if e["event"] == "progress"]
    assert evs[-1]["fraction"] == 1.0 and evs[-1]["bytes_done"] == 1_000_000
    mids = [e for e in evs[:-1] if e.get("bytes_done")]
    assert mids and all(e["bytes_total"] == 1_000_000 and e["fraction"] < 1 for e in mids)
    assert "MB" in mids[-1]["message"] and "/s" in mids[-1]["message"]


def test_watch_download_reraises(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "HEARTBEAT", 0.05)
    _json_events(monkeypatch)

    def boom():
        raise OSError("network down")

    try:
        live.watch_download(boom, tmp_path, None)
    except OSError as exc:
        assert "network down" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected OSError")


def test_bar_without_fraction_shows_activity():
    buf = io.StringIO()
    sink = progress.BarSink(buf)
    sink.event({"event": "progress", "step": "pip:gpu", "message": "12.0 MB · 3.0 MB/s · 0:04"})
    out = buf.getvalue()
    assert "pip:gpu …" in out and "0:04" in out and "%" not in out


def test_watch_download_counts_staging_cache(monkeypatch, tmp_path):
    """Bytes staged in a download cache (Xet) count before the model file appears."""
    monkeypatch.setattr(live, "HEARTBEAT", 0.05)
    buf = _json_events(monkeypatch)
    target, cache = tmp_path / "model", tmp_path / "xet"
    target.mkdir()
    cache.mkdir()

    def fetch():
        for i in range(4):
            (cache / f"chunk{i}").write_bytes(b"x" * 250_000)
            time.sleep(0.08)
        (target / "model.bin").write_bytes(b"x" * 1_000_000)  # final copy at the end

    with progress.step_ctx("model:x"):
        live.watch_download(fetch, target, 1_000_000, extra=[cache])
    evs = [e for e in _parse(buf) if e["event"] == "progress"]
    assert any(0 < e.get("bytes_done", 0) < 1_000_000 for e in evs[:-1])
    assert evs[-1]["bytes_done"] == 1_000_000  # not double-counted
