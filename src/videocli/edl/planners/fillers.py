"""Filler words: per-language defaults, user override, word-timestamp cuts with a small pad."""

from __future__ import annotations

from typing import Any

from videocli.edl.planners.cuts import Cut

DEFAULTS: dict[str, list[str]] = {
    "en": ["um", "uh", "umm", "uhm", "erm", "er", "ah", "hmm", "mm", "mhm"],
    "es": ["eh", "em", "este", "mmm"],
    "fr": ["euh", "heu", "bah", "ben"],
    "de": ["äh", "ähm", "öh", "hm", "hmm"],
    "it": ["ehm", "eh", "uhm"],
    "pt": ["é", "hum", "ahn", "tipo"],
    "hi": ["um", "uh", "matlab"],
    "ne": ["um", "uh", "ani"],
}


def words_for(language: str | None, override: list[str]) -> list[str]:
    if override:
        return override
    lang = (language or "en").split("-")[0].lower()
    return DEFAULTS.get(lang, DEFAULTS["en"])


def plan(words: list[dict[str, Any]], override: list[str], *, language: str | None, pad: float) -> tuple[list[Cut], list[str]]:
    from videocli.index.search import norm_token, tokens

    fillers = [tokens(f) for f in words_for(language, override)]
    fillers = [f for f in fillers if f]
    norms = [norm_token(w["word"]) for w in words]
    out: list[Cut] = []
    used: set[str] = set()
    i = 0
    while i < len(words):
        matched = None
        for f in sorted(fillers, key=len, reverse=True):
            n = len(f)
            if norms[i:i + n] == f:
                matched = f
                break
        if matched is None:
            i += 1
            continue
        j = i + len(matched) - 1
        lo = words[i - 1]["end"] if i > 0 else 0.0
        hi = words[j + 1]["start"] if j + 1 < len(words) else words[j]["end"] + pad
        a = max(lo, words[i]["start"] - pad)
        b = min(hi, words[j]["end"] + pad)
        if b > a:
            out.append(Cut(a, b, "filler"))
            used.add(" ".join(matched))
        i = j + 1
    return out, sorted(used)
