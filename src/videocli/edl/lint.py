"""EDL lint rules. Each issue carries a severity and, where possible, a ready-to-run ``fix``."""

from __future__ import annotations

from bisect import bisect_right
from pathlib import Path
from typing import Any

from videocli.core.timeutil import ms
from videocli.edl.model import EDL

EPS = 0.01


def _words_index(transcript: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[float]]:
    if not transcript:
        return [], []
    words = sorted((w for s in transcript.get("segments", []) for w in s.get("words", [])), key=lambda w: w["start"])
    return words, [w["start"] for w in words]


def _word_at(words, starts, t: float) -> dict[str, Any] | None:
    i = bisect_right(starts, t) - 1
    if i >= 0 and words[i]["start"] + EPS < t < words[i]["end"] - EPS:
        return words[i]
    return None


def lint(
    edl: EDL,
    *,
    transcripts: dict[str, dict[str, Any] | None] | None = None,
    min_clip: float = 0.3,
    min_cut: float = 0.2,
    check_files: bool = True,
) -> dict[str, Any]:
    from videocli.core.source import compute_source_id

    issues: list[dict[str, Any]] = []
    transcripts = transcripts or {}
    name = edl.name
    idx = {sid: _words_index(transcripts.get(sid)) for sid in edl.sources}

    # source_changed / out_of_bounds
    for sid, s in edl.sources.items():
        if check_files:
            p = Path(s.path)
            if not p.exists():
                issues.append({"rule": "source_changed", "severity": "error", "source": sid, "message": f"{s.path} is missing"})
            elif compute_source_id(p) != sid:
                issues.append({"rule": "source_changed", "severity": "error", "source": sid, "message": f"{s.path} changed since planning",
                               "fix": f"videocli analyze {s.path}"})

    main = edl.tracks.main
    for c in main:
        dur = edl.sources[c.source].duration
        if c.start < 0 or (dur and c.end > dur + 0.05):
            issues.append({"rule": "out_of_bounds", "severity": "error", "clip": c.id, "start": c.start, "end": c.end, "source_duration": dur})
        if c.out_duration < min_clip:
            issues.append({"rule": "tiny_clip", "severity": "warn", "clip": c.id, "duration": ms(c.out_duration)})
        words, starts = idx.get(c.source, ([], []))
        if words:
            w = _word_at(words, starts, c.start)
            if w is not None:
                issues.append({"rule": "clipped_word", "severity": "error", "clip": c.id, "at": c.start, "word": w["word"],
                               "edge": "start", "fix": f"videocli edl trim {name} {c.id} --start {w['start']}"})
            w = _word_at(words, starts, c.end)
            if w is not None:
                issues.append({"rule": "clipped_word", "severity": "error", "clip": c.id, "at": c.end, "word": w["word"],
                               "edge": "end", "fix": f"videocli edl trim {name} {c.id} --end {w['end']}"})

    # tiny_cut: neighbours from the same source with a very small gap between them
    for p, c in zip(main, main[1:], strict=False):
        if p.source == c.source and 0 < c.start - p.end < min_cut:
            issues.append({"rule": "tiny_cut", "severity": "warn", "clips": [p.id, c.id], "at": p.end, "duration": ms(c.start - p.end),
                           "fix": f"videocli edl restore {name} --from {p.end} --to {c.start}"})

    # overlap: the same source moment used twice
    by_src: dict[str, list] = {}
    for c in main:
        by_src.setdefault(c.source, []).append(c)
    for clips in by_src.values():
        clips = sorted(clips, key=lambda x: x.start)
        for a, b in zip(clips, clips[1:], strict=False):
            if b.start < a.end - EPS:
                issues.append({"rule": "overlap", "severity": "warn", "clips": [a.id, b.id], "from": b.start, "to": min(a.end, b.end)})

    # orphan_anchor / anchor out of range
    clip_by_id = {c.id: c for c in main}
    for it in edl.anchored():
        c = clip_by_id.get(it.anchor)
        if c is None:
            issues.append({"rule": "orphan_anchor", "severity": "error", "item": it.id, "anchor": it.anchor,
                           "message": f"{it.id} is anchored to {it.anchor}, which no longer exists"})
        elif it.offset < -EPS or it.offset > c.out_duration + EPS:
            issues.append({"rule": "anchor_out_of_range", "severity": "warn", "item": it.id, "anchor": it.anchor, "offset": it.offset})

    # phase 6: layers, transitions, split edits, music, reframe
    tm = edl.timemap() if main else None
    spans = {sp.id: sp for sp in tm.spans} if tm else {}
    windows = []
    for ov in edl.tracks.overlay:
        sp = spans.get(ov.anchor)
        if sp is None:
            continue
        a = sp.out_start + ov.offset
        b = a + (ov.end - ov.start)
        windows.append((a, b, ov))
        if b > sp.out_end + EPS:
            issues.append({"rule": "overlay_past_clip", "severity": "warn", "item": ov.id, "anchor": ov.anchor,
                           "overrun": ms(b - sp.out_end)})
        src = edl.sources.get(ov.source)
        if src and src.duration and ov.end > src.duration + 0.05:
            issues.append({"rule": "out_of_bounds", "severity": "error", "item": ov.id})
    windows.sort(key=lambda w: w[0])
    for (_a1, b1, o1), (a2, _b2, o2) in zip(windows, windows[1:], strict=False):
        if o1.layer == o2.layer and a2 < b1 - EPS:
            issues.append({"rule": "overlapping_overlays", "severity": "error", "items": [o1.id, o2.id], "at": ms(a2),
                           "fix": f"videocli edl lint {name}  # move {o2.id} to start after {ms(b1)}s or put it on another layer"})
    ids = [c.id for c in main]
    for t in edl.transitions:
        if t.type == "cut" or t.after not in ids:
            continue
        i = ids.index(t.after)
        if i + 1 >= len(main):
            issues.append({"rule": "transition_handles", "severity": "error", "clip": t.after, "message": "transition after the last clip"})
            continue
        a, b = main[i], main[i + 1]
        if t.duration > min(a.out_duration, b.out_duration) / 2 + EPS:
            issues.append({"rule": "transition_handles", "severity": "error", "clips": [a.id, b.id], "duration": t.duration,
                           "fix": f"videocli edl transition {name} {a.id} --type {t.type} --duration {ms(min(a.out_duration, b.out_duration) / 2)}"})
    for i, c in enumerate(main):
        if c.audio.lag and i + 1 < len(main) and c.audio.lag > main[i + 1].out_duration:
            issues.append({"rule": "split_edit_overrun", "severity": "warn", "clip": c.id, "lag": c.audio.lag})
        if c.audio.lead and i > 0 and c.audio.lead > main[i - 1].out_duration:
            issues.append({"rule": "split_edit_overrun", "severity": "warn", "clip": c.id, "lead": c.audio.lead})
        if c.ramp and (c.audio.lead or c.audio.lag):
            issues.append({"rule": "ramp_sound", "severity": "warn", "clip": c.id, "message": "ramped clips are muted; split-edit sound is ignored"})
    total = tm.duration if tm else 0.0
    for it in edl.tracks.audio:
        if it.role != "music" or it.fit:
            continue
        src = edl.sources.get(it.source)
        length = ((it.end if it.end is not None else (src.duration if src else 0.0)) - it.start)
        start = it.at or 0.0
        if start + length < total - 0.5:
            issues.append({"rule": "music_short", "severity": "warn", "item": it.id, "ends_at": ms(start + length), "edit": ms(total),
                           "fix": f"videocli edl audio {name} --fit  # or re-add the music with --fit"})
    if edl.reframe is not None:
        w, h = edl.resolution
        aw, ah = (int(x) for x in edl.reframe.aspect.split(":"))
        for sid in {c.source for c in main}:
            s = edl.sources[sid]
            if s.width and s.height:
                crop_w = min(s.width, s.height * aw / ah)
                if crop_w < 0.5 * w:
                    issues.append({"rule": "reframe_low_resolution", "severity": "warn", "source": sid,
                                   "crop_width": int(crop_w), "output_width": w})

    if not main:
        issues.append({"rule": "empty", "severity": "error", "message": "The spine has no clips"})

    errors = sum(1 for i in issues if i["severity"] == "error")
    return {"name": name, "passed": errors == 0, "errors": errors, "warnings": len(issues) - errors, "issues": issues}
