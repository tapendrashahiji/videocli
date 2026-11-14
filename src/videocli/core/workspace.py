"""The ``.videocli/`` workspace: layout, schema version, source registry and writer lock (SPEC §5)."""

from __future__ import annotations

import contextlib
import os
import sys
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from videocli import __version__
from videocli.core.errors import InputError
from videocli.core.fsutil import read_json, write_json

WORKSPACE_DIRNAME = ".videocli"
WORKSPACE_SCHEMA = "videocli.workspace/v1"
WORKSPACE_VERSION = 1

SUBDIRS = ("sources", "project", "edits", "grades", "bakes", "graph", "orders", "reports", "review", "logs", "jobs")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def find_workspace(start: Path | None = None) -> Path | None:
    """Nearest existing ``.videocli`` directory at or above ``start``."""
    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        cand = d / WORKSPACE_DIRNAME
        if (cand / "workspace.json").is_file():
            return cand
    return None


def resolve_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Where the workspace is (or would be created).

    Order: ``--workspace`` → ``VIDEOCLI_WORKSPACE`` → nearest existing ``.videocli`` above
    the current directory → ``./.videocli``. A path that does not end in ``.videocli`` is
    treated as the project folder that contains it.
    """
    raw = explicit or os.environ.get("VIDEOCLI_WORKSPACE")
    if raw:
        p = Path(raw).expanduser()
        return (p if p.name == WORKSPACE_DIRNAME else p / WORKSPACE_DIRNAME).resolve()
    found = find_workspace()
    if found:
        return found
    return (Path.cwd() / WORKSPACE_DIRNAME).resolve()


class _Lock:
    """Inter-process exclusive lock on ``.videocli/lock``; re-entrant inside one process."""

    def __init__(self, path: Path):
        self.path = path
        self.depth = 0
        self.fh = None
        self.mutex = threading.RLock()

    def acquire(self, timeout: float) -> None:
        self.mutex.acquire()
        if self.depth:
            self.depth += 1
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        deadline = time.monotonic() + timeout
        while True:
            try:
                _lock_file(fh)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    fh.close()
                    self.mutex.release()
                    raise InputError(
                        "WORKSPACE_LOCKED",
                        "Another videocli process is writing to this workspace.",
                        hint="Wait for it to finish, then retry.",
                        retryable=True,
                        details={"lock": str(self.path)},
                    ) from None
                time.sleep(0.1)
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n".encode())
        fh.flush()
        self.fh = fh
        self.depth = 1

    def release(self) -> None:
        self.depth -= 1
        if self.depth == 0 and self.fh is not None:
            try:
                _unlock_file(self.fh)
            finally:
                self.fh.close()
                self.fh = None
        self.mutex.release()


if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI
    import msvcrt

    def _lock_file(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_file(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = _Lock(self.root / "lock")
        self._data: dict[str, Any] | None = None

    # ------------------------------------------------------------------ creation
    @classmethod
    def open(cls, root: Path | None = None, *, create: bool = True) -> Workspace:
        root = resolve_root(root) if root is None or not isinstance(root, Path) else root
        ws = cls(root)
        if ws.exists():
            ws._load()
        elif create:
            ws.create()
        return ws

    def exists(self) -> bool:
        return (self.root / "workspace.json").is_file()

    def create(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in SUBDIRS:
            (self.root / sub).mkdir(exist_ok=True)
        if not self.exists():
            self._data = {
                "schema": WORKSPACE_SCHEMA,
                "version": WORKSPACE_VERSION,
                "created": now_iso(),
                "tool_version": __version__,
                "sources": {},
                "paths": {},
            }
            self.save()
        gi = self.root / ".gitignore"
        if not gi.exists():
            gi.write_text("# videocli workspace caches\nsources/\ngraph/\nbakes/\ngrades/\nlogs/\njobs/\nlock\n", encoding="utf-8")

    def ensure(self) -> Workspace:
        if not self.exists():
            self.create()
        else:
            for sub in SUBDIRS:
                (self.root / sub).mkdir(exist_ok=True)
        return self

    def _load(self) -> None:
        try:
            data = read_json(self.root / "workspace.json")
        except (OSError, ValueError) as exc:
            raise InputError("WORKSPACE_INVALID", f"Cannot read {self.root / 'workspace.json'}: {exc}") from exc
        if data.get("schema") != WORKSPACE_SCHEMA:
            raise InputError("WORKSPACE_INVALID", f"Unknown workspace schema {data.get('schema')!r}.")
        if int(data.get("version", 0)) > WORKSPACE_VERSION:
            raise InputError(
                "WORKSPACE_INVALID",
                f"Workspace version {data.get('version')} is newer than this videocli understands.",
                hint="Upgrade videocli.",
            )
        data.setdefault("sources", {})
        data.setdefault("paths", {})
        self._data = data

    @property
    def data(self) -> dict[str, Any]:
        if self._data is None:
            if self.exists():
                self._load()
            else:
                self.create()
        assert self._data is not None
        return self._data

    def save(self) -> None:
        assert self._data is not None
        write_json(self.root / "workspace.json", self._data)

    # ------------------------------------------------------------------ layout
    @property
    def project_dir(self) -> Path:
        return self.root.parent

    def dir(self, name: str) -> Path:
        p = self.root / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    def source_dir(self, source_id: str) -> Path:
        return self.dir(f"sources/{source_id}")

    @property
    def config_file(self) -> Path:
        return self.root / "config.toml"

    def log_path(self, name: str) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        return self.dir("logs") / f"{name}-{stamp}.log"

    # ------------------------------------------------------------------ lock
    @contextlib.contextmanager
    def lock(self, timeout: float = 30.0) -> Iterator[None]:
        self.ensure()
        self._lock.acquire(timeout)
        try:
            # Re-read so concurrent writers' changes are not lost.
            if self._lock.depth == 1 and self.exists():
                self._load()
            yield
        finally:
            self._lock.release()
