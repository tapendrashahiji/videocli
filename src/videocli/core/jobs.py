"""Job state files, so long commands can resume and ``videocli jobs`` can list them (SPEC §6.4)."""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from videocli.core.fsutil import read_json, write_json
from videocli.core.workspace import Workspace, now_iso


@dataclass
class Job:
    id: str
    command: str
    argv: list[str]
    status: str = "running"  # running | finished | failed
    started: str = field(default_factory=now_iso)
    updated: str = field(default_factory=now_iso)
    pid: int = field(default_factory=os.getpid)
    state: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": self.command,
            "argv": self.argv,
            "status": self.status,
            "started": self.started,
            "updated": self.updated,
            "pid": self.pid,
            "state": self.state,
        }


def _path(ws: Workspace, job_id: str) -> Path:
    return ws.dir("jobs") / f"{job_id}.json"


def start(ws: Workspace, job_id: str, command: str, argv: list[str], state: dict[str, Any] | None = None) -> Job:
    """Start (or resume) a job. Re-using an id keeps the original start time."""
    p = _path(ws, job_id)
    started = now_iso()
    if p.is_file():
        with contextlib.suppress(OSError, ValueError):
            started = read_json(p).get("started", started)
    job = Job(job_id, command, list(argv), started=started, state=state or {})
    write_json(p, job.as_dict())
    return job


def update(ws: Workspace, job: Job, **state: Any) -> None:
    job.state.update(state)
    job.updated = now_iso()
    write_json(_path(ws, job.id), job.as_dict())


def finish(ws: Workspace, job: Job, status: str = "finished") -> None:
    job.status = status
    job.updated = now_iso()
    if status == "finished":
        _path(ws, job.id).unlink(missing_ok=True)
    else:
        write_json(_path(ws, job.id), job.as_dict())


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except SystemError:  # pragma: no cover - Windows oddity
        return False
    return True


def unfinished(ws: Workspace) -> list[dict[str, Any]]:
    out = []
    d = ws.root / "jobs"
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            data = read_json(p)
        except (OSError, ValueError):
            continue
        if data.get("status") == "running" and not _alive(int(data.get("pid", 0))):
            data["status"] = "interrupted"
        out.append(data)
    return out
