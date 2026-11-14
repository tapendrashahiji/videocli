"""API keys for optional cloud services, entered by a person and never shown.

A key comes from, in order: the service's environment variable, the project's ``.env``
(next to ``.videocli``, kept out of git), or the user's ``.env`` in the videocli config
folder. ``videocli keys set`` reads the key from a terminal with echo off, so an agent
driving the CLI can neither type nor read it. Nothing in this module returns a key to the
CLI's output: :func:`status` reports only whether one is set and where.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from videocli.core.errors import UsageError


@dataclass(frozen=True)
class Service:
    name: str
    env: str
    host: str
    signup: str


SERVICES: dict[str, Service] = {
    "elevenlabs": Service("elevenlabs", "ELEVENLABS_API_KEY", "api.elevenlabs.io", "elevenlabs.io → Profile → API keys"),
}

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def _service(name: str) -> Service:
    if name not in SERVICES:
        raise UsageError("BAD_ARGUMENT", f"Unknown service '{name}'.", hint=f"Choose one of: {', '.join(SERVICES)}")
    return SERVICES[name]


def project_dir() -> Path:
    from videocli.core.workspace import find_workspace

    ws = find_workspace()
    return ws.parent if ws else Path.cwd()


def user_env_path() -> Path:
    from platformdirs import user_config_dir

    return Path(user_config_dir("videocli", appauthor=False)) / ".env"


def env_path(scope: str) -> Path:
    return user_env_path() if scope == "user" else project_dir() / ".env"


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        m = _LINE.match(line)
        if m and not line.lstrip().startswith("#"):
            v = m.group(2)
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out[m.group(1)] = v
    return out


def _lookup(svc: Service) -> tuple[str | None, str | None]:
    if os.environ.get(svc.env):
        return os.environ[svc.env], "environment"
    for scope in ("project", "user"):
        v = read_env(env_path(scope)).get(svc.env)
        if v:
            return v, scope
    return None, None


def get(name: str) -> str | None:
    """The key for ``name``, for request headers only. Never print or return it to the CLI."""
    return _lookup(_service(name))[0]


def status() -> list[dict[str, Any]]:
    out = []
    for svc in SERVICES.values():
        _, where = _lookup(svc)
        out.append({
            "service": svc.name, "set": where is not None, "source": where, "env": svc.env, "host": svc.host,
            "project_env": str(env_path("project")), "user_env": str(env_path("user")),
        })
    return out


def _write_env(path: Path, var: str, value: str | None) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    kept = [ln for ln in lines if not ((m := _LINE.match(ln)) and m.group(1) == var)]
    if value is not None:
        kept.append(f"{var}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + ("\n" if kept else ""))
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - Windows
        pass


def ensure_gitignored(folder: Path) -> bool:
    """Add ``.env`` to the project's ``.gitignore``. Returns True when the file changed."""
    gi = folder / ".gitignore"
    lines = gi.read_text(encoding="utf-8").splitlines() if gi.is_file() else []
    if any(ln.strip() in (".env", "/.env", "*.env", ".env*") for ln in lines):
        return False
    with open(gi, "a", encoding="utf-8") as f:
        if lines and lines[-1].strip():
            f.write("\n")
        f.write("# API keys for videocli (never commit)\n.env\n")
    return True


def valid_key(value: str) -> bool:
    return bool(value) and len(value) >= 8 and not any(c.isspace() for c in value) and value.isprintable()


def set_interactive(name: str, scope: str = "project", *, reader=None, stdin_isatty=None) -> dict[str, Any]:
    """Ask a person for the key with echo off and store it. Refuses without a terminal."""
    svc = _service(name)
    isatty = sys.stdin.isatty() if stdin_isatty is None else stdin_isatty
    if not isatty:
        raise UsageError(
            "NEEDS_TERMINAL",
            "API keys are typed by a person in a terminal, so they never pass through an agent or a log.",
            hint=f"Run this yourself in a terminal: videocli keys set {name}   (or export {svc.env} in your shell)",
        )
    if reader is None:
        import getpass

        reader = getpass.getpass
    value = reader(f"{svc.name} API key ({svc.signup}); input is hidden: ").strip()
    if not valid_key(value):
        raise UsageError("BAD_ARGUMENT", "That does not look like an API key (empty, too short or contains spaces).",
                         hint=f"Copy the key again from {svc.signup} and re-run: videocli keys set {name}")
    path = env_path(scope)
    _write_env(path, svc.env, value)
    ignored = ensure_gitignored(path.parent) if scope == "project" else False
    return {"service": name, "set": True, "file": str(path), "scope": scope, "gitignore_updated": ignored}


def remove(name: str, scope: str = "project") -> dict[str, Any]:
    svc = _service(name)
    path = env_path(scope)
    had = svc.env in read_env(path)
    if had:
        _write_env(path, svc.env, None)
    return {"service": name, "removed": had, "file": str(path), "scope": scope}
