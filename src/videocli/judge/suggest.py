"""Suggestions with evidence: retakes, dead shots and chapters. Nothing here changes an EDL."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from videocli.core.timeutil import ms


def _norm(text: str) -> str:
    from videocli.index.search import tokens

    return " ".join(tokens(text))


def retakes(transcript: dict[str, Any] | None, threshold: float = 0.6, window: int = 3) -> list[dict[str, Any]]:
    """Nearby segments that say nearly the same thing; recommends keeping the last take."""
    if not transcript:
        return []
    segs = [s for s in transcript.get("segments", []) if len(_norm(s["text"]).split()) >= 3]
    out = []
    used: set[int] = set()
    for i, a in enumerate(segs):
        if i in used:
            continue
        for j in range(i + 1, min(len(segs), i + 1 + window)):
            b = segs[j]
            ratio = SequenceMatcher(None, _norm(a["text"]), _norm(b["text"])).ratio()
            if ratio >= threshold:
                out.append({"keep": {"start": b["start"], "end": b["end"], "text": b["text"]},
                            "drop": {"start": a["start"], "end": a["end"], "text": a["text"]},
                            "similarity": round(ratio, 3), "why": "Same line said again shortly after; the later take is usually the cleaner one."})
                used.add(i)
                break
    return out


def dead_shots(shots: dict[str, Any] | None, transcript: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not shots:
        return []
    words = [(w["start"], w["end"]) for s in (transcript or {}).get("segments", []) for w in s.get("words", [])]
    out = []
    for s in shots.get("shots", []):
        m = s.get("metrics") or {}
        speech = any(a < s["end"] and b > s["start"] for a, b in words)
        reasons = []
        if (m.get("black_ratio") or 0) > 0.9:
            reasons.append("black")
        if (m.get("motion") is not None and m["motion"] < 0.02) and not speech and s["end"] - s["start"] > 2:
            reasons.append("frozen and silent")
        if s.get("usable") is False:
            reasons.append("marked unusable")
        if reasons:
            out.append({"shot": s["id"], "start": s["start"], "end": s["end"], "reasons": reasons})
    return out


def chapters(transcript: dict[str, Any] | None, silence: dict[str, Any] | None, slides: dict[str, Any] | None,
             scenes: dict[str, Any] | None, min_gap: float = 60.0) -> list[dict[str, Any]]:
    """Candidate chapter starts from long pauses, slide changes and scene changes, scored by agreement."""
    cands: dict[float, dict[str, Any]] = {}

    def add(t: float, kind: str, weight: float) -> None:
        k = round(t, 1)
        c = cands.setdefault(k, {"at": ms(t), "evidence": [], "score": 0.0})
        c["evidence"].append(kind)
        c["score"] += weight

    for s in (silence or {}).get("silences", []):
        if s["duration"] >= 1.5:
            add(s["end"], f"pause {s['duration']:.1f}s", min(3.0, s["duration"]))
    for s in (slides or {}).get("slides", [])[1:]:
        add(s["start"], "slide change", 2.0)
    for s in (scenes or {}).get("scenes", [])[1:]:
        add(s["start"], "scene change", 0.5)
    # merge candidates within 3 s
    merged: list[dict[str, Any]] = []
    for c in sorted(cands.values(), key=lambda c: c["at"]):
        if merged and c["at"] - merged[-1]["at"] < 3.0:
            merged[-1]["evidence"] += c["evidence"]
            merged[-1]["score"] += c["score"]
        else:
            merged.append(c)
    merged.sort(key=lambda c: -c["score"])
    chosen: list[dict[str, Any]] = []
    for c in merged:
        if all(abs(c["at"] - x["at"]) >= min_gap for x in chosen):
            chosen.append(c)
    chosen.sort(key=lambda c: c["at"])
    segs = (transcript or {}).get("segments", [])
    for c in chosen:
        nxt = [s["text"] for s in segs if s["start"] >= c["at"]][:2]
        c["text_after"] = " ".join(nxt)[:200]
        c["score"] = round(c["score"], 2)
    return chosen
