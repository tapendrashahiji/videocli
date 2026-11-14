"""The ``titles`` baker (base tier): templates drawn with Pillow into a transparent clip.

A design lays out static elements once; each frame then applies the animation (fade, slide,
wipe, scale, typewriter) with easing. Text uses raqm shaping when available, so Devanagari and
right-to-left scripts shape correctly.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from videocli.graphics.templates import Brand, Template, font_file


def ease_out(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 1 - (1 - p) ** 3


def ease_in_out(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 4 * p**3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def hex_rgba(c: str, alpha: int = 255) -> tuple[int, int, int, int]:
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    a = int(c[6:8], 16) if len(c) == 8 else alpha
    return (r, g, b, a)


def luminance(c: str) -> float:
    r, g, b, _ = hex_rgba(c)

    def lin(v: int) -> float:
        x = v / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _font(brand: Brand, size: float, bold: bool, text: str) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_file(brand, bold, text), max(8, int(size)))


def wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if font.getlength(cand) <= width or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


@dataclass
class Layout:
    """Static elements drawn once, plus geometry used by the animation and by lint."""

    layer: Image.Image
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 of the visible content
    text: str = ""
    overflow: bool = False
    text_color: str = "#ffffff"
    extra: dict[str, Any] = field(default_factory=dict)


def _text_block(draw: ImageDraw.ImageDraw, xy: tuple[int, int], lines: list[str], font, fill, spacing: float = 1.2, anchor: str = "la") -> int:
    x, y = xy
    asc, desc = font.getmetrics()
    lh = int((asc + desc) * spacing)
    for i, ln in enumerate(lines):
        draw.text((x, y + i * lh), ln, font=font, fill=fill, anchor=anchor)
    return lh * len(lines)


def layout(t: Template, p: dict[str, Any], brand: Brand, W: int, H: int, *, chars: int | None = None) -> Layout:
    design = t.design or t.name
    S = min(W, H)
    m = int(round(brand.safe_margin * S)) if t.placement.get("safe_area", True) else 0
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    white = brand.colors.get("secondary", "#ffffff")
    accent = p.get("accent") or brand.colors.get("accent", "#e63946")

    def clip_text(s: str) -> str:
        return s if chars is None else s[:chars]

    if design in ("lower-third", "chapter-marker"):
        big = _font(brand, S * (0.055 if design == "lower-third" else 0.045), True, p.get("title", ""))
        small = _font(brand, S * 0.034, False, p.get("subtitle", ""))
        maxw = int(W * 0.6)
        title = (f"{p['number']}  " if p.get("number") else "") + p.get("title", "")
        tl = wrap(clip_text(title), big, maxw)
        sl = wrap(clip_text(p.get("subtitle", "")), small, maxw) if p.get("subtitle") else []
        tw = max([big.getlength(x) for x in tl] + [small.getlength(x) for x in sl] + [1])
        a, dsc = big.getmetrics()
        th = int((a + dsc) * 1.2) * len(tl) + (int(sum(small.getmetrics()) * 1.2) * len(sl) + int(S * 0.01) if sl else 0)
        pad = int(S * 0.02)
        bar = max(3, int(S * 0.008))
        bw, bh = int(tw + 2 * pad + bar), int(th + 2 * pad)
        if design == "lower-third":
            x0, y0 = m, H - m - bh - int(S * 0.04)
        else:
            x0, y0 = m, m
        d.rounded_rectangle((x0, y0, x0 + bw, y0 + bh), radius=int(S * 0.008), fill=hex_rgba(brand.colors.get("primary", "#111111"), 200))
        d.rectangle((x0, y0, x0 + bar, y0 + bh), fill=hex_rgba(accent))
        y = y0 + pad
        y += _text_block(d, (x0 + bar + pad, y), tl, big, hex_rgba(white))
        if sl:
            _text_block(d, (x0 + bar + pad, y + int(S * 0.01)), sl, small, hex_rgba(white, 230))
        overflow = tw > maxw + 1 or len(tl) > 2
        return Layout(img, (x0, y0, x0 + bw, y0 + bh), title + " " + p.get("subtitle", ""), overflow, white,
                      {"slide_from": -(x0 + bw)})

    if design in ("title-card", "end-card", "quote"):
        if design != "quote":
            bg = p.get("background") or brand.colors.get("primary", "#111111")
            dim = float(p.get("dim") or 0.0)
            d.rectangle((0, 0, W, H), fill=hex_rgba("#000000", int(255 * dim)) if dim > 0 else hex_rgba(bg))
        text = p.get("title") or p.get("text", "")
        big = _font(brand, S * (0.085 if design != "quote" else 0.055), design != "quote", text)
        maxw = int(W - 2 * m - S * 0.1)
        body = f"“{clip_text(text)}”" if design == "quote" else clip_text(text)
        tl = wrap(body, big, maxw)
        lh = int(sum(big.getmetrics()) * 1.2)
        sub = p.get("subtitle") or (f"— {p['author']}" if p.get("author") else "")
        small = _font(brand, S * 0.04, False, sub)
        sl = wrap(clip_text(sub), small, maxw) if sub else []
        slh = int(sum(small.getmetrics()) * 1.2)
        total = lh * len(tl) + (slh * len(sl) + int(S * 0.03) if sl else 0)
        y = (H - total) // 2
        y0 = y
        for ln in tl:
            d.text((W // 2, y), ln, font=big, fill=hex_rgba(white), anchor="ma")
            y += lh
        if sl:
            y += int(S * 0.03)
            for ln in sl:
                d.text((W // 2, y), ln, font=small, fill=hex_rgba(white, 220), anchor="ma")
                y += slh
        tw = max([big.getlength(x) for x in tl] + [1])
        box = (int((W - tw) / 2), y0, int((W + tw) / 2), y)
        return Layout(img, box, text + " " + sub, len(tl) > 4, white)

    if design == "callout":
        f = _font(brand, S * 0.04, True, p.get("text", ""))
        tl = wrap(clip_text(p.get("text", "")), f, int(W * 0.4))
        pad = int(S * 0.015)
        tw = max(f.getlength(x) for x in tl)
        lh = int(sum(f.getmetrics()) * 1.2)
        cx, cy = int(float(p.get("x", 0.5)) * W), int(float(p.get("y", 0.4)) * H)
        bw, bh = int(tw + 2 * pad), lh * len(tl) + 2 * pad
        x0 = min(max(m, cx - bw // 2), W - m - bw)
        y0 = min(max(m, cy - bh - int(S * 0.05)), H - m - bh)
        r = int(S * 0.012)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=hex_rgba(accent))
        d.line((cx, cy, x0 + bw // 2, y0 + bh), fill=hex_rgba(accent), width=max(2, int(S * 0.004)))
        d.rounded_rectangle((x0, y0, x0 + bw, y0 + bh), radius=int(S * 0.01), fill=hex_rgba(accent))
        _text_block(d, (x0 + pad, y0 + pad), tl, f, hex_rgba(white))
        return Layout(img, (x0, y0, x0 + bw, max(y0 + bh, cy + r)), p.get("text", ""), len(tl) > 3, white, {"origin": (cx, cy)})

    if design == "counter":
        f = _font(brand, S * 0.14, True, "0")
        lab = _font(brand, S * 0.04, False, p.get("label", ""))
        return Layout(img, (int(W * 0.25), int(H * 0.35), int(W * 0.75), int(H * 0.65)), p.get("label", ""), False, white,
                      {"font": f, "label_font": lab})

    if design == "progress-bar":
        h = max(2, int(float(p.get("height", 0.012)) * H))
        return Layout(img, (0, H - h, W, H), "", False, white, {"h": h, "accent": accent})

    if design == "logo-bug":
        logo = Image.open(p["logo"]).convert("RGBA")
        lh = int(S * 0.09)
        lw = int(logo.width * lh / logo.height)
        logo = logo.resize((lw, lh), Image.Resampling.LANCZOS)
        corner = p.get("corner", "tr")
        x0 = m if corner[1] == "l" else W - m - lw
        y0 = m if corner[0] == "t" else H - m - lh
        img.alpha_composite(logo, (x0, y0))
        return Layout(img, (x0, y0, x0 + lw, y0 + lh), "", False, white)

    raise ValueError(f"unknown design {design!r}")


def frame(t: Template, p: dict[str, Any], brand: Brand, W: int, H: int, time: float, duration: float,
          cache: dict[str, Any] | None = None) -> Image.Image:
    cache = cache if cache is not None else {}
    anim = p.get("animation", "fade")
    tin, tout = t.duration.in_, t.duration.out
    a_in = ease_out(time / tin) if tin > 0 else 1.0
    a_out = ease_out((duration - time) / tout) if tout > 0 else 1.0
    vis = min(a_in, a_out)
    design = t.design or t.name
    if design in ("counter", "progress-bar"):
        lay = cache.get("lay") or layout(t, p, brand, W, H)
        cache["lay"] = lay
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if design == "progress-bar":
            prog = min(1.0, max(0.0, time / max(duration, 1e-6)))
            d.rectangle((0, H - lay.extra["h"], int(W * prog), H), fill=hex_rgba(lay.extra["accent"], int(255 * vis)))
            return img
        span = max(1e-6, duration - tin - tout)
        k = ease_in_out((time - tin) / span)
        v = float(p.get("from", 0)) + (float(p["to"]) - float(p.get("from", 0))) * k
        num = f"{p.get('prefix', '')}{v:,.0f}{p.get('suffix', '')}"
        d.text((W // 2, H // 2), num, font=lay.extra["font"], fill=hex_rgba(lay.text_color, int(255 * vis)), anchor="mm")
        if p.get("label"):
            d.text((W // 2, int(H * 0.62)), p["label"], font=lay.extra["label_font"], fill=hex_rgba(lay.text_color, int(220 * vis)), anchor="ma")
        return img
    if anim == "typewriter":
        full = len(p.get("title") or p.get("text") or "")
        n = int(round(full * min(1.0, time / max(tin * 3, 1e-6))))
        key = f"tw{n}"
        if key not in cache:
            cache[key] = layout(t, p, brand, W, H, chars=n)
        lay = cache[key]
        vis = a_out
    else:
        lay = cache.get("lay") or layout(t, p, brand, W, H)
        cache["lay"] = lay
    base = lay.layer
    if anim == "slide":
        dx = int(round((1 - vis) * lay.extra.get("slide_from", -W * 0.3)))
        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        out.alpha_composite(base, (dx, 0) if dx >= 0 else (0, 0), (0, 0) if dx >= 0 else (-dx, 0))
        return _fade(out, min(1.0, a_out * 1.0) if time > tin else 1.0)
    if anim == "wipe":
        x0, y0, x1, y1 = lay.box
        reveal = x0 + int((x1 - x0) * vis)
        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if reveal > 0:
            out.paste(base.crop((0, 0, reveal, H)), (0, 0))
        return out
    if anim == "scale":
        x0, y0, x1, y1 = lay.box
        ox, oy = lay.extra.get("origin", ((x0 + x1) // 2, (y0 + y1) // 2))
        s = 0.6 + 0.4 * vis
        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        crop = base.crop((x0, y0, x1, y1))
        nw, nh = max(1, int(crop.width * s)), max(1, int(crop.height * s))
        scaled = crop.resize((nw, nh), Image.Resampling.BICUBIC)
        out.alpha_composite(scaled, (int(ox + (x0 - ox) * s), int(oy + (y0 - oy) * s)))
        return _fade(out, vis)
    return _fade(base, vis)


def _fade(img: Image.Image, a: float) -> Image.Image:
    if a >= 0.999:
        return img
    r, g, b, al = img.split()
    al = al.point(lambda v: int(v * max(0.0, a)))
    return Image.merge("RGBA", (r, g, b, al))


def frames(t: Template, p: dict[str, Any], brand: Brand, W: int, H: int, fps: float, n: int) -> Iterator[bytes]:
    cache: dict[str, Any] = {}
    duration = n / fps
    last_key = None
    last = b""
    for i in range(n):
        tt = i / fps
        steady = t.duration.in_ < tt < duration - t.duration.out and (t.design or t.name) not in ("counter", "progress-bar") \
            and p.get("animation") != "typewriter"
        if steady and last_key == "steady":
            yield last
            continue
        img = frame(t, p, brand, W, H, tt, duration, cache)
        last = img.tobytes()
        last_key = "steady" if steady else None
        yield last


def bake(t: Template, p: dict[str, Any], brand: Brand, size: tuple[int, int], rate: str, n: int, out_dir: Path) -> Path:
    from videocli.core.timeutil import Rate
    from videocli.media import ffmpeg
    from videocli.render.bakers import alpha_codec, check_contract

    W, H = size
    fps = Rate.parse(rate).fps
    out = out_dir / "graphic.mov"
    args = ["-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", rate, "-i", "pipe:0",
            *alpha_codec(n / fps), "-frames:v", str(n), ffmpeg.fpath(out)]
    ffmpeg.run(args, log_name=f"titles-{t.name}", stdin_iter=frames(t, p, brand, W, H, fps, n))
    check_contract(out, duration=n / fps, size=(W, H), fps=rate, alpha=True)
    return out
