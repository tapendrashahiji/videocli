"""``setup``: install dependency groups into the tool's own environment and fetch models."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Any

from videocli.core import progress
from videocli.core.errors import DependencyError, RuntimeFailure, VideoCliError
from videocli.setup import profiles, provision


def _pip_command(packages: list[str]) -> list[str]:
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--python", sys.executable, *packages]
    return [sys.executable, "-m", "pip", "install", *packages]


def _npm_command(packages: list[str]) -> list[str] | None:
    npm = shutil.which("npm")
    return [npm, "install", "-g", *packages] if npm else None


def plan(groups: list[str], model: str) -> list[dict[str, Any]]:
    """Actions needed to make ``groups`` usable. Read-only."""
    actions: list[dict[str, Any]] = []
    try:
        provision.provision(dry_run=True)
    except DependencyError as exc:
        actions.append({"kind": "ffmpeg", "status": "blocked", "message": exc.message, "hint": exc.hint})
    else:
        pass
    for name in groups:
        g = profiles.GROUPS[name]
        st = profiles.check_group(name)
        if st.ok:
            continue
        missing_mods = [m for m in g.modules if not profiles.module_present(m)]
        if g.pip and missing_mods:
            actions.append({"kind": "pip", "group": name, "packages": list(g.pip), "command": _pip_command(list(g.pip))})
        if g.node_min is not None:
            nv = profiles.node_version()
            if nv is None or nv[0] < g.node_min:
                actions.append({
                    "kind": "node", "group": name, "status": "blocked",
                    "message": f"Node.js {g.node_min} or newer is required for '{name}'.",
                    "hint": "Install Node.js from https://nodejs.org or your package manager, then re-run setup.",
                })
            elif g.npm:
                cmd = _npm_command(list(g.npm))
                actions.append({"kind": "npm", "group": name, "packages": list(g.npm), "command": cmd})
        if name == "base" and missing_mods and not g.pip:
            actions.append({"kind": "pip", "group": "base", "packages": ["videocli"], "command": _pip_command(["--force-reinstall", "videocli"])})
    if "base" in groups and profiles.whisper_model_path(model) is None:
        actions.append({"kind": "model", "name": f"whisper-{model}", "licence": "MIT"})
    return actions


def execute(actions: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    results = []
    for act in actions:
        kind = act["kind"]
        if act.get("status") == "blocked":
            results.append({**act, "result": "blocked"})
            continue
        label = f"{kind}:{act.get('group') or act.get('name')}"
        with progress.step_ctx(label):
            if kind in ("pip", "npm"):
                cmd = act["command"]
                if not cmd:
                    results.append({**act, "result": "blocked", "message": "npm not found"})
                    continue
                proc = subprocess.run(cmd, capture_output=True, text=True, check=False, env={**os.environ})
                if proc.returncode != 0:
                    tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["unknown error"]
                    raise RuntimeFailure(
                        "INSTALL_FAILED",
                        f"Installing group '{act['group']}' failed: {tail[0]}",
                        hint="Check network or proxy settings and retry. Offline machines need a local package index.",
                        retryable=True,
                        details={"command": cmd},
                    )
                results.append({**act, "result": "installed"})
            elif kind == "model":
                path = profiles.download_whisper(model)
                results.append({**act, "result": "downloaded", "path": str(path)})
            elif kind == "ffmpeg":
                results.append({**act, **provision.provision()})
    return results


def ask_profile() -> str | None:
    """Ask on the terminal device which profile to install (decision D6). None if no terminal."""
    names = ["lecture", "finishing", "full"]
    try:
        tty_in = open("CONIN$" if sys.platform == "win32" else "/dev/tty", encoding="utf-8")
        tty_out = open("CONOUT$" if sys.platform == "win32" else "/dev/tty", "w", encoding="utf-8")
    except OSError:
        return None
    with tty_in, tty_out:
        tty_out.write("\nWhich videocli profile should be installed?\n")
        for i, n in enumerate(names, 1):
            size = profiles.profile_size_mb(n)
            tty_out.write(f"  {i}) {n:<10} ~{size / 1000:.1f} GB  {profiles.PROFILE_BLURB[n]}\n")
        tty_out.write("Anything can be added later with: videocli setup --with GROUP\n")
        tty_out.write("Choose [1]: ")
        tty_out.flush()
        ans = tty_in.readline().strip().lower()
    if not ans:
        return "lecture"
    if ans.isdigit() and 1 <= int(ans) <= len(names):
        return names[int(ans) - 1]
    if ans in profiles.PROFILES:
        return ans
    return "lecture"


def summarize_error(exc: VideoCliError) -> dict[str, Any]:
    return {"code": exc.code, "message": exc.message, "hint": exc.hint}
