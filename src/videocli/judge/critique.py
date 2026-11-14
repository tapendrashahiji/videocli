"""Independent critique (phase 10): a pack without the builder's reasoning, a rubric, and a work order.

A critic is a provider that did not build the edit and does not see how it was built. The pack
holds a low-resolution preview, strips around every cut, a cut sheet, the compact transcript on
output time and the audit report — never the EDL history or the planners' reasons.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from videocli.core.fsutil import rel, write_json
from videocli.core.timeutil import format_time
from videocli.core.workspace import Workspace, now_iso
from videocli.edl.model import EDL

SCHEMA = "videocli.critique/v1"
RUBRIC = {"story": 3.0, "pacing": 3.0, "shot_choice": 3.0, "sound": 2.0, "picture": 2.0, "technical": 1.0}
CATEGORIES = "story|pacing|shot|sound|colour|graphics|technical"
ANSWER_SCHEMA = {
    "scores": {k: "number:1-5" for k in RUBRIC},
    "problems": [{"at": "number", "category": CATEGORIES, "evidence": "string", "fix": "string"}],
}
BRIEF = ("You are an independent editor reviewing a cut you did not make. Find problems; do not praise. "
         "Judge story, pacing and shot choice first. Every problem needs a timecode (seconds into the preview), "
         "a category, the evidence you saw or heard, and a concrete fix. Score each rubric dimension 1-5.")


def output_transcript(ws: Workspace, edl: EDL) -> str:
    from videocli.core.cache import read_artifact
    from videocli.render.captions import group_cues, map_words

    words = map_words(edl, {sid: read_artifact(ws.source_dir(sid) / "transcript.json") for sid in edl.sources})
    cues = group_cues(words, max_chars=60, max_lines=1, duration=edl.output_duration())
    return "\n".join(f"[{format_time(a)}-{format_time(b)}] {t}" for a, b, t in cues)


def build_pack(ws: Workspace, edl: EDL, settings: Any, *, refs: list[str] | None = None, audit_report: dict[str, Any] | None = None) -> dict[str, Any]:
    from videocli.media.audio import decode_pcm
    from videocli.media.frames import extract_many
    from videocli.media.sheets import strip
    from videocli.render.run import render

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = ws.dir(f"reports/critique/{edl.name}-{stamp}")
    preview = d / "preview.mp4"
    render(ws, edl, preview, preset_name="preview", settings=settings, jobs=int(settings.jobs))
    import numpy as np

    tm = edl.timemap()
    strips = []
    for k, sp in enumerate(tm.spans[:-1], 1):
        a, b = max(0.0, sp.out_end - 1.0), min(tm.duration, sp.out_end + 1.0)
        times = [round(a + (b - a) * (i + 0.5) / 6, 3) for i in range(6)]
        frames = list(zip(times, extract_many(preview, times, d / "frames", width=200), strict=False))
        pcm = decode_pcm(preview, start=a, duration=b - a, rate=8000)
        out = d / f"cut-{k:03d}.jpg"
        strip(frames, np.asarray(pcm, dtype=np.float32), 8000, a, b, out, width=1200)
        strips.append({"cut": k, "at": round(sp.out_end, 3), "path": rel(out)})
    transcript = output_transcript(ws, edl)
    (d / "transcript.txt").write_text(transcript + "\n", encoding="utf-8")
    pack = {"edl": edl.name, "preview": rel(preview), "duration": round(tm.duration, 3), "strips": strips, "transcript": rel(d / "transcript.txt"),
            "references": refs or [], "audit": {k: audit_report[k] for k in ("metrics", "issues", "brief") if k in audit_report} if audit_report else None,
            "excluded": ["edl history", "planner reasons", "builder notes"]}
    write_json(d / "pack.json", pack)
    return {**pack, "dir": rel(d)}


def make_order(ws: Workspace, pack: dict[str, Any], *, fresh: bool, provider: str) -> dict[str, Any]:
    from videocli.providers import orders as om

    images = [s["path"] for s in pack["strips"]]
    item = {"id": f"critique:{pack['edl']}:{Path(pack['dir']).name}",
            "images": images, "video": pack["preview"], "transcript": pack["transcript"],
            "question": BRIEF + (" Use a separate sub-agent with no access to how this edit was built." if fresh and provider == "agent" else ""),
            "answer_schema": ANSWER_SCHEMA}
    return om.create(ws, "critique", [item], meta={"edl": pack["edl"], "pack": pack["dir"], "fresh": fresh}, brief=BRIEF)


def score(scores: dict[str, float]) -> float:
    total = sum(RUBRIC.values())
    return round(sum(scores.get(k, 0) * w for k, w in RUBRIC.items()) / total, 3)


def store(ws: Workspace, edl: EDL, order: dict[str, Any], pack: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    (ans,) = order["answers"].values()
    a = ans["answer"]
    problems = sorted(a["problems"], key=lambda p: ({"story": 0, "pacing": 1, "shot": 2}.get(p["category"], 3), p["at"]))
    doc = {"schema": SCHEMA, "edl": edl.name, "created": now_iso(), "revision": len(edl.history), "scores": a["scores"],
           "weighted": score(a["scores"]), "problems": problems, "provider": ans["provider"], "model": ans.get("model"),
           "fresh": order["meta"].get("fresh", False), "pack": pack["dir"]}
    p = ws.dir("reports") / f"critique-{edl.name}-r{len(edl.history)}.json"
    write_json(p, doc)
    return doc, p


def load_latest(ws: Workspace, name: str) -> dict[str, Any] | None:
    files = sorted((ws.root / "reports").glob(f"critique-{name}-r*.json"), key=lambda p: p.stat().st_mtime)
    return json.loads(files[-1].read_text()) if files else None
