"""Load, validate, snap and save EDL files under ``.videocli/edits/<name>.edl.json``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from videocli.core.errors import InputError, UsageError
from videocli.core.fsutil import atomic_write_text
from videocli.core.timeutil import Rate, ms
from videocli.core.workspace import Workspace, now_iso
from videocli.edl.model import EDL, NAME_RE, HistoryEntry


def edl_path(ws: Workspace, name: str) -> Path:
    if not NAME_RE.match(name):
        raise UsageError("BAD_ARGUMENT", f"Invalid EDL name {name!r}.", hint="Use letters, digits, '.', '_' or '-'.")
    return ws.dir("edits") / f"{name}.edl.json"


def exists(ws: Workspace, name: str) -> bool:
    return edl_path(ws, name).is_file()


def _loc(err: Any) -> str:
    return ".".join(str(p) for p in err.get("loc", ()))


def parse(text: str, origin: str) -> EDL:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(
            "EDL_INVALID", f"{origin} is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}.",
            details={"line": exc.lineno, "column": exc.colno},
        ) from exc
    return validate(raw, origin)


def validate(raw: Any, origin: str = "EDL") -> EDL:
    try:
        return EDL.model_validate(raw)
    except ValidationError as exc:
        errs = exc.errors()
        first = errs[0]
        loc = _loc(first)
        msg = first.get("msg", "invalid")
        raise InputError(
            "EDL_INVALID",
            f"{origin}: {msg}" + (f" (at {loc})" if loc else ""),
            hint="Change the EDL through videocli edl commands rather than by hand.",
            details={"errors": [{"loc": _loc(e), "msg": e.get("msg")} for e in errs[:10]], "count": len(errs)},
        ) from exc


def load(ws: Workspace, name: str) -> EDL:
    p = edl_path(ws, name)
    if not p.is_file():
        names = list_names(ws)
        raise InputError(
            "EDL_NOT_FOUND", f"No EDL named {name!r}.",
            hint=(f"Existing EDLs: {', '.join(names[:10])}" if names else "Create one with: videocli plan FILE -o NAME"),
        )
    return parse(p.read_text(encoding="utf-8"), p.name)


def list_names(ws: Workspace) -> list[str]:
    d = ws.root / "edits"
    return sorted(p.name[: -len(".edl.json")] for p in d.glob("*.edl.json")) if d.is_dir() else []


def snap(edl: EDL) -> EDL:
    """Snap spine clip times to timeline frames so every clip lasts whole frames."""
    rate = Rate.parse(edl.rate)
    for c in edl.tracks.main:
        dur = edl.sources[c.source].duration if c.source in edl.sources else None
        start = rate.snap(c.start)
        frames = max(1, round((c.end - c.start) / c.speed * rate.fps))
        end = ms(start + frames * rate.frame_duration * c.speed)
        if dur and end > dur + 1e-6:
            # Never run past the source: drop whole frames from the tail instead.
            while frames > 1 and end > dur + 1e-6:
                frames -= 1
                end = ms(start + frames * rate.frame_duration * c.speed)
        c.start, c.end = start, end
    for r in edl.cuts:
        r.start, r.end = rate.snap(r.start), rate.snap(r.end)
    edl.cuts = [r for r in edl.cuts if r.end > r.start]
    return edl


def dumps(edl: EDL) -> str:
    data = edl.model_dump(mode="json", by_alias=True, exclude_none=True)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def save(ws: Workspace, edl: EDL, op: str | None = None, **details: Any) -> Path:
    if op:
        edl.history.append(HistoryEntry(op=op, at=now_iso(), **{k: v for k, v in details.items() if v is not None}))
    snap(edl)
    edl = validate(json.loads(dumps(edl)), edl.name)  # re-validate after edits
    p = edl_path(ws, edl.name)
    with ws.lock():
        atomic_write_text(p, dumps(edl))
    return p
