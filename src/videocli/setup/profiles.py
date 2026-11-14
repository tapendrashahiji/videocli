"""Dependency groups, profiles, models and licences in one table (SPEC §4.2, §4.4)."""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from videocli.core.errors import UsageError, feature_not_installed


@dataclass(frozen=True)
class Group:
    name: str
    summary: str
    pip: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    npm: tuple[str, ...] = ()
    node_min: int | None = None
    licences: tuple[tuple[str, str], ...] = ()
    size_mb: int = 0
    models: tuple[str, ...] = ()


GROUPS: dict[str, Group] = {
    g.name: g
    for g in [
        Group(
            "base",
            "Phases 0 to 4; basic grade, composite and titles",
            modules=("typer", "rich", "pydantic", "platformdirs", "numpy", "faster_whisper", "scenedetect", "cv2", "PIL"),
            licences=(
                ("typer", "MIT"), ("rich", "MIT"), ("pydantic", "MIT"), ("platformdirs", "MIT"), ("numpy", "BSD-3-Clause"),
                ("faster-whisper", "MIT"), ("ctranslate2", "MIT"), ("scenedetect", "BSD-3-Clause"),
                ("opencv-python-headless", "Apache-2.0"), ("pillow", "MIT-CMU"), ("ffmpeg", "LGPL-2.1+/GPL-2.0+ (separate process)"),
            ),
            size_mb=250,
            models=("whisper-small",),
        ),
        Group(
            "perception", "Shot attributes, faces, visual search",
            pip=("onnxruntime>=1.17",), modules=("onnxruntime",), licences=(("onnxruntime", "MIT"),), size_mb=600,
        ),
        Group(
            "diarize", "Speaker labels",
            pip=("pyannote.audio>=3.1",), modules=("pyannote.audio",),
            licences=(("pyannote.audio", "MIT (models are gated on Hugging Face)"),), size_mb=2500,
        ),
        Group(
            "music", "Beats, downbeats and sections (light default; see decisions X4)",
            pip=("librosa>=0.10",), modules=("librosa",), licences=(("librosa", "ISC"),), size_mb=120,
        ),
        Group("stems", "Dialogue and music separation", pip=("demucs>=4",), modules=("demucs",), licences=(("demucs", "MIT"),), size_mb=2500),
        Group("ocio", "Log, HDR and ACES colour", pip=("opencolorio>=2.3",), modules=("PyOpenColorIO",), licences=(("OpenColorIO", "BSD-3-Clause"),), size_mb=40),
        Group("manim", "Diagram and equation graphics", pip=("manim>=0.18",), modules=("manim",), licences=(("manim", "MIT"),), size_mb=300),
        Group(
            "html", "HTML, CSS and typographic graphics (HyperFrames)",
            npm=("hyperframes",), node_min=22, licences=(("hyperframes", "Apache-2.0"), ("Node.js", "MIT")), size_mb=400,
        ),
        Group(
            "matte", "Background removal and colour windows",
            pip=("onnxruntime>=1.17",), modules=("onnxruntime",),
            licences=(("onnxruntime", "MIT"), ("matting models", "varies: shown before each download")), size_mb=500,
        ),
        Group("otio", "Editor export (OpenTimelineIO)", pip=("opentimelineio>=0.17",), modules=("opentimelineio",), licences=(("OpenTimelineIO", "Apache-2.0"),), size_mb=30),
    ]
}

PROFILES: dict[str, tuple[str, ...]] = {
    "lecture": ("base",),
    "finishing": ("base", "ocio", "html", "otio"),
    "artistic": ("base", "ocio", "html", "otio", "perception", "music", "diarize"),
    "full": tuple(GROUPS),
}

PROFILE_BLURB = {
    "lecture": "Cut silence and fillers, captions, verify, basic titles/grades/overlays",
    "finishing": "Lecture + log/HDR colour, HTML motion graphics, editor export (needs Node.js 22)",
    "artistic": "Finishing + shot analysis, music structure, speaker labels",
    "full": "Everything, including stems, Manim and matting (several GB)",
}


def profile_size_mb(profile: str) -> int:
    return sum(GROUPS[g].size_mb for g in PROFILES[profile])


