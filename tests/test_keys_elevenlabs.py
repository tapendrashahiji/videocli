"""API keys typed by a person, the ElevenLabs transcript backend, and the low-confidence hint."""

from __future__ import annotations

import io
import json
import os
import stat
import urllib.error
from pathlib import Path

import pytest

from conftest import ff
from test_audio_perception import lecture_clip  # noqa: F401 - fixture
from videocli.analyze import elevenlabs
from videocli.analyze.transcript import low_confidence_hint
from videocli.setup import keys

SECRET = "sk_test_0123456789abcdef"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    return tmp_path


def test_set_key_hidden_gitignored_and_never_reported(project):
    res = keys.set_interactive("elevenlabs", reader=lambda prompt: SECRET, stdin_isatty=True)
    env = project / ".env"
    assert res["set"] and Path(res["file"]) == env and res["gitignore_updated"]
    assert f"ELEVENLABS_API_KEY={SECRET}" in env.read_text()
    assert stat.S_IMODE(os.stat(env).st_mode) == 0o600
    assert ".env" in (project / ".gitignore").read_text().splitlines()
    assert keys.get("elevenlabs") == SECRET
    st = keys.status()
    assert st[0]["set"] and st[0]["source"] == "project"
    assert SECRET not in json.dumps(st) and SECRET not in json.dumps(res)
    # second set keeps .gitignore unchanged and replaces the line
    keys.set_interactive("elevenlabs", reader=lambda p: SECRET + "x", stdin_isatty=True)
    assert env.read_text().count("ELEVENLABS_API_KEY=") == 1
    assert keys.remove("elevenlabs")["removed"] and keys.get("elevenlabs") is None


def test_env_var_wins_and_bad_keys_refused(project, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "from-environment")
    assert keys.get("elevenlabs") == "from-environment"
    assert keys.status()[0]["source"] == "environment"
    from videocli.core.errors import UsageError

    with pytest.raises(UsageError):
        keys.set_interactive("elevenlabs", reader=lambda p: "has space", stdin_isatty=True)
    with pytest.raises(UsageError):
        keys.set_interactive("nope", reader=lambda p: SECRET, stdin_isatty=True)


def test_keys_set_refuses_without_terminal(cli, project):
    code, env = cli("keys", "set", "elevenlabs")
    assert code == 2 and env["error"]["code"] == "NEEDS_TERMINAL"
    d = cli.ok("keys", "status")
    assert d["keys"][0]["set"] is False


def test_to_segments_maps_words_probability_and_splits():
    words = [
        {"text": "Hello", "type": "word", "start": 0.0, "end": 0.4, "logprob": -0.1},
        {"text": " ", "type": "spacing", "start": 0.4, "end": 0.5},
        {"text": "guys.", "type": "word", "start": 0.5, "end": 0.9, "logprob": -2.0},
        {"text": "(laughs)", "type": "audio_event", "start": 1.0, "end": 1.5},
        {"text": "Next", "type": "word", "start": 1.6, "end": 2.0},
        {"text": "part", "type": "word", "start": 3.5, "end": 3.9},
    ]
    segs = elevenlabs.to_segments(words)
    assert [s["text"] for s in segs] == ["Hello guys.", "Next", "part"]  # sentence end, then a 1.5 s gap
    assert segs[0]["words"][0]["probability"] == pytest.approx(0.905, abs=0.001)
    assert segs[1]["words"][0]["probability"] == 1.0


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "a.wav"
    ff("-f", "lavfi", "-i", "sine=f=300:sample_rate=16000:d=1", "-ac", "1", str(p))
    return p


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_elevenlabs_request_and_mapping(project, wav, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", SECRET)
    seen = {}

    def opener(req, timeout):
        seen["headers"] = dict(req.header_items())
        seen["body"] = req.data
        seen["url"] = req.full_url
        return FakeResp(json.dumps({"language_code": "nep", "language_probability": 0.9, "words": [
            {"text": "namaste", "type": "word", "start": 0.1, "end": 0.6, "logprob": -0.05}]}).encode())

    out = elevenlabs.ElevenLabs(None, opener=opener).transcribe(
        wav, model="scribe_v1", language="ne", initial_prompt=None, on_progress=lambda f: None)
    assert seen["url"] == elevenlabs.URL
    assert seen["headers"]["Xi-api-key"] == SECRET
    body = seen["body"]
    assert b'name="model_id"\r\n\r\nscribe_v1' in body and b'name="language_code"\r\n\r\nne' in body
    assert b"fLaC" in body  # audio uploaded as FLAC
    assert out["language"] == "nep" and out["segments"][0]["words"][0]["word"] == "namaste"


def test_elevenlabs_rejected_key_message_has_no_secret(project, wav, monkeypatch):
    from videocli.core.errors import RuntimeFailure

    monkeypatch.setenv("ELEVENLABS_API_KEY", SECRET)

    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"detail": {"status": "invalid_api_key"}}'))

    with pytest.raises(RuntimeFailure) as exc:
        elevenlabs.ElevenLabs(None, opener=opener).transcribe(wav, model="scribe_v1", language=None, initial_prompt=None, on_progress=lambda f: None)
    e = exc.value
    assert e.code == "API_REQUEST_FAILED" and "rejected" in e.message and "keys set elevenlabs" in (e.hint or "")
    assert SECRET not in json.dumps(e.to_dict())


def test_analyze_backend_elevenlabs_without_key(cli, lecture_clip, project):  # noqa: F811
    code, env = cli("analyze", str(lecture_clip), "--only", "transcript", "--backend", "elevenlabs")
    assert code != 0 and env["error"]["code"] == "API_KEY_MISSING"
    assert "keys set elevenlabs" in env["error"]["hint"]


def test_analyze_backend_elevenlabs_end_to_end(cli, lecture_clip, project, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ELEVENLABS_API_KEY", SECRET)
    calls = []

    def fake(self, wav, *, model, language, initial_prompt, on_progress):
        calls.append(model)
        return {"language": "en", "language_probability": 0.9, "segments": [
            {"start": 0.1, "end": 0.5, "text": "hello", "words": [{"start": 0.1, "end": 0.5, "word": "hello", "probability": 0.99}]}]}

    monkeypatch.setattr(elevenlabs.ElevenLabs, "transcribe", fake)
    cli.ok("analyze", str(lecture_clip), "--only", "transcript", "--backend", "elevenlabs")
    assert calls == ["scribe_v1"]
    doc = json.loads(next(Path(".videocli/sources").glob("*/transcript.json")).read_text())
    assert doc["backend"] == "elevenlabs" and doc["model"] == "scribe_v1"


def test_low_confidence_hint_never_suggests_smaller_model():
    assert "--model medium" in low_confidence_hint("small", "whisper")
    assert "--model large-v3" in low_confidence_hint("medium", "whisper")
    h = low_confidence_hint("large-v3", "whisper")
    assert "--model" not in h and "largest" in h and "elevenlabs" in h
    assert "largest" in low_confidence_hint("distil-large-v3", "whisper")
    assert "--model" not in low_confidence_hint("scribe_v1", "elevenlabs")
