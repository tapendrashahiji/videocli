"""Word-anchored timing: put a graphic on a spoken phrase, optionally landing its reveal on the word's end."""

from __future__ import annotations

from typing import Any

from videocli.core.errors import InputError
from videocli.core.timeutil import ms
from videocli.edl.model import EDL

READING_CPS = 15.0  # characters per second a viewer reads comfortably
HOLD = 1.0  # seconds of the final state before the item leaves


def reading_time(text: str) -> float:
    return len(text.strip()) / READING_CPS + HOLD


def place_at_word(edl: EDL, transcripts: dict[str, Any], phrase: str, *, land: str = "start", reveal: float = 0.4) -> dict[str, Any]:
    """Anchor (clip id, offset) for a phrase spoken in the edit. ``land=end``: the reveal finishes on the last word's end."""
    from videocli.index.search import find_phrase

    tm = edl.timemap()
    for sid, tr in transcripts.items():
        if not tr:
            continue
        for hit in find_phrase(tr, phrase):
            outs = tm.to_output(sid, hit["start"])
            if not outs:
                continue
            o_start = outs[0]
            o_end_list = tm.to_output(sid, max(hit["start"], hit["end"] - 1e-3))
            o_end = (o_end_list[0] + 1e-3) if o_end_list else o_start + (hit["end"] - hit["start"])
            target = o_start if land == "start" else o_end - reveal
            target = max(0.0, target)
            hit_span = tm.to_source(target)
            if hit_span is None:
                continue
            span = hit_span[0]
            return {"anchor": span.id, "offset": ms(target - span.out_start), "word_start": ms(o_start), "word_end": ms(o_end),
                    "source": sid, "source_time": hit["start"]}
    raise InputError("PHRASE_NOT_FOUND", f"{phrase!r} is not spoken anywhere in the edit.",
                     hint="Check with: videocli find FILE \"phrase\" — words cut from the edit cannot anchor graphics.")
