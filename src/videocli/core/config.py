"""Settings: one model with every key, default and description.

Precedence, highest first: flag → environment → project file → user file → default.

* Keys are dotted (``silence.threshold_db``). In TOML files they are tables
  (``[silence] threshold_db = -35``); in the environment they are upper-case with
  underscores (``VIDEOCLI_SILENCE_THRESHOLD_DB``).
* The project file is ``.videocli/config.toml``; the user file lives in the platform
  config directory (``videocli/config.toml``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from videocli.core.errors import UsageError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


def _cpu_jobs() -> int:
    return max(1, min(4, os.cpu_count() or 1))


class Settings(BaseModel):
    """Every setting. Field names use ``__`` where the public key has a dot."""

    model_config = {"extra": "forbid", "validate_assignment": True}

    # general
    provider: str = Field(default="agent", description="Default model provider for work orders: agent, local or api")
    jobs: int = Field(default_factory=_cpu_jobs, ge=1, le=64, description="Parallel workers for renders and batch work")
    list_limit: int = Field(default=50, ge=1, description="Default --limit for list outputs")
    ffmpeg: str = Field(default="", description="Path to ffmpeg (empty: auto-detect)")
    ffprobe: str = Field(default="", description="Path to ffprobe (empty: auto-detect)")

    # transcript
    transcript__model: str = Field(default="small", description="faster-whisper model name or path")
    transcript__language: str = Field(default="", description="Language code; empty means auto-detect")
    transcript__device: str = Field(default="auto", description="auto, cpu or cuda")
    transcript__compute_type: str = Field(default="auto", description="ctranslate2 compute type; auto picks per device")
    transcript__backend: str = Field(default="whisper", description="whisper (local, private) or elevenlabs (cloud; uploads the audio; needs: videocli keys set elevenlabs)")
    transcript__elevenlabs_model: str = Field(default="scribe_v1", description="ElevenLabs speech-to-text model id")
    transcript__low_confidence: float = Field(default=0.5, ge=0, le=1, description="Word probability below which a word is low-confidence")
    transcript__low_confidence_share: float = Field(default=0.15, ge=0, le=1, description="Share of low-confidence words that triggers a warning")

    # layout (screen text, faces, free space)
    layout__every: float = Field(default=1.0, gt=0, le=10, description="Seconds between layout samples")
    layout__width: int = Field(default=1280, ge=320, le=3840, description="Frame width for screen-text reading")
    layout__backend: str = Field(default="rapidocr", description="rapidocr (PaddleOCR text) or florence (text and objects, Florence-2)")
    layout__ocr_lang: str = Field(default="en", description="Script for rapidocr: en, ch, devanagari, latin, arabic, cyrillic, …")

    # silence and loudness
    silence__threshold_db: float = Field(default=-35.0, le=0, description="silencedetect noise threshold in dB")
    silence__min_duration: float = Field(default=0.3, gt=0, description="Shortest silence to record, in seconds")
    loudness__target_lufs: float = Field(default=-14.0, le=0, description="Integrated loudness target for renders")
    loudness__true_peak_db: float = Field(default=-1.0, le=0, description="True-peak ceiling for renders")
    loudness__warn_lu: float = Field(default=6.0, gt=0, description="Warn when source loudness is this far from target")

    # planning
    plan__min_clip: float = Field(default=0.3, ge=0, description="Drop clips shorter than this after planning")
    plan__min_cut: float = Field(default=0.2, ge=0, description="Merge away cuts shorter than this")
    plan__filler_pad: float = Field(default=0.04, ge=0, description="Padding around filler-word cuts")

    # render
    render__crf: int = Field(default=18, ge=0, le=51, description="x264 CRF for delivery encodes")
    render__x264_preset: str = Field(default="medium", description="x264 preset for delivery encodes")
    render__intermediate_crf: int = Field(default=12, ge=0, le=51, description="x264 CRF for intermediate segments")
    render__fade_ms: int = Field(default=15, ge=0, le=500, description="Audio fade at every clip edge, in ms")

    # sheets
    sheet__cols: int = Field(default=6, ge=1, le=20, description="Contact-sheet columns")
    sheet__width: int = Field(default=1600, ge=320, le=4096, description="Contact-sheet width in pixels")
    sheet__max_tiles: int = Field(default=48, ge=1, le=400, description="Tiles per contact sheet before paginating")

    # verify thresholds
    verify__duration_tolerance: float = Field(default=0.1, ge=0, description="Allowed output duration error in seconds (plus one frame per clip)")
    verify__av_sync_tolerance: float = Field(default=0.045, ge=0, description="Allowed audio/video stream length difference in seconds")
    verify__black_min: float = Field(default=0.5, gt=0, description="Shortest black stretch to report, in seconds")
    verify__freeze_min: float = Field(default=2.0, gt=0, description="Shortest frozen stretch to report, in seconds")
    verify__click_threshold: float = Field(default=0.35, gt=0, description="Sample step (full scale 1.0) that counts as a click at a join")
    verify__silence_gap: float = Field(default=2.0, gt=0, description="Longest acceptable silence in the output, in seconds")
    verify__loudness_tolerance: float = Field(default=2.0, gt=0, description="Allowed integrated-loudness error in LU")

    # cache
    cache__max_gb: float = Field(default=20.0, gt=0, description="Soft size limit for render, bake and LUT caches")


def key_to_field(key: str) -> str:
    return key.replace(".", "__").replace("-", "_")


def field_to_key(name: str) -> str:
    return name.replace("__", ".")


def env_name(key: str) -> str:
    return "VIDEOCLI_" + key_to_field(key).replace("__", "_").upper()


def all_keys() -> list[str]:
    return [field_to_key(n) for n in Settings.model_fields]


def user_config_path() -> Path:
    from platformdirs import user_config_dir

    return Path(user_config_dir("videocli", appauthor=False)) / "config.toml"


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return _flatten(tomllib.load(fh))
    except tomllib.TOMLDecodeError as exc:
        raise UsageError("BAD_ARGUMENT", f"Cannot parse config file {path}: {exc}") from exc


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def write_toml(path: Path, flat: dict[str, Any]) -> None:
    """Write a flat dotted-key mapping as TOML tables (scalars only)."""
    top = {k: v for k, v in flat.items() if "." not in k}
    tables: dict[str, dict[str, Any]] = {}
    for k, v in flat.items():
        if "." in k:
            sect, name = k.rsplit(".", 1)
            tables.setdefault(sect, {})[name] = v
    lines = [f"{k} = {_toml_value(v)}" for k, v in sorted(top.items())]
    for sect in sorted(tables):
        if lines:
            lines.append("")
        lines.append(f"[{sect}]")
        lines += [f"{k} = {_toml_value(v)}" for k, v in sorted(tables[sect].items())]
    from videocli.core.fsutil import atomic_write_text

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, "\n".join(lines) + "\n")


def _validate_layer(layer: dict[str, Any], origin: str) -> dict[str, Any]:
    known = set(Settings.model_fields)
    out = {}
    for key, value in layer.items():
        name = key_to_field(key)
        if name not in known:
            raise UsageError("BAD_ARGUMENT", f"Unknown setting '{key}' in {origin}.", hint="Run: videocli config show")
        out[name] = value
    return out


class LoadedSettings:
    """Effective settings plus where each value came from."""

    def __init__(self, settings: Settings, sources: dict[str, str]):
        self.settings = settings
        self.sources = sources

    def __getattr__(self, item: str) -> Any:
        return getattr(self.settings, item)

    def get(self, key: str) -> Any:
        return getattr(self.settings, key_to_field(key))


def load(
    project_file: Path | None,
    overrides: dict[str, Any] | None = None,
    *,
    user_file: Path | None = None,
    environ: dict[str, str] | None = None,
) -> LoadedSettings:
    environ = dict(os.environ if environ is None else environ)
    user_file = user_config_path() if user_file is None else user_file
    layers: list[tuple[str, dict[str, Any]]] = []
    layers.append(("user", _validate_layer(read_toml(user_file), str(user_file))))
    if project_file is not None:
        layers.append(("project", _validate_layer(read_toml(project_file), str(project_file))))
    env_layer = {}
    for key in all_keys():
        name = env_name(key)
        if name in environ:
            env_layer[key_to_field(key)] = environ[name]
    layers.append(("env", env_layer))
    layers.append(("flag", {key_to_field(k): v for k, v in (overrides or {}).items() if v is not None}))

    merged: dict[str, Any] = {}
    sources = {n: "default" for n in Settings.model_fields}
    for origin, layer in layers:
        for name, value in layer.items():
            merged[name] = value
            sources[name] = origin
    try:
        settings = Settings(**merged)
    except ValidationError as exc:
        err = exc.errors()[0]
        name = str(err["loc"][0]) if err.get("loc") else "?"
        raise UsageError(
            "BAD_ARGUMENT",
            f"Invalid value for setting '{field_to_key(name)}' (from {sources.get(name, '?')}): {err['msg']}",
        ) from exc
    return LoadedSettings(settings, {field_to_key(k): v for k, v in sources.items()})


def coerce_value(key: str, raw: str) -> Any:
    """Validate a string value for ``key`` and return it in its proper type."""
    name = key_to_field(key)
    if name not in Settings.model_fields:
        raise UsageError("BAD_ARGUMENT", f"Unknown setting '{key}'.", hint="Run: videocli config show")
    try:
        probe = Settings.model_validate({name: raw})
    except ValidationError as exc:
        raise UsageError("BAD_ARGUMENT", f"Invalid value for '{key}': {exc.errors()[0]['msg']}") from exc
    return getattr(probe, name)


def describe() -> list[dict[str, Any]]:
    out = []
    for name, field in Settings.model_fields.items():
        out.append({"key": field_to_key(name), "description": field.description, "env": env_name(field_to_key(name))})
    return out
