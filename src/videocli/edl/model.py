"""``videocli.edl/v1`` (SPEC §8.5): the whole multi-track schema, validated from the first release.

Phase 2 renders ``tracks.main`` only. Overlays, graphics, audio items, transitions and grades
validate now and become renderable in their phase.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA = "videocli.edl/v1"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
TIME_TOL = 0.05  # seconds of slack against probe durations


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=False)


class SourceRef(_Base):
    path: str
    duration: float = Field(ge=0)
    fps: str | None = None
    has_video: bool = True
    has_audio: bool = True
    input_transform: str | None = None


class ClipAudio(_Base):
    gain_db: float = 0.0
    lead: float = Field(default=0.0, ge=0)
    lag: float = Field(default=0.0, ge=0)
    stream: int = Field(default=0, ge=0)


class Clip(_Base):
    id: str
    source: str
    start: float = Field(ge=0)
    end: float
    speed: float = Field(default=1.0, gt=0, le=100)
    audio: ClipAudio = Field(default_factory=ClipAudio)
    grade: str | None = None
    reason: str | None = None
    locked: bool = False
    ramp: list[tuple[float, float]] | None = Field(default=None, description="Speed ramp: (position 0-1 in the clip, rate) points")
    freeze: float = Field(default=0.0, ge=0, le=60, description="Hold the last frame this many seconds")
    stabilize: float | None = Field(default=None, ge=0, le=1, description="Stabilisation strength (baked)")

    @model_validator(mode="after")
    def _order(self) -> Clip:
        if not self.end > self.start:
            raise ValueError(f"clip {self.id}: end ({self.end}) must be after start ({self.start})")
        if self.ramp is not None:
            pts = self.ramp
            if len(pts) < 2 or pts[0][0] != 0.0 or pts[-1][0] != 1.0:
                raise ValueError(f"clip {self.id}: a ramp needs points from position 0 to 1")
            if any(b[0] <= a[0] for a, b in zip(pts, pts[1:])):
                raise ValueError(f"clip {self.id}: ramp positions must increase")
            if any(not (0.05 <= r <= 20) for _, r in pts):
                raise ValueError(f"clip {self.id}: ramp rates must be between 0.05 and 20")
        return self

    def pieces(self, n: int = 16) -> list[tuple[float, float, float]]:
        """Constant-speed pieces (source start, source end, speed). A ramp becomes ``n`` pieces."""
        if not self.ramp:
            return [(self.start, self.end, self.speed)]
        out = []
        dur = self.end - self.start
        for k in range(n):
            p0, p1 = k / n, (k + 1) / n
            pm = (p0 + p1) / 2
            rate = _interp(self.ramp, pm)
            out.append((self.start + p0 * dur, self.start + p1 * dur, rate))
        return out

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def out_duration(self) -> float:
        return sum((b - a) / sp for a, b, sp in self.pieces()) + self.freeze


def _interp(points: list[tuple[float, float]], p: float) -> float:
    for (p0, r0), (p1, r1) in zip(points, points[1:]):
        if p0 <= p <= p1:
            return r0 + (r1 - r0) * (p - p0) / (p1 - p0)
    return points[-1][1]


class Overlay(_Base):
    id: str
    source: str
    start: float = Field(ge=0)
    end: float
    anchor: str
    offset: float = 0.0
    mode: Literal["cover", "pip"] = "cover"
    pos: str | None = None
    scale: float | None = Field(default=None, gt=0, le=1)
    opacity: float = Field(default=1.0, ge=0, le=1)
    mute: bool = True
    grade: str | None = None
    layer: int = 0
    locked: bool = False

    @model_validator(mode="after")
    def _order(self) -> Overlay:
        if not self.end > self.start:
            raise ValueError(f"overlay {self.id}: end must be after start")
        return self


class Graphic(_Base):
    id: str
    baker: str = "titles"
    template: str
    params: dict[str, Any] = Field(default_factory=dict)
    anchor: str
    offset: float = 0.0
    duration: float = Field(gt=0)
    layer: int = 0
    locked: bool = False


class AudioItem(_Base):
    id: str
    source: str
    role: Literal["music", "sfx", "tone"] = "music"
    start: float = Field(default=0.0, ge=0)
    end: float | None = None
    at: float | None = Field(default=None, ge=0)
    anchor: str | None = None
    offset: float = 0.0
    gain_db: float = 0.0
    duck_db: float | None = None
    fade: float = Field(default=0.0, ge=0)
    fit: bool = False
    locked: bool = False

    @model_validator(mode="after")
    def _placed(self) -> AudioItem:
        if self.at is None and self.anchor is None:
            raise ValueError(f"audio item {self.id}: needs 'at' (output time) or 'anchor'")
        if self.end is not None and not self.end > self.start:
            raise ValueError(f"audio item {self.id}: end must be after start")
        return self


class Tracks(_Base):
    main: list[Clip] = Field(default_factory=list)
    overlay: list[Overlay] = Field(default_factory=list)
    graphics: list[Graphic] = Field(default_factory=list)
    audio: list[AudioItem] = Field(default_factory=list)


class Transition(_Base):
    after: str
    type: Literal["cut", "dissolve", "fade", "dip"] = "dissolve"
    duration: float = Field(default=0.5, gt=0, le=10)
    color: str | None = None


class Grade(_Base):
    ops: list[dict[str, Any]] = Field(default_factory=list)


class ColorSpec(_Base):
    working: str = "rec709"
    output: str = "rec709"


class Captions(_Base):
    mode: Literal["none", "burn", "sidecar"] = "none"
    style: str = "default"
    max_chars: int = Field(default=42, ge=10, le=120)
    max_lines: int = Field(default=2, ge=1, le=4)


class Mix(_Base):
    target_lufs: float = Field(default=-14.0, le=0)
    true_peak_db: float = Field(default=-1.0, le=0)
    fade_ms: int = Field(default=15, ge=0, le=500)
    normalize: bool = True


class Marker(_Base):
    at: float = Field(ge=0)
    kind: str = "note"
    text: str = ""


class Output(_Base):
    preset: str = "source"
    container: Literal["mp4", "mov", "mkv"] = "mp4"
    vcodec: Literal["h264"] = "h264"
    crf: int = Field(default=18, ge=0, le=51)
    acodec: Literal["aac"] = "aac"
    audio_bitrate: str = "192k"


class Reframe(_Base):
    aspect: Literal["16:9", "9:16", "1:1", "4:5", "4:3"] = "9:16"
    mode: Literal["center", "subject"] = "center"
    crops: dict[str, float] = Field(default_factory=dict, description="Per-clip horizontal crop centre 0-1 (subject mode)")


class Sound(_Base):
    denoise: float = Field(default=0.0, ge=0, le=1)
    level: bool = False
    compress: bool = False
    highpass: float | None = Field(default=None, ge=20, le=400)
    room_tone: str | None = Field(default=None, description="auto, or a source id / path for a room-tone recording")


class CutRecord(_Base):
    """Why a source range is absent from the spine (derived reasons for ``edl show``)."""

    source: str
    start: float
    end: float
    reason: str = "manual"


class HistoryEntry(BaseModel):
    model_config = ConfigDict(extra="allow")
    op: str
    at: str


class EDL(_Base):
    schema_: Literal["videocli.edl/v1"] = Field(default=SCHEMA, alias="schema")
    name: str
    rate: str = "25/1"
    resolution: tuple[int, int] = (1920, 1080)
    color: ColorSpec = Field(default_factory=ColorSpec)
    sources: dict[str, SourceRef] = Field(default_factory=dict)
    tracks: Tracks = Field(default_factory=Tracks)
    transitions: list[Transition] = Field(default_factory=list)
    grades: dict[str, Grade] = Field(default_factory=dict)
    captions: Captions = Field(default_factory=Captions)
    mix: Mix = Field(default_factory=Mix)
    markers: list[Marker] = Field(default_factory=list)
    output: Output = Field(default_factory=Output)
    reframe: Reframe | None = None
    sound: Sound = Field(default_factory=Sound)
    commands: dict[str, list[str]] = Field(default_factory=dict, description="Commands the generic baker may run, by name")
    cuts: list[CutRecord] = Field(default_factory=list)
    history: list[HistoryEntry] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("name must be 1-64 characters of letters, digits, '.', '_' or '-'")
        return v

    @field_validator("rate")
    @classmethod
    def _rate(cls, v: str) -> str:
        from videocli.core.timeutil import Rate

        return str(Rate.parse(v))

    @field_validator("resolution")
    @classmethod
    def _res(cls, v: tuple[int, int]) -> tuple[int, int]:
        w, h = v
        if w < 16 or h < 16 or w > 8192 or h > 8192:
            raise ValueError("resolution out of range")
        if w % 2 or h % 2:
            raise ValueError("resolution must be even in both dimensions")
        return (int(w), int(h))

    @model_validator(mode="after")
    def _references(self) -> EDL:
        seen: set[str] = set()
        items: list[tuple[str, Any]] = [
            *(("clip", c) for c in self.tracks.main),
            *(("overlay", o) for o in self.tracks.overlay),
            *(("graphic", g) for g in self.tracks.graphics),
            *(("audio item", a) for a in self.tracks.audio),
        ]
        for kind, it in items:
            if not ID_RE.match(it.id):
                raise ValueError(f"{kind} id {it.id!r} must be lower-case letters, digits or '_' (e.g. c0001)")
            if it.id in seen:
                raise ValueError(f"duplicate item id {it.id!r}")
            seen.add(it.id)
            src = getattr(it, "source", None)
            if src is not None:
                if src not in self.sources:
                    raise ValueError(f"{kind} {it.id}: unknown source {src!r}")
                dur = self.sources[src].duration
                end = getattr(it, "end", None)
                if dur and end is not None and end > dur + TIME_TOL:
                    raise ValueError(f"{kind} {it.id}: end {end} is past the source duration {dur}")
        clip_ids = {c.id for c in self.tracks.main}
        for t in self.transitions:
            if t.after not in clip_ids:
                raise ValueError(f"transition after unknown clip {t.after!r}")
        for c in self.tracks.main:
            if c.grade and c.grade not in self.grades:
                raise ValueError(f"clip {c.id}: unknown grade {c.grade!r}")
        return self

    # ------------------------------------------------------------------ helpers
    def clip(self, clip_id: str) -> Clip | None:
        return next((c for c in self.tracks.main if c.id == clip_id), None)

    def item(self, item_id: str) -> Any:
        for coll in (self.tracks.main, self.tracks.overlay, self.tracks.graphics, self.tracks.audio):
            for it in coll:
                if it.id == item_id:
                    return it
        return None

    def anchored(self) -> list[Any]:
        return [*self.tracks.overlay, *self.tracks.graphics, *(a for a in self.tracks.audio if a.anchor)]

    def all_ids(self) -> set[str]:
        return {it.id for coll in (self.tracks.main, self.tracks.overlay, self.tracks.graphics, self.tracks.audio) for it in coll}

    def next_id(self, prefix: str = "c") -> str:
        used = self.all_ids()
        nums = [int(i[len(prefix):]) for i in used if i.startswith(prefix) and i[len(prefix):].isdigit()]
        n = max(nums, default=0) + 1
        while f"{prefix}{n:04d}" in used:  # pragma: no cover
            n += 1
        return f"{prefix}{n:04d}"

    def timemap(self):
        from videocli.core.timeutil import TimeMap

        overlaps = {t.after: t.duration for t in self.transitions if t.type != "cut"}
        clips = []
        for c in self.tracks.main:
            # The time map uses each clip's effective speed: source span over output span.
            eff = (c.end - c.start) / max(1e-9, c.out_duration - c.freeze) if (c.ramp or c.freeze) else c.speed
            clips.append((c.id, c.source, c.start, c.end, eff))
        tm = TimeMap(clips, overlaps)
        if any(c.freeze for c in self.tracks.main):
            # Freezes extend a clip's output after its last source frame.
            t = 0.0
            spans = []
            from videocli.core.timeutil import Span

            for c, sp in zip(self.tracks.main, tm.spans):
                nsp = Span(sp.id, sp.source, sp.start, sp.end, sp.speed, t)
                spans.append(nsp)
                t = nsp.out_end + c.freeze - overlaps.get(c.id, 0.0)
            tm.spans = spans
            tm.duration = t + overlaps.get(self.tracks.main[-1].id, 0.0) if spans else 0.0
        return tm

    def output_duration(self) -> float:
        return self.timemap().duration


def empty(name: str, rate: str, resolution: tuple[int, int]) -> EDL:
    return EDL(name=name, rate=rate, resolution=resolution)
