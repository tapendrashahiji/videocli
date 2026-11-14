"""``doctor``: one report of what is installed, what works, and the commands that fix the rest."""

from __future__ import annotations

import platform
import shutil
import sys
from typing import Any

from videocli.core.errors import DependencyError
from videocli.media import ffmpeg
from videocli.setup import profiles


def _tool(name: str) -> dict[str, Any]:
    try:
        t = ffmpeg.locate(name)
        return {"path": t.path, "version": ".".join(map(str, t.version_tuple)), "origin": t.origin, "ok": True}
    except DependencyError as exc:
        return {"path": None, "version": None, "ok": False, "error": exc.code, "message": exc.message}


def _gpu() -> dict[str, Any]:
    cuda = False
    devices = 0
    try:
        import ctranslate2  # bundled with faster-whisper

        devices = int(ctranslate2.get_cuda_device_count())
        cuda = devices > 0
    except Exception:
        cuda = bool(shutil.which("nvidia-smi"))
    return {"cuda": cuda, "cuda_devices": devices}


def report(model: str = "small", skill_check: bool = True) -> dict[str, Any]:
    ff = _tool("ffmpeg")
    fp = _tool("ffprobe")
    fix: list[str] = []
    if ff["ok"]:
        try:
            caps = ffmpeg.capabilities()
            ff["missing_features"] = caps.base_missing()
            ff["finishing_missing"] = caps.finishing_missing()
            if ff["missing_features"]:
                ff["ok"] = False
        except DependencyError as exc:  # pragma: no cover
            ff["ok"] = False
            ff["missing_features"] = [exc.code]
    else:
        ff["missing_features"] = []
    if not ff["ok"] or not fp["ok"]:
        fix.append("videocli setup")

    groups: dict[str, bool] = {}
    group_missing: dict[str, list[str]] = {}
    for name in profiles.GROUPS:
        st = profiles.check_group(name, deep=bool(profiles.GROUPS[name].npm))
        groups[name] = st.ok
        if not st.ok:
            group_missing[name] = st.missing
    if not groups["base"]:
        fix.append("pip install --force-reinstall videocli")

    models = {f"whisper-{model}": profiles.whisper_model_path(model) is not None}
    for name in ("tiny", "base", "small", "medium", "large-v3"):
        models.setdefault(f"whisper-{name}", profiles.whisper_model_path(name) is not None)
    if not models[f"whisper-{model}"] and "videocli setup" not in fix:
        fix.append("videocli setup")

    licences = []
    for name, ok in groups.items():
        if ok:
            licences += [{"name": n, "licence": lic, "group": name} for n, lic in profiles.GROUPS[name].licences]
    for mname, present in models.items():
        if present:
            licences.append({"name": mname, "licence": profiles.MODEL_LICENCES["whisper"], "group": "model"})

    warnings: list[dict[str, str]] = []
    skill: dict[str, Any] | None = None
    if skill_check:
        try:
            from videocli.agent.guide import installed_skill_status

            skill = installed_skill_status()
            if skill and skill.get("stale"):
                warnings.append({"code": "SKILL_STALE", "message": f"Installed skill at {skill['path']} is from videocli {skill['version']}."})
                fix.append("videocli skill install")
        except ImportError:  # pragma: no cover - agent package arrives in phase 4
            skill = None

    lost = profiles.lost_groups()
    if lost:
        warnings.append({"code": "GROUPS_LOST", "message": f"Installed before but missing now (a reinstall removes them): {', '.join(lost)}."})
        fix.append("videocli setup --restore")
    gpu = _gpu()
    if gpu["cuda"] and not groups.get("gpu") and "gpu" not in lost:
        warnings.append({"code": "GPU_RUNTIME_MISSING",
                         "message": "An NVIDIA GPU is present but the CUDA 12 runtime is not installed; transcription runs on the CPU."})
        fix.append("videocli setup --with gpu   # optional: GPU transcription")
    ready = bool(ff["ok"] and fp["ok"] and groups["base"] and models[f"whisper-{model}"])
    out: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": f"{sys.platform}-{platform.machine().lower()}",
        "ffmpeg": ff,
        "ffprobe": fp,
        "models": models,
        "groups": groups,
        "group_missing": group_missing,
        "licences": licences,
        "gpu": gpu,
        "ready": ready,
        "fix": list(dict.fromkeys(fix)),
        "reinstall_command": profiles.reinstall_command(),
    }
    if skill is not None:
        out["skill"] = skill
    out["_warnings"] = warnings
    return out
