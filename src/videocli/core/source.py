"""Source identity (SPEC §5.2) and the workspace source registry."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from videocli.core.errors import InputError, file_not_found
from videocli.core.workspace import Workspace, now_iso

BLOCK = 1024 * 1024
SOURCE_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def compute_source_id(path: Path) -> str:
    """First 12 hex chars of SHA-256 over size plus three 1 MiB blocks (start, middle, end)."""
    size = os.path.getsize(path)
    h = hashlib.sha256()
    h.update(str(size).encode())
    with open(path, "rb") as fh:
        offsets = [0]
        if size > BLOCK:
            offsets.append(max(0, size // 2 - BLOCK // 2))
            offsets.append(max(0, size - BLOCK))
        for off in offsets:
            fh.seek(off)
            h.update(fh.read(BLOCK))
    return h.hexdigest()[:12]


@dataclass
class Source:
    id: str
    path: Path
    size: int
    mtime: float

    def as_dict(self) -> dict:
        return {"id": self.id, "path": str(self.path), "size": self.size}


@dataclass
class Registration:
    source: Source
    changed_from: str | None = None  # old id when the file at this path changed
    new: bool = False


def _stat(path: Path) -> tuple[int, float]:
    st = path.stat()
    return st.st_size, st.st_mtime


def register(ws: Workspace, path: str | os.PathLike[str]) -> Registration:
    """Register ``path`` (or look it up). Detects a changed file at a known path."""
    p = Path(path)
    if not p.exists():
        raise file_not_found(str(path))
    if p.is_dir():
        raise InputError("FILE_NOT_FOUND", f"Expected a file, got a directory: {path}")
    p = p.resolve()
    size, mtime = _stat(p)
    key = str(p)
    with ws.lock():
        data = ws.data
        known_id = data["paths"].get(key)
        rec = data["sources"].get(known_id) if known_id else None
        if rec and rec.get("size") == size and abs(rec.get("mtime", 0) - mtime) < 1e-6:
            return Registration(Source(known_id, p, size, mtime))  # type: ignore[arg-type]
        sid = compute_source_id(p)
        changed_from = known_id if known_id and known_id != sid else None
        is_new = sid not in data["sources"]
        entry = data["sources"].get(sid, {"registered": now_iso()})
        entry.update({"path": key, "size": size, "mtime": mtime})
        data["sources"][sid] = entry
        data["paths"][key] = sid
        ws.save()
    ws.source_dir(sid)
    return Registration(Source(sid, p, size, mtime), changed_from=changed_from, new=is_new)


def lookup(ws: Workspace, ref: str) -> Source:
    """Find a registered source by id or by path; registers new paths."""
    if SOURCE_ID_RE.match(ref) and not Path(ref).exists():
        rec = ws.data["sources"].get(ref)
        if not rec:
            raise InputError("SOURCE_UNKNOWN", f"No source with id {ref} in this workspace.")
        p = Path(rec["path"])
        return Source(ref, p, rec.get("size", 0), rec.get("mtime", 0.0))
    return register(ws, ref).source


def check_unchanged(source_id: str, path: str | os.PathLike[str]) -> Path:
    """Raise ``SOURCE_CHANGED`` if the file at ``path`` no longer hashes to ``source_id``."""
    p = Path(path)
    if not p.exists():
        raise file_not_found(str(path))
    actual = compute_source_id(p)
    if actual != source_id:
        raise InputError(
            "SOURCE_CHANGED",
            f"{path} changed since it was registered (expected {source_id}, found {actual}).",
            hint="Re-run analyze on the new file and re-plan, or restore the original file.",
            details={"expected": source_id, "actual": actual, "path": str(path)},
        )
    return p.resolve()
