"""Render graph: nodes, keys, compiler (EDL → nodes) and a parallel, resumable scheduler (SPEC §10.5).

Phase 2 compiles spine-only timelines: one ``segment`` and one ``lane`` per clip, then
``picture`` (join by stream copy), ``mix`` (sound master) and ``encode``. Every node except
``encode`` is content-addressed in ``.videocli/graph/``; a re-run skips finished nodes.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from videocli.core import progress
from videocli.core.cache import Store, node_key
from videocli.core.errors import RuntimeFailure, VideoCliError

RENDER_VERSION = 1  # bump when node outputs change for the same parameters

Runner = Callable[["Node", Path, list[Path]], str]


@dataclass
class Node:
    kind: str
    params: dict[str, Any]
    inputs: list[Node] = field(default_factory=list)
    label: str = ""
    runner: Runner | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # runtime-only inputs (paths); not part of the key
    key: str = ""
    output: Path | None = None
    cached: bool = False

    def compute_key(self, tool_versions: dict[str, str]) -> str:
        self.key = node_key(self.kind, self.params, [n.key for n in self.inputs], tool_versions)
        return self.key

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "key": self.key[:16], "cached": self.cached, "inputs": [n.key[:16] for n in self.inputs]}


def tool_versions() -> dict[str, str]:
    from videocli.media import ffmpeg

    return {"render": str(RENDER_VERSION), "ffmpeg": ".".join(map(str, ffmpeg.locate("ffmpeg").version_tuple))}


def finalize(nodes: list[Node], store: Store) -> list[Node]:
    """Compute keys in topological order and mark cached nodes."""
    tv = tool_versions()
    for n in topo(nodes):
        n.compute_key(tv)
        out = store.get(n.key)
        if out is not None:
            n.cached = True
            n.output = out
    return nodes


def topo(nodes: list[Node]) -> list[Node]:
    seen: set[int] = set()
    order: list[Node] = []

    def visit(n: Node) -> None:
        if id(n) in seen:
            return
        seen.add(id(n))
        for i in n.inputs:
            visit(i)
        order.append(n)

    for n in nodes:
        visit(n)
    return order


def execute(nodes: list[Node], store: Store, *, jobs: int = 2, on_done: Callable[[Node], None] | None = None) -> None:
    """Run every uncached node, independent ones in parallel, in dependency order."""
    order = [n for n in topo(nodes) if not n.cached]
    total = len(order)
    if not total:
        return
    pending = {id(n): n for n in order}
    done_count = 0
    lock = threading.Lock()
    errors: list[BaseException] = []

    def ready(n: Node) -> bool:
        return all(i.cached or i.output is not None for i in n.inputs)

    def run(n: Node) -> Node:
        assert n.runner is not None
        with store.build(n.key, meta={"kind": n.kind, "label": n.label}) as tmp:
            name = n.runner(n, tmp, [i.output for i in n.inputs])  # type: ignore[misc]
            record(tmp, name)
        n.output = store.path(n.key)
        return n

    running: dict[Future, Node] = {}
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        while pending or running:
            if not errors:
                for nid, n in list(pending.items()):
                    if len(running) >= max(1, jobs):
                        break
                    if ready(n):
                        running[pool.submit(run, n)] = n
                        del pending[nid]
            if not running:
                if errors:
                    break
                if pending:  # pragma: no cover - would mean a cycle
                    raise RuntimeFailure("RENDER_FAILED", "Render graph has unsatisfiable dependencies.")
                break
            finished, _ = wait(list(running), return_when=FIRST_COMPLETED)
            for f in finished:
                n = running.pop(f)
                exc = f.exception()
                if exc is not None:
                    errors.append(exc)
                    continue
                with lock:
                    done_count += 1
                progress.update(done_count / total, message=f"{n.kind} {n.label}")
                if on_done:
                    on_done(n)
            if errors and not running:
                break
    if errors:
        err = errors[0]
        if not isinstance(err, Exception):  # KeyboardInterrupt and friends propagate unchanged
            raise err
        if isinstance(err, VideoCliError):
            raise err
        raise RuntimeFailure("RENDER_FAILED", f"{type(err).__name__}: {err}") from err


def record(entry_dir: Path, name: str) -> None:
    import json

    (entry_dir / "out.json").write_text(json.dumps({"file": name}), encoding="utf-8")


def entry_file(entry_dir: Path) -> Path:
    """Main output file of a finished store entry."""
    import json

    meta = entry_dir / "out.json"
    if meta.is_file():
        return entry_dir / json.loads(meta.read_text(encoding="utf-8"))["file"]
    raise RuntimeFailure("RENDER_FAILED", f"Render cache entry {entry_dir.name[:16]} has no output record.")


def node_file(n: Node) -> Path:
    assert n.output is not None
    return entry_file(n.output)
