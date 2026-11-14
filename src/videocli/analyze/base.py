"""Analyzer protocol, run context, registry and the ``analyze`` runner (SPEC §10.1).

Each analyzer turns a source (and the artifacts of the analyzers it requires) into one
artifact under ``sources/<id>/``. The runner resolves dependencies, checks availability
of every analyzer before any work starts, skips fresh artifacts, records timings, and
rebuilds the index whenever something new was written.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from videocli.core import progress
from videocli.core.cache import analysis_key, is_fresh, read_artifact
from videocli.core.errors import InputError, UsageError, VideoCliError
from videocli.core.source import Source
from videocli.core.workspace import Workspace


@dataclass
class Availability:
    ok: bool
    error: VideoCliError | None = None

    @classmethod
    def yes(cls) -> Availability:
        return cls(True)


@dataclass
class RunContext:
    ws: Workspace
    settings: Any
    force: bool = False
    options: dict[str, Any] = field(default_factory=dict)  # command-line overrides (model, language, ...)
    probe: Any = None  # ProbeResult
    keys: dict[str, str] = field(default_factory=dict)  # artifact keys of analyzers already resolved
    warnings: list[dict[str, str]] = field(default_factory=list)

    def warn(self, code: str, message: str) -> None:
        self.warnings.append({"code": code, "message": message})

    def artifact_path(self, src: Source, name: str) -> Path:
        return self.ws.source_dir(src.id) / f"{name}.json"


class Analyzer(Protocol):
    name: str
    version: int
    requires: tuple[str, ...]
    schema: str
    needs_audio: bool
    needs_video: bool

    def params(self, ctx: RunContext) -> dict[str, Any]: ...
    def available(self, ctx: RunContext) -> Availability: ...
    def run(self, src: Source, params: dict[str, Any], ctx: RunContext) -> Path: ...


REGISTRY: dict[str, Analyzer] = {}
DEFAULT_SET = ("probe", "audio", "silence", "loudness", "transcript", "scenes")


def register(analyzer: Analyzer) -> Analyzer:
    REGISTRY[analyzer.name] = analyzer
    return analyzer


def _load_all() -> None:
    # Importing the modules registers the analyzers. Kept here so `analyze` imports lazily.
    from videocli.analyze import audio, loudness, scenes, silence, transcript  # noqa: F401
    from videocli.media import probe_analyzer  # noqa: F401


def get(name: str) -> Analyzer:
    _load_all()
    if name not in REGISTRY:
        raise UsageError("BAD_ARGUMENT", f"Unknown analyzer '{name}'.", hint=f"Choose from: {', '.join(sorted(REGISTRY))}")
    return REGISTRY[name]


def resolve(names: list[str]) -> list[str]:
    """Names plus their requirements, in dependency order."""
    _load_all()
    order: list[str] = []
    visiting: set[str] = set()

    def visit(n: str) -> None:
        if n in order:
            return
        if n in visiting:  # pragma: no cover - registry bug
            raise RuntimeError(f"analyzer dependency cycle at {n}")
        visiting.add(n)
        for r in get(n).requires:
            visit(r)
        visiting.discard(n)
        order.append(n)

    for n in names:
        visit(n)
    return order


def selection(only: list[str] | None, skip: list[str] | None, default: tuple[str, ...] = DEFAULT_SET) -> list[str]:
    _load_all()
    for n in [*(only or []), *(skip or [])]:
        get(n)
    chosen = list(only) if only else [n for n in default if n in REGISTRY]
    chosen = [n for n in chosen if n not in (skip or [])]
    return chosen


def key_for(an: Analyzer, src: Source, params: dict[str, Any], ctx: RunContext) -> str:
    inputs = {r: ctx.keys.get(r) for r in an.requires}
    return analysis_key(src.id, an.name, an.version, {"params": params, "inputs": inputs})


@dataclass
class AnalyzeResult:
    source_id: str
    ran: list[str] = field(default_factory=list)
    cached: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    seconds: dict[str, float] = field(default_factory=dict)
    artifacts: list[tuple[str, Path]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, str]] = field(default_factory=list)


def run_analyzers(
    src: Source,
    names: list[str],
    ctx: RunContext,
    *,
    explicit: set[str] | None = None,
    on_artifact: Callable[[str, Path], None] | None = None,
) -> AnalyzeResult:
    """Run ``names`` (already including requirements) for one source."""
    from videocli.media import probe as probemod

    result = AnalyzeResult(src.id)
    explicit = set(names) if explicit is None else explicit
    order = resolve(names)
    analyzers = [get(n) for n in order]

    # Probe first: other analyzers need to know which streams exist.
    ctx.probe = probemod.load(ctx.ws, src)
    has_audio = bool(ctx.probe.audio)
    has_video = ctx.probe.video is not None

    runnable: list[Analyzer] = []
    dropped: set[str] = set()
    for an in analyzers:
        reason = None
        if an.needs_audio and not has_audio:
            reason = "source has no audio"
        elif an.needs_video and not has_video:
            reason = "source has no video"
        elif any(r in dropped for r in an.requires):
            reason = "requirement skipped"
        if reason:
            if an.name in explicit and an.needs_audio and not has_audio and reason == "source has no audio":
                raise InputError("NO_AUDIO", f"{src.path} has no audio stream; '{an.name}' needs one.")
            dropped.add(an.name)
            result.skipped.append({"name": an.name, "reason": reason})
            continue
        runnable.append(an)

    # Validate first: every dependency problem surfaces before any long work starts.
    for an in runnable:
        av = an.available(ctx)
        if not av.ok and av.error is not None:
            raise av.error

    for an in runnable:
        params = an.params(ctx)
        key = key_for(an, src, params, ctx)
        ctx.keys[an.name] = key
        path = ctx.artifact_path(src, an.name)
        if not ctx.force and is_fresh(path, key) and _payload_ok(an, path):
            result.cached.append(an.name)
            result.artifacts.append((an.name, path))
            continue
        t0 = time.monotonic()
        with progress.step_ctx(an.name, source=src.id):
            out = an.run(src, {**params, "_key": key}, ctx)
        result.seconds[an.name] = round(time.monotonic() - t0, 2)
        result.ran.append(an.name)
        result.artifacts.append((an.name, out))
        if on_artifact:
            on_artifact(an.name, out)
    result.warnings = ctx.warnings
    return result


def _payload_ok(an: Analyzer, path: Path) -> bool:
    """Artifacts that point at side files (audio.wav) are fresh only if the file exists."""
    doc = read_artifact(path) or {}
    side = doc.get("file")
    return not side or (path.parent / side).is_file()


def load_artifact(ws: Workspace, src: Source, name: str, *, required: bool = True) -> dict[str, Any] | None:
    path = ws.source_dir(src.id) / f"{name}.json"
    doc = read_artifact(path)
    if doc is None and required:
        raise InputError(
            "ARTIFACT_MISSING",
            f"No {name} artifact for {src.path.name}.",
            hint=f"Run: videocli analyze {src.path} --only {name}",
            details={"artifact": name, "source_id": src.id},
        )
    return doc
