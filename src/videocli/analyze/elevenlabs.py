"""ElevenLabs speech-to-text as a transcript backend (opt-in; uploads the audio).

Chosen with ``analyze --backend elevenlabs`` or ``transcript.backend = elevenlabs``. The
key comes from :mod:`videocli.setup.keys` and goes only into the request header.
"""

from __future__ import annotations

import json
import math
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from videocli.core import progress
from videocli.core.errors import DependencyError, RuntimeFailure

URL = "https://api.elevenlabs.io/v1/speech-to-text"
TIMEOUT = 900
SEGMENT_GAP = 1.0  # seconds of silence that start a new segment
SEGMENT_MAX = 15.0


def multipart(fields: dict[str, str], file_field: str, filename: str, data: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = "videocli-" + uuid.uuid4().hex
    parts: list[bytes] = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode() + data + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def to_segments(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ElevenLabs words (with spacing and audio-event items) → our segments of words."""
    segs: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []

    def flush() -> None:
        if cur:
            segs.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": " ".join(w["word"] for w in cur), "words": list(cur)})
            cur.clear()

    for w in words:
        if w.get("type", "word") != "word":
            continue
        text = str(w.get("text", "")).strip()
        if not text:
            continue
        start, end = float(w.get("start", 0.0)), float(w.get("end", 0.0))
        lp = w.get("logprob")
        prob = math.exp(float(lp)) if isinstance(lp, (int, float)) else 1.0
        if cur and (start - cur[-1]["end"] > SEGMENT_GAP or end - cur[0]["start"] > SEGMENT_MAX):
            flush()
        cur.append({"start": start, "end": max(end, start), "word": text, "probability": max(0.0, min(1.0, prob))})
        if text.endswith((".", "?", "!", "।")):
            flush()
    flush()
    return segs


def _encode_flac(wav: Path, out: Path) -> None:
    from videocli.media import ffmpeg

    ffmpeg.run(["-y", "-i", str(wav), "-c:a", "flac", str(out)], log_name="elevenlabs-encode")


class ElevenLabs:
    def __init__(self, settings: Any, opener=None):
        self.settings = settings
        self.opener = opener or urllib.request.urlopen

    def transcribe(self, wav, *, model, language, initial_prompt, on_progress):
        from videocli.setup import keys

        key = keys.get("elevenlabs")
        if not key:
            raise DependencyError("API_KEY_MISSING", "ElevenLabs transcription needs an API key.",
                                  hint="A person runs (the key is typed, never shown): videocli keys set elevenlabs")
        with tempfile.TemporaryDirectory(prefix="videocli-el-") as tmp:
            flac = Path(tmp) / "audio.flac"
            _encode_flac(Path(wav), flac)
            data = flac.read_bytes()
        fields = {"model_id": model, "timestamps_granularity": "word", "tag_audio_events": "false", "diarize": "false"}
        if language:
            fields["language_code"] = language
        body, ctype = multipart(fields, "file", "audio.flac", data, "audio/flac")
        req = urllib.request.Request(URL, data=body, method="POST", headers={
            "xi-api-key": key, "Content-Type": ctype, "Accept": "application/json", "User-Agent": "videocli",
        })
        progress.update(None, f"uploading {len(data) / 1024**2:.1f} MB to ElevenLabs and waiting for the transcript")
        try:
            with self.opener(req, timeout=TIMEOUT) as resp:
                doc = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8", "replace")).get("detail", "")
                if isinstance(detail, dict):
                    detail = detail.get("message") or detail.get("status") or ""
            except Exception:
                pass
            why = {401: "the API key was rejected", 403: "the key lacks speech-to-text permission",
                   422: "the request was rejected", 429: "rate limit or quota reached"}.get(exc.code, f"HTTP {exc.code}")
            hint = ("Enter the key again: videocli keys set elevenlabs" if exc.code in (401, 403)
                    else "Check the language code and model (transcript.elevenlabs_model)." if exc.code == 422
                    else "Wait and retry, or check your ElevenLabs plan." if exc.code == 429
                    else "Retry later, or use the local backend: --backend whisper")
            raise RuntimeFailure("API_REQUEST_FAILED", f"ElevenLabs transcription failed: {why}{(': ' + str(detail)) if detail else ''}.",
                                 hint=hint, retryable=exc.code in (429, 500, 502, 503, 504)) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeFailure("API_REQUEST_FAILED", f"Could not reach ElevenLabs: {getattr(exc, 'reason', exc)}.",
                                 hint="Check the network (api.elevenlabs.io must be reachable), or use --backend whisper.",
                                 retryable=True) from None
        on_progress(1.0)
        return {
            "language": doc.get("language_code"),
            "language_probability": float(doc.get("language_probability") or 0.0),
            "segments": to_segments(doc.get("words") or []),
            "device": "elevenlabs",
        }
