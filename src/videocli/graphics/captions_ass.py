"""Animated captions as ASS subtitles, burned with libass last in the chain.

Presets: ``clean`` (sentence cues), ``bold-words`` (two or three words per cue, upper case),
``karaoke`` (the current word highlighted from word timestamps). Cues never show for less than a
minimum time and sit inside the safe area for the output aspect ratio.
"""

from __future__ import annotations

from typing import Any

from videocli.core.timeutil import ms

MIN_CUE = 0.5


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _ass_color(hexcol: str, alpha: int = 0) -> str:
    c = hexcol.lstrip("#")
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def group_words(words: list[dict[str, Any]], preset: str, max_chars: int, lines: int = 2) -> list[list[dict[str, Any]]]:
    cues: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    per = 3 if preset == "bold-words" else None
    for w in words:
        if cur:
            gap = w["start"] - cur[-1]["end"]
            text_len = len(" ".join(x["word"] for x in cur)) + 1 + len(w["word"])
            punct = cur[-1]["word"].rstrip('"\')]').endswith((".", "?", "!", ",", ";", ":"))
            if per is not None:
                brk = len(cur) >= per or gap > 0.4 or punct
            else:
                brk = text_len > max_chars * lines or gap > 0.8 or (punct and cur[-1]["word"].endswith((".", "?", "!")) and len(cur) >= 3) \
                    or w["end"] - cur[0]["start"] > 6.0
            if brk:
                cues.append(cur)
                cur = []
        cur.append(w)
    if cur:
        cues.append(cur)
    return cues


def build(words: list[dict[str, Any]], *, preset: str, size: tuple[int, int], duration: float, brand: Any,
          params: dict[str, Any] | None = None, max_chars: int = 42) -> str:
    params = params or {}
    W, H = size
    S = min(W, H)
    vertical = H > W
    font_size = int(float(params.get("size", S * (0.065 if preset == "bold-words" else 0.05))))
    # Layout: chars per line, lines per cue, top or bottom, distance from that edge (fraction of height).
    max_chars = int(params.get("max_chars", max_chars))
    n_lines = int(params.get("lines", 2))
    position = str(params.get("position", "bottom"))
    margin_v = int(H * float(params.get("margin", 0.22 if vertical else 0.08)))
    alignment = 8 if position == "top" else 2
    margin_h = int(W * max(0.05, brand.safe_margin))
    primary = params.get("color", brand.colors.get("secondary", "#ffffff"))
    accent = params.get("accent", brand.colors.get("accent", "#e63946"))
    outline = params.get("outline", "#000000")
    bold = -1 if preset in ("bold-words", "karaoke") else 0
    fontname = params.get("font", "Noto Sans")
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
    ]
    # Karaoke fills from SecondaryColour (not yet spoken) to PrimaryColour (spoken): accent marks the current word.
    prim = _ass_color(accent) if preset == "karaoke" else _ass_color(primary)
    sec = _ass_color(primary)
    head.append(f"Style: Default,{fontname},{font_size},{prim},{sec},{_ass_color(outline)},&H80000000,{bold},0,0,0,100,100,0,0,1,"
                f"{max(2, font_size // 14)},0,{alignment},{margin_h},{margin_h},{margin_v},1")
    head += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    lines = []
    cues = group_words(words, preset, max_chars, n_lines)
    for i, c in enumerate(cues):
        start = c[0]["start"]
        end = max(c[-1]["end"] + 0.15, start + MIN_CUE)
        if i + 1 < len(cues):
            end = min(end, cues[i + 1][0]["start"])
        end = min(end, duration)
        if end <= start:
            continue
        if preset == "karaoke":
            parts = []
            t = start
            for w in c:
                lead = max(0.0, w["start"] - t)
                if lead > 0.005:
                    parts.append(f"{{\\k{int(round(lead * 100))}}}")
                dur = max(1, int(round((w["end"] - max(w["start"], t)) * 100)))
                parts.append(f"{{\\k{dur}}}{_esc(w['word'])} ")
                t = w["end"]
            text = "".join(parts).rstrip()
        elif preset == "bold-words":
            text = _esc(" ".join(w["word"] for w in c).upper())
        else:
            text = _esc(" ".join(w["word"] for w in c))
        lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{text}")
    return "\n".join(head + lines) + "\n"


def cue_times(ass_text: str) -> list[tuple[float, float]]:
    out = []
    for line in ass_text.splitlines():
        if line.startswith("Dialogue:"):
            f = line.split(",", 9)

            def p(x: str) -> float:
                h, m, s = x.split(":")
                return ms(int(h) * 3600 + int(m) * 60 + float(s))
            out.append((p(f[1]), p(f[2])))
    return out
