"""Time parsing, frame rates, half-open range helpers and source/output mapping (SPEC §7)."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction

from videocli.core.errors import UsageError

_TIME_RE = re.compile(r"^(?:(?:(\d+):)?(\d{1,2}):)?(\d+(?:\.\d*)?|\.\d+)$")


def parse_time(text: str | float | int) -> float:
    """Parse ``SS.mmm``, ``MM:SS(.mmm)`` or ``HH:MM:SS(.mmm)`` into seconds."""
    if isinstance(text, (int, float)):
        if text < 0 or math.isnan(text):
            raise UsageError("BAD_ARGUMENT", f"Time must be non-negative: {text}")
        return float(text)
    s = str(text).strip()
    m = _TIME_RE.match(s)
    if not m:
        raise UsageError(
            "BAD_ARGUMENT",
            f"Cannot parse time '{text}'.",
            hint="Use seconds (90.5), MM:SS (1:30.5) or HH:MM:SS (0:01:30.5).",
        )
    hours, minutes, seconds = m.groups()
    total = float(seconds)
    if minutes is not None:
        if total >= 60:
            raise UsageError("BAD_ARGUMENT", f"Seconds must be below 60 in '{text}'.")
        total += int(minutes) * 60
    if hours is not None:
        if int(minutes or 0) >= 60:
            raise UsageError("BAD_ARGUMENT", f"Minutes must be below 60 in '{text}'.")
        total += int(hours) * 3600
    return round(total, 6)


def ms(t: float) -> float:
    """Round to millisecond precision, the public time resolution."""
    return round(t + 0.0, 3)


def format_time(t: float, *, width: int = 4) -> str:
    """Compact fixed-width seconds, e.g. ``0012.34`` (used by compact formats)."""
    return f"{t:0{width + 3}.2f}"


def format_hms(t: float) -> str:
    h, rem = divmod(max(t, 0.0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


# --------------------------------------------------------------------------- rates


@dataclass(frozen=True)
class Rate:
    """A rational frame rate such as ``30000/1001``."""

    value: Fraction

    @classmethod
    def parse(cls, text: str | float | int | Fraction | Rate) -> Rate:
        if isinstance(text, Rate):
            return text
        if isinstance(text, Fraction):
            return cls(text)
        if isinstance(text, (int, float)):
            if text <= 0:
                raise UsageError("BAD_ARGUMENT", f"Frame rate must be positive: {text}")
            return cls(Fraction(text).limit_denominator(1001))
        s = str(text).strip()
        try:
            if "/" in s:
                num, den = s.split("/", 1)
                frac = Fraction(int(num), int(den))
            else:
                frac = Fraction(s).limit_denominator(1001)
        except (ValueError, ZeroDivisionError) as exc:
            raise UsageError("BAD_ARGUMENT", f"Cannot parse frame rate '{text}'.") from exc
        if frac <= 0:
            raise UsageError("BAD_ARGUMENT", f"Frame rate must be positive: {text}")
        return cls(frac)

    def __str__(self) -> str:
        return f"{self.value.numerator}/{self.value.denominator}"

    @property
    def fps(self) -> float:
        return float(self.value)

    @property
    def frame_duration(self) -> float:
        return float(1 / self.value)

    def to_frame(self, seconds: float) -> int:
        """Index of the frame that is showing at ``seconds`` (nearest frame start)."""
        return int(round(Fraction(seconds).limit_denominator(10**6) * self.value))

    def to_seconds(self, frame: int) -> float:
        return float(Fraction(frame) / self.value)

    def snap(self, seconds: float) -> float:
        """Snap a time to the nearest frame boundary, at millisecond precision."""
        return ms(self.to_seconds(self.to_frame(seconds)))


# --------------------------------------------------------------------------- ranges

Range = tuple[float, float]


def _check(r: Range) -> Range:
    a, b = r
    if b < a:
        raise ValueError(f"range end before start: {r}")
    return (float(a), float(b))


def normalize(ranges: Iterable[Range]) -> list[Range]:
    """Sort and merge overlapping or touching half-open ranges; drop empty ones."""
    items = sorted(_check(r) for r in ranges if r[1] > r[0])
    out: list[list[float]] = []
    for a, b in items:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


merge = normalize


def subtract(ranges: Iterable[Range], remove: Iterable[Range]) -> list[Range]:
    """Return ``ranges`` minus ``remove``."""
    base = normalize(ranges)
    cuts = normalize(remove)
    out: list[Range] = []
    for a, b in base:
        cur = a
        for c, d in cuts:
            if d <= cur or c >= b:
                continue
            if c > cur:
                out.append((cur, c))
            cur = max(cur, d)
            if cur >= b:
                break
        if cur < b:
            out.append((cur, b))
    return out


def intersect(a_ranges: Iterable[Range], b_ranges: Iterable[Range]) -> list[Range]:
    a = normalize(a_ranges)
    b = normalize(b_ranges)
    out: list[Range] = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if lo < hi:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def complement(ranges: Iterable[Range], start: float, end: float) -> list[Range]:
    """The parts of ``[start, end)`` not covered by ``ranges``."""
    return subtract([(start, end)], ranges)


def total(ranges: Iterable[Range]) -> float:
    return sum(b - a for a, b in normalize(ranges))


def clip_range(r: Range, start: float, end: float) -> Range | None:
    a, b = max(r[0], start), min(r[1], end)
    return (a, b) if b > a else None


# --------------------------------------------------------------------------- mapping


@dataclass(frozen=True)
class Span:
    """One spine clip as seen by the time mapper."""

    id: str
    source: str
    start: float  # source time
    end: float  # source time
    speed: float = 1.0
    out_start: float = 0.0  # output time

    @property
    def out_duration(self) -> float:
        return (self.end - self.start) / self.speed

    @property
    def out_end(self) -> float:
        return self.out_start + self.out_duration


class TimeMap:
    """Maps between source times and output times for a spine of clips.

    ``overlaps`` are output-time shortenings after a clip (transitions), keyed by clip id.
    """

    def __init__(self, clips: Sequence[tuple[str, str, float, float, float]], overlaps: dict[str, float] | None = None):
        overlaps = overlaps or {}
        spans: list[Span] = []
        t = 0.0
        for cid, src, a, b, speed in clips:
            sp = Span(cid, src, a, b, speed or 1.0, t)
            spans.append(sp)
            t = sp.out_end - overlaps.get(cid, 0.0)
        self.spans = spans
        self.duration = spans[-1].out_end if spans else 0.0

    def to_output(self, source: str, t: float) -> list[float]:
        """All output times at which source time ``t`` of ``source`` plays (may be several)."""
        hits = []
        for sp in self.spans:
            if sp.source == source and sp.start <= t < sp.end:
                hits.append(ms(sp.out_start + (t - sp.start) / sp.speed))
        return hits

    def to_source(self, t: float) -> tuple[Span, float] | None:
        """The clip and source time showing at output time ``t``."""
        for sp in self.spans:
            if sp.out_start <= t < sp.out_end:
                return sp, ms(sp.start + (t - sp.out_start) * sp.speed)
        if self.spans and abs(t - self.duration) < 1e-9:
            sp = self.spans[-1]
            return sp, sp.end
        return None

    def span(self, clip_id: str) -> Span | None:
        for sp in self.spans:
            if sp.id == clip_id:
                return sp
        return None

    def map_range(self, source: str, a: float, b: float) -> list[Range]:
        """Output ranges where any part of source range ``[a, b)`` plays."""
        out: list[Range] = []
        for sp in self.spans:
            if sp.source != source:
                continue
            lo, hi = max(a, sp.start), min(b, sp.end)
            if lo < hi:
                out.append((ms(sp.out_start + (lo - sp.start) / sp.speed), ms(sp.out_start + (hi - sp.start) / sp.speed)))
        return out


def parse_duration_spec(text: str) -> float:
    """Parse ``30d``, ``12h``, ``45m``, ``90s`` or plain seconds into seconds."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhdw]?)\s*", text)
    if not m:
        raise UsageError("BAD_ARGUMENT", f"Cannot parse duration '{text}'.", hint="Use forms like 30d, 12h, 45m.")
    n = float(m.group(1))
    mult = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)]
    return n * mult


def parse_range(text: str) -> Range:
    """Parse ``A-B`` where A and B are times (``1:00-1:30`` or ``60-90``)."""
    # Split on a dash that is not part of a time. Times never contain '-', so split once.
    if "-" not in text:
        raise UsageError("BAD_ARGUMENT", f"Range '{text}' must look like A-B.")
    a, b = text.split("-", 1)
    ra, rb = parse_time(a), parse_time(b)
    if rb <= ra:
        raise UsageError("BAD_ARGUMENT", f"Range end must be after start: '{text}'.")
    return (ra, rb)
