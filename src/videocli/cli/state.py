"""Per-invocation state shared by the CLI layer: global options, workspace, settings."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from videocli.core.config import LoadedSettings
    from videocli.core.workspace import Workspace


@dataclass
class State:
    json: bool = False
    workspace_path: str | None = None
    progress: str = "auto"
    dry_run: bool = False
    estimate: bool = False
    force: bool = False
    quiet: bool = False
    verbose: bool = False
    jobs: int | None = None
    provider: str | None = None
    command: str = "videocli"
    argv: list[str] = field(default_factory=list)
    _ws: Workspace | None = None
    _settings: LoadedSettings | None = None

    def reset(self) -> None:
        self.__init__()  # type: ignore[misc]

    # ------------------------------------------------------------------ workspace
    def workspace(self, create: bool = True) -> Workspace:
        from videocli.core.workspace import Workspace, resolve_root
        from videocli.media import ffmpeg

        if self._ws is None:
            root = resolve_root(self.workspace_path)
            ws = Workspace(root)
            if ws.exists():
                ws._load()
            elif create:
                ws.create()
            self._ws = ws
        elif create and not self._ws.exists():
            self._ws.create()
        if self._ws.exists():
            ffmpeg.set_log_dir(self._ws.root / "logs")
        return self._ws

    def existing_workspace(self) -> Workspace | None:
        from videocli.core.workspace import Workspace, resolve_root

        root = resolve_root(self.workspace_path)
        ws = Workspace(root)
        return self.workspace(create=False) if ws.exists() else None

    # ------------------------------------------------------------------ settings
    @property
    def settings(self) -> LoadedSettings:
        if self._settings is None:
            from videocli.core import config
            from videocli.core.workspace import resolve_root
            from videocli.media import ffmpeg

            project = resolve_root(self.workspace_path) / "config.toml"
            overrides: dict[str, Any] = {"jobs": self.jobs, "provider": self.provider}
            self._settings = config.load(project, overrides)
            ffmpeg.set_paths(self._settings.ffmpeg or None, self._settings.ffprobe or None)
        return self._settings


state = State()


def detect_json_mode(argv: list[str]) -> bool:
    if "--json" in argv:
        return True
    env = os.environ.get("VIDEOCLI_OUTPUT", "").strip().lower()
    if env == "json":
        return True
    if env in ("human", "text", "rich"):
        return False
    try:
        return not sys.stdout.isatty()
    except (AttributeError, ValueError):
        return True


def cwd_rel(path: Path | str) -> str:
    from videocli.core.fsutil import rel

    return rel(path)
