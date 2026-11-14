"""``probe``: normalise ffprobe output into ``videocli.probe/v1`` (SPEC §8.1)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from videocli.core.cache import analysis_key, is_fresh, read_artifact, write_artifact
from videocli.core.errors import InputError
from videocli.core.source import Source
from videocli.core.timeutil import Rate
from videocli.core.workspace import Workspace
from videocli.media import ffmpeg

SCHEMA = "videocli.probe/v1"
VERSION = 1

COMMON_PIX_FMTS = {
    "yuv420p", "yuvj420p", "yuv422p", "yuvj422p", "yuv444p", "yuvj444p", "nv12",
    "yuv420p10le", "yuv422p10le", "yuv444p10le", "p010le", "yuv420p12le", "yuv422p12le",
    "rgb24", "bgr24", "rgba", "bgra", "argb", "gbrp", "gbrp10le", "yuva420p", "yuva444p10le", "gray",
}
LOG_PATTERNS = {
    "s-log": r"s-?log[23]?", "c-log": r"c-?log[23]?", "v-log": r"v-?log", "log-c": r"log-?c[34]?|arri",
    "f-log": r"f-?log2?", "n-log": r"n-?log", "d-log": r"d-?log", "hlg-tagged-as-log": r"\bhlg\b",
}
VERY_LONG = 4 * 3600


class Warn(BaseModel):
    code: str
    message: str


class ColorInfo(BaseModel):
    primaries: str | None = None
    transfer: str | None = None
    matrix: str | None = None
    range: str | None = None
    hdr: str | None = Field(default=None, description="hlg, pq or null for SDR")
    log_guess: str | None = None


class VideoInfo(BaseModel):
    index: int = 0
    codec: str
    width: int
    height: int
    display_width: int
    display_height: int
    fps: str
    avg_fps: str
    vfr: bool
    rotation: int = 0
    pix_fmt: str | None = None
    bit_depth: int = 8
    frames: int | None = None
    duration: float | None = None
    alpha: bool = False
    color: ColorInfo = Field(default_factory=ColorInfo)


class AudioInfo(BaseModel):
    index: int
    stream_index: int
    codec: str
    sample_rate: int
    channels: int
    channel_layout: str | None = None
    language: str | None = None
    duration: float | None = None


class SubtitleInfo(BaseModel):
    index: int
    stream_index: int
    codec: str
    language: str | None = None


class ProbeResult(BaseModel):
    schema_: str = Field(default=SCHEMA, alias="schema")
    source_id: str
    path: str
    size: int
    duration: float
    container: str
    bit_rate: int | None = None
    video: VideoInfo | None = None
    audio: list[AudioInfo] = []
    subtitles: list[SubtitleInfo] = []
    warnings: list[Warn] = []

    model_config = {"populate_by_name": True}


def _num(v: Any, cast=float) -> Any:
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


def _rate(text: str | None) -> Rate | None:
    if not text or text in ("0/0", "0/1"):
        return None
    try:
        return Rate.parse(text)
    except Exception:
        return None


def _rotation(stream: dict[str, Any]) -> int:
    rot = None
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            rot = _num(sd["rotation"], float)
    if rot is None:
        rot = _num((stream.get("tags") or {}).get("rotate"), float)
    if rot is None:
        return 0
    # ffprobe reports display-matrix rotation counter-clockwise; normalise to 0/90/180/270 clockwise.
    r = int(round(-rot)) % 360
    return min((0, 90, 180, 270, 360), key=lambda x: abs(x - r)) % 360


def _bit_depth(stream: dict[str, Any]) -> int:
    raw = _num(stream.get("bits_per_raw_sample"), int)
    if raw:
        return raw
    pf = stream.get("pix_fmt") or ""
    m = re.search(r"p(\d{2})(?:le|be)$", pf) or re.search(r"(\d{2})(?:le|be)$", pf)
    if m:
        return int(m.group(1))
    return 8


def classify_color(stream: dict[str, Any], tags_text: str) -> ColorInfo:
    transfer = stream.get("color_transfer")
    hdr = None
    if transfer == "arib-std-b67":
        hdr = "hlg"
    elif transfer == "smpte2084":
        hdr = "pq"
    log_guess = None
    low = tags_text.lower()
    for name, pat in LOG_PATTERNS.items():
        if name == "hlg-tagged-as-log":
            continue
        if re.search(pat, low):
            log_guess = name
            break
    return ColorInfo(
        primaries=stream.get("color_primaries"),
        transfer=transfer,
        matrix=stream.get("color_space"),
        range=stream.get("color_range"),
        hdr=hdr,
        log_guess=log_guess,
    )


def normalize(raw: dict[str, Any], src: Source) -> ProbeResult:
    fmt = raw.get("format") or {}
    streams = raw.get("streams") or []
    warnings: list[Warn] = []
    duration = _num(fmt.get("duration")) or 0.0
    container = (fmt.get("format_name") or "").split(",")[0]
    ext = src.path.suffix.lower().lstrip(".")
    if ext and ext in (fmt.get("format_name") or "").split(","):
        container = ext

    video = None
    audio: list[AudioInfo] = []
    subs: list[SubtitleInfo] = []
    a_i = s_i = 0
    for st in streams:
        kind = st.get("codec_type")
        disp = st.get("disposition") or {}
        if kind == "video" and video is None and not disp.get("attached_pic"):
            r_rate = _rate(st.get("r_frame_rate"))
            a_rate = _rate(st.get("avg_frame_rate")) or r_rate
            rate = a_rate or r_rate or Rate.parse("25/1")
            vfr = False
            if r_rate and a_rate and abs(r_rate.fps - a_rate.fps) / max(r_rate.fps, 1e-6) > 0.01:
                vfr = True
            rotation = _rotation(st)
            w, h = int(st.get("width") or 0), int(st.get("height") or 0)
            dw, dh = (h, w) if rotation in (90, 270) else (w, h)
            tags_text = " ".join(f"{k}={v}" for k, v in {**(fmt.get("tags") or {}), **(st.get("tags") or {})}.items())
            pix = st.get("pix_fmt")
            # Report the stream's own nominal rate when it is constant; avg for VFR.
            fps_rate = r_rate if (r_rate and not vfr) else rate
            video = VideoInfo(
                index=int(st.get("index", 0)),
                codec=st.get("codec_name") or "unknown",
                width=w,
                height=h,
                display_width=dw,
                display_height=dh,
                fps=str(fps_rate),
                avg_fps=str(a_rate or rate),
                vfr=vfr,
                rotation=rotation,
                pix_fmt=pix,
                bit_depth=_bit_depth(st),
                frames=_num(st.get("nb_frames"), int),
                duration=_num(st.get("duration")),
                alpha=bool(pix and pix.startswith(("yuva", "rgba", "bgra", "argb", "abgr", "gbrap", "ya", "rgb32", "bgr32"))),
                color=classify_color(st, tags_text),
            )
        elif kind == "audio":
            audio.append(
                AudioInfo(
                    index=a_i,
                    stream_index=int(st.get("index", 0)),
                    codec=st.get("codec_name") or "unknown",
                    sample_rate=_num(st.get("sample_rate"), int) or 0,
                    channels=int(st.get("channels") or 0),
                    channel_layout=st.get("channel_layout"),
                    language=(st.get("tags") or {}).get("language"),
                    duration=_num(st.get("duration")),
                )
            )
            a_i += 1
        elif kind == "subtitle":
            subs.append(
                SubtitleInfo(
                    index=s_i,
                    stream_index=int(st.get("index", 0)),
                    codec=st.get("codec_name") or "unknown",
                    language=(st.get("tags") or {}).get("language"),
                )
            )
            s_i += 1

    if not duration:
        durs = [d for d in [video.duration if video else None, *[a.duration for a in audio]] if d]
        duration = max(durs) if durs else 0.0

    if video is None and not audio:
        raise InputError("NOT_A_MEDIA_FILE", f"{src.path} has no audio or video streams.", hint="Is this a media file?")
    if not audio:
        warnings.append(Warn(code="NO_AUDIO_STREAM", message="Source has no audio stream; audio analyzers will be skipped."))
    if video:
        if video.vfr:
            warnings.append(Warn(code="VFR_SOURCE", message=f"Variable frame rate (nominal {video.fps}, average {video.avg_fps}); renders conform it to the timeline rate."))
        if video.width % 2 or video.height % 2:
            warnings.append(Warn(code="ODD_DIMENSIONS", message=f"{video.width}x{video.height} has an odd dimension; renders pad or crop by one pixel."))
        if video.pix_fmt and video.pix_fmt not in COMMON_PIX_FMTS:
            warnings.append(Warn(code="UNUSUAL_PIXEL_FORMAT", message=f"Pixel format {video.pix_fmt} is unusual; renders convert it."))
        if video.color.hdr:
            warnings.append(Warn(code="HDR_SOURCE", message=f"Source is {video.color.hdr.upper()}; an input transform is required."))
        if video.color.log_guess:
            warnings.append(Warn(code="LOG_SOURCE", message=f"Tags suggest camera log ({video.color.log_guess}); supply an input transform LUT."))
        if video.rotation:
            warnings.append(Warn(code="ROTATED_SOURCE", message=f"Display rotation {video.rotation}°; frames and renders apply it."))
    if duration > VERY_LONG:
        warnings.append(Warn(code="VERY_LONG_FILE", message=f"File is {duration / 3600:.1f} hours long; analysis will take a while."))

    return ProbeResult(
        source_id=src.id,
        path=str(src.path),
        size=src.size,
        duration=round(duration, 3),
        container=container or "unknown",
        bit_rate=_num(fmt.get("bit_rate"), int),
        video=video,
        audio=audio,
        subtitles=subs,
        warnings=warnings,
    )


def probe_path(path: Path, source_id: str = "000000000000") -> ProbeResult:
    """Probe a file without a workspace (used by verify on outputs)."""
    raw = ffmpeg.run_probe(["-show_format", "-show_streams", ffmpeg.fpath(path)], log_name="probe")
    st = path.stat()
    return normalize(raw, Source(source_id, path.resolve(), st.st_size, st.st_mtime))


def probe(ws: Workspace, src: Source, *, force: bool = False) -> tuple[ProbeResult, Path, bool]:
    """Probe a registered source, using the cached artifact when fresh. Returns (result, path, cached)."""
    out = ws.source_dir(src.id) / "probe.json"
    key = analysis_key(src.id, "probe", VERSION, {})
    if not force and is_fresh(out, key):
        doc = read_artifact(out) or {}
        body = {k: v for k, v in doc.items() if k not in ("key", "created", "tool_version", "params")}
        body["path"] = str(src.path)
        return ProbeResult.model_validate(body), out, True
    try:
        raw = ffmpeg.run_probe(["-show_format", "-show_streams", ffmpeg.fpath(src.path)], log_name="probe")
    except Exception as exc:
        from videocli.core.errors import RuntimeFailure

        if isinstance(exc, RuntimeFailure) and exc.code == "FFMPEG_FAILED":
            raise InputError(
                "NOT_A_MEDIA_FILE",
                f"ffprobe could not read {src.path}: {exc.message.split(': ', 1)[-1]}",
                hint="Check that the file is a complete audio or video file.",
                details=exc.details,
            ) from exc
        raise
    result = normalize(raw, src)
    body = result.model_dump(by_alias=True, exclude={"schema_"})
    write_artifact(out, schema=SCHEMA, key=key, params={}, body=body)
    return result, out, False


def load(ws: Workspace, src: Source) -> ProbeResult:
    return probe(ws, src)[0]