def resolve(profile: str | None = None, with_groups: list[str] | None = None) -> list[str]:
    """Groups for a profile plus extra groups, in table order, de-duplicated."""
    chosen: list[str] = []
    if profile:
        if profile not in PROFILES:
            raise UsageError("BAD_ARGUMENT", f"Unknown profile '{profile}'.", hint=f"Choose one of: {', '.join(PROFILES)}")
        chosen += PROFILES[profile]
    for g in with_groups or []:
        if g not in GROUPS:
            raise UsageError("BAD_ARGUMENT", f"Unknown group '{g}'.", hint=f"Choose from: {', '.join(GROUPS)}")
        chosen.append(g)
    if "base" not in chosen:
        chosen.insert(0, "base")
    order = list(GROUPS)
    return sorted(dict.fromkeys(chosen), key=order.index)


# --------------------------------------------------------------------------- checks


def node_version() -> tuple[int, ...] | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"v(\d+)\.(\d+)\.(\d+)", out.strip())
    return tuple(int(x) for x in m.groups()) if m else None


def npm_package_installed(name: str) -> bool:
    npm = shutil.which("npm")
    if not npm:
        return False
    try:
        res = subprocess.run([npm, "ls", "-g", "--depth=0", name], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0 and name in res.stdout


def module_present(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


@dataclass
class GroupStatus:
    name: str
    ok: bool
    missing: list[str] = field(default_factory=list)


def check_group(name: str, *, deep: bool = True) -> GroupStatus:
    g = GROUPS[name]
    missing = [m for m in g.modules if not module_present(m)]
    if g.node_min is not None:
        nv = node_version()
        if nv is None:
            missing.append("node")
        elif nv[0] < g.node_min:
            missing.append(f"node>={g.node_min} (found {'.'.join(map(str, nv))})")
        elif deep:
            missing += [f"npm:{p}" for p in g.npm if not npm_package_installed(p)]
    return GroupStatus(name, not missing, missing)


def require_group(name: str, what: str) -> None:
    """Raise ``FEATURE_NOT_INSTALLED`` with the setup hint when ``name`` is missing."""
    if not check_group(name, deep=False).ok:
        raise feature_not_installed(name, what)


# --------------------------------------------------------------------------- models

WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3", "tiny.en", "base.en", "small.en", "medium.en", "distil-large-v3")
MODEL_LICENCES = {"whisper": "MIT"}


def models_dir() -> Path:
    from platformdirs import user_data_dir

    return Path(user_data_dir("videocli", appauthor=False)) / "models"


def whisper_model_dir(name: str) -> Path:
    return models_dir() / f"whisper-{name}"


def _hf_cache_dirs() -> list[Path]:
    dirs = []
    if os.environ.get("HF_HUB_CACHE"):
        dirs.append(Path(os.environ["HF_HUB_CACHE"]))
    if os.environ.get("HF_HOME"):
        dirs.append(Path(os.environ["HF_HOME"]) / "hub")
    dirs.append(Path.home() / ".cache" / "huggingface" / "hub")
    return dirs


def whisper_model_path(name: str) -> Path | None:
    """Local path of a faster-whisper model if it is available offline, else None."""
    p = Path(name)
    if p.is_dir() and (p / "model.bin").is_file():
        return p
    own = whisper_model_dir(name)
    if (own / "model.bin").is_file():
        return own
    repo = f"models--Systran--faster-whisper-{name}"
    for d in _hf_cache_dirs():
        snaps = d / repo / "snapshots"
        if snaps.is_dir():
            for s in snaps.iterdir():
                if (s / "model.bin").is_file():
                    return s
    return None


def download_whisper(name: str) -> Path:
    from videocli.core.errors import RuntimeFailure

    target = whisper_model_dir(name)
    target.mkdir(parents=True, exist_ok=True)
    try:
        import warnings

        from faster_whisper import download_model

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            download_model(name, output_dir=str(target))
    except Exception as exc:  # network, proxy, disk
        raise RuntimeFailure(
            "DOWNLOAD_FAILED",
            f"Could not download the whisper '{name}' model: {exc}",
            hint="Check the network or proxy (huggingface.co must be reachable), then re-run: videocli setup",
            retryable=True,
        ) from exc
    return target
