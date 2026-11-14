"""Build ``index.json`` (``videocli.index/v1``): a summary plus pointers to every artifact of a source."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from videocli import __version__
from videocli.core.cache import read_artifact
from videocli.core.fsutil import write_json
from videocli.core.source import Source
from videocli.core.workspace import Workspace, now_iso

SCHEMA = "videocli.index/v1"
KNOWN = ("probe", "audio", "silence", "loudness", "transcript", "scenes", "shots", "motion", "faces", "prosody",
         "events", "music", "embed", "slides", "diarize", "color")


def build(ws: Workspace, src: Source) -> Path:
    d = ws.source_dir(src.id)
    artifacts: dict[str, Any] = {}
    docs: dict[str, dict[str, Any]] = {}
    for name in KNOWN:
        p = d / f"{name}.json"
        doc = read_artifact(p)
        if not doc:
            continue
        docs[name] = doc
        artifacts[name] = {"path": f"{name}.json", "schema": doc.get("schema"), "key": doc.get("key"), "created": doc.get("created")}
        if doc.get("file"):
            artifacts[name]["file"] = doc["file"]

    probe = docs.get("probe", {})
    summary: dict[str, Any] = {
        "duration": probe.get("duration"),
        "has_video": probe.get("video") is not None,
        "has_audio": bool(probe.get("audio")),
    }
    if probe.get("video"):
        v = probe["video"]
        summary["video"] = f"{v.get('display_width')}x{v.get('display_height')} @ {v.get('fps')}"
    if "silence" in docs:
        s = docs["silence"]
        summary.update({
            "speech_seconds": s.get("speech_seconds"),
            "silence_seconds": s.get("silence_seconds"),
            "silence_count": len(s.get("silences", [])),
            "longest_silence": max((x["duration"] for x in s.get("silences", [])), default=0.0),
        })
    if "transcript" in docs:
        t = docs["transcript"]
        summary.update({
            "word_count": t.get("word_count"),
            "segment_count": len(t.get("segments", [])),
            "language": t.get("language"),
            "low_confidence_share": t.get("low_confidence_share"),
        })
    if "loudness" in docs:
        lo = docs["loudness"]
        summary.update({
            "integrated_lufs": lo.get("integrated_lufs"),
            "true_peak_db": lo.get("true_peak_db"),
            "lra_lu": lo.get("lra_lu"),
            "noise_floor_lufs": lo.get("noise_floor_lufs"),
        })
    if "scenes" in docs:
        summary["scene_count"] = len(docs["scenes"].get("scenes", []))
    out = d / "index.json"
    write_json(out, {
        "schema": SCHEMA,
        "source_id": src.id,
        "path": str(src.path),
        "created": now_iso(),
        "tool_version": __version__,
        "summary": summary,
        "artifacts": artifacts,
    })
    return out


def load(ws: Workspace, src: Source) -> dict[str, Any] | None:
    return read_artifact(ws.source_dir(src.id) / "index.json")
