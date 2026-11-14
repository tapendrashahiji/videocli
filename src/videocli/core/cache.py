"""Analysis artifacts and the content-addressed store (SPEC §5.2).

* Artifacts are JSON files under ``sources/<id>/`` whose header holds ``schema``, ``key``,
  ``created``, ``tool_version`` and ``params``. An artifact is fresh when its stored key
  equals the key computed from the current inputs.
* :class:`Store` maps a key to a directory. Render nodes, compiled LUTs and bakes use it.
  Entries are built in a temporary directory and renamed into place, so a crash never
  leaves a half-written entry; least-recently-used entries are pruned first.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from videocli import __version__
from videocli.core.fsutil import dir_size, read_json, write_json
from videocli.core.workspace import now_iso


def make_key(*parts: Any) -> str:
    """SHA-256 over a canonical JSON encoding of ``parts``."""
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def analysis_key(source_id: str, analyzer: str, version: int, params: dict[str, Any]) -> str:
    return make_key(source_id, analyzer, version, params)


def node_key(kind: str, params: dict[str, Any], input_keys: list[str], tool_versions: dict[str, str]) -> str:
    return make_key(kind, params, input_keys, tool_versions)


def write_artifact(path: Path, *, schema: str, key: str, params: dict[str, Any], body: dict[str, Any]) -> Path:
    doc = {"schema": schema, "key": key, "created": now_iso(), "tool_version": __version__, "params": params}
    doc.update(body)
    write_json(path, doc)
    return path


def read_artifact(path: Path) -> dict[str, Any] | None:
    try:
        return read_json(path)
    except (OSError, ValueError):
        return None


def is_fresh(path: Path, key: str) -> bool:
    doc = read_artifact(path)
    return bool(doc) and doc.get("key") == key  # type: ignore[union-attr]


# --------------------------------------------------------------------------- store

COMPLETE = ".complete"
USED = ".used"


@dataclass
class Entry:
    key: str
    path: Path
    size: int
    last_used: float


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)

    def path(self, key: str) -> Path:
        return self.root / key

    def has(self, key: str) -> bool:
        return (self.path(key) / COMPLETE).is_file()

    def get(self, key: str) -> Path | None:
        p = self.path(key)
        if not (p / COMPLETE).is_file():
            return None
        self.touch(key)
        return p

    def touch(self, key: str) -> None:
        with contextlib.suppress(OSError):
            (self.path(key) / USED).write_text(str(time.time()), encoding="utf-8")

    @contextlib.contextmanager
    def build(self, key: str, meta: dict[str, Any] | None = None) -> Iterator[Path]:
        """Yield a temporary directory; on success it becomes the entry for ``key``."""
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".tmp-{key[:16]}-{uuid.uuid4().hex[:8]}"
        tmp.mkdir()
        try:
            yield tmp
            write_json(tmp / "meta.json", {"key": key, "created": now_iso(), "tool_version": __version__, **(meta or {})})
            (tmp / COMPLETE).write_text("", encoding="utf-8")
            (tmp / USED).write_text(str(time.time()), encoding="utf-8")
            final = self.path(key)
            if final.exists():
                shutil.rmtree(final, ignore_errors=True)
            os.replace(tmp, final)
        finally:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)

    def entries(self) -> list[Entry]:
        out: list[Entry] = []
        if not self.root.is_dir():
            return out
        for d in self.root.iterdir():
            if not d.is_dir():
                continue
            if d.name.startswith(".tmp-"):
                out.append(Entry(d.name, d, dir_size(d), 0.0))
                continue
            try:
                last = float((d / USED).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                last = d.stat().st_mtime
            out.append(Entry(d.name, d, dir_size(d), last))
        return out

    def size(self) -> int:
        return sum(e.size for e in self.entries())

    def prune(self, *, older_than: float | None = None, max_bytes: int | None = None, dry_run: bool = False) -> list[Entry]:
        """Remove entries unused for ``older_than`` seconds, then LRU until under ``max_bytes``."""
        entries = sorted(self.entries(), key=lambda e: e.last_used)
        removed: list[Entry] = []
        now = time.time()
        keep: list[Entry] = []
        for e in entries:
            stale_tmp = e.key.startswith(".tmp-") and now - e.path.stat().st_mtime > 3600
            if stale_tmp or (older_than is not None and now - e.last_used > older_than):
                removed.append(e)
            else:
                keep.append(e)
        if max_bytes is not None:
            total = sum(e.size for e in keep)
            while keep and total > max_bytes:
                e = keep.pop(0)
                removed.append(e)
                total -= e.size
        if not dry_run:
            for e in removed:
                shutil.rmtree(e.path, ignore_errors=True)
        return removed

    def clear(self, dry_run: bool = False) -> list[Entry]:
        entries = self.entries()
        if not dry_run:
            for e in entries:
                shutil.rmtree(e.path, ignore_errors=True)
        return entries
