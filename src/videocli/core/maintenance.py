"""Cache status, prune and clear across workspace areas (``videocli cache``)."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from videocli.core.cache import Store
from videocli.core.fsutil import dir_size
from videocli.core.workspace import Workspace

STORE_AREAS = ("graph", "bakes", "grades")
OTHER_AREAS = ("sources", "logs", "review")


def _count(p: Path) -> int:
    if not p.is_dir():
        return 0
    return sum(1 for _ in p.iterdir())


def cache_status(ws: Workspace | None, max_gb: float) -> dict[str, Any]:
    areas = []
    if ws is not None:
        for a in (*STORE_AREAS, *OTHER_AREAS):
            p = ws.root / a
            areas.append({"area": a, "bytes": dir_size(p), "entries": _count(p)})
    total = sum(a["bytes"] for a in areas)
    store_total = sum(a["bytes"] for a in areas if a["area"] in STORE_AREAS)
    return {
        "workspace": str(ws.root) if ws else None,
        "areas": areas,
        "total_bytes": total,
        "store_bytes": store_total,
        "limit_bytes": int(max_gb * 1024**3),
        "over_limit": store_total > max_gb * 1024**3,
    }


def cache_prune(ws: Workspace | None, older_than: float | None, max_gb: float, *, dry_run: bool) -> dict[str, Any]:
    removed: list[dict[str, Any]] = []
    if ws is None:
        return {"removed": [], "freed_bytes": 0, "dry_run": dry_run}
    budget = int(max_gb * 1024**3)
    for a in STORE_AREAS:
        st = Store(ws.root / a)
        for e in st.prune(older_than=older_than, max_bytes=budget, dry_run=dry_run):
            removed.append({"area": a, "key": e.key, "bytes": e.size})
    if older_than is not None:
        now = time.time()
        for f in (ws.root / "logs").glob("*.log"):
            if now - f.stat().st_mtime > older_than:
                removed.append({"area": "logs", "key": f.name, "bytes": f.stat().st_size})
                if not dry_run:
                    f.unlink(missing_ok=True)
    return {"removed": removed, "freed_bytes": sum(r["bytes"] for r in removed), "dry_run": dry_run}


def cache_clear(ws: Workspace | None, *, dry_run: bool) -> dict[str, Any]:
    if ws is None:
        return {"cleared": [], "freed_bytes": 0, "dry_run": dry_run}
    cleared = []
    for a in (*STORE_AREAS, "sources", "logs"):
        p = ws.root / a
        size = dir_size(p)
        cleared.append({"area": a, "bytes": size})
        if not dry_run and p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            p.mkdir(exist_ok=True)
    return {"cleared": cleared, "freed_bytes": sum(c["bytes"] for c in cleared), "dry_run": dry_run}
