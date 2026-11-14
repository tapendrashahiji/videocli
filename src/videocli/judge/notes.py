"""Human notes (``videocli.notes/v1``): ingest, list, and apply as scoped EDL operations (phase 10)."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from videocli.core.errors import InputError
from videocli.core.fsutil import read_json, write_json
from videocli.core.timeutil import parse_time
from videocli.core.workspace import Workspace, now_iso

SCHEMA = "videocli.notes/v1"
CATS = ("story", "pacing", "shot", "sound", "colour", "graphics", "other")
ALLOWED = ("edl ", "grade ", "gfx ", "refine ")


class Note(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    edl: str
    at: float | None = None
    start: float | None = Field(default=None, alias="from")
    end: float | None = Field(default=None, alias="to")
    category: Literal["story", "pacing", "shot", "sound", "colour", "graphics", "other"] = "other"
    priority: Literal["low", "medium", "high"] = "medium"
    text: str
    status: Literal["open", "applied", "declined", "question"] = "open"
    ops: list[str] = Field(default_factory=list)
    question: str | None = None
    author: str | None = None


def _path(ws: Workspace) -> Path:
    return ws.root / "project" / "notes.json"


def load_all(ws: Workspace) -> list[Note]:
    p = _path(ws)
    if not p.is_file():
        return []
    return [Note.model_validate(n) for n in read_json(p).get("notes", [])]


def save_all(ws: Workspace, notes: list[Note]) -> None:
    write_json(_path(ws), {"schema": SCHEMA, "updated": now_iso(), "notes": [n.model_dump(by_alias=True, exclude_none=True) for n in notes]})


_LINE = re.compile(r"^\s*(?P<a>[\d:.]+)(?:\s*-\s*(?P<b>[\d:.]+))?\s+(?:(?P<cat>[a-z]+)(?:\s+(?P<pri>low|medium|high))?\s*:\s*)?(?P<text>.+)$")


def parse_text(text: str, edl: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Plain lines: ``12.4 pacing high: hold longer on the hands`` or ``1:02-1:10 shot: wrong take``."""
    notes, errors = [], []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            errors.append(f"line {n}: expected 'TIME [- TIME] [category [priority]:] text'")
            continue
        try:
            a = parse_time(m["a"])
            b = parse_time(m["b"]) if m["b"] else None
        except Exception as exc:  # noqa: BLE001
            errors.append(f"line {n}: {exc}")
            continue
        cat = m["cat"] if m["cat"] in CATS else "other"
        d = {"edl": edl, "category": cat, "priority": m["pri"] or "medium", "text": m["text"].strip()}
        if b is not None:
            d.update({"from": a, "to": b})
        else:
            d["at"] = a
        notes.append(d)
    return notes, errors


def ingest(ws: Workspace, path: Path, edl_name: str | None) -> dict[str, Any]:
    if not path.is_file():
        raise InputError("FILE_NOT_FOUND", f"No such file: {path}")
    raw = path.read_text(encoding="utf-8")
    errors: list[str] = []
    if path.suffix.lower() == ".json":
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InputError("SCHEMA_INVALID", f"{path.name} is not valid JSON (line {exc.lineno}).", details={"line": exc.lineno}) from exc
        edl = doc.get("edl") or edl_name
        items = doc.get("notes", doc if isinstance(doc, list) else [])
        for it in items:
            it.setdefault("edl", edl)
        approved = bool(doc.get("approved")) if isinstance(doc, dict) else False
    else:
        if not edl_name:
            raise InputError("BAD_ARGUMENT", "Text notes need --edl NAME.")
        items, errors = parse_text(raw, edl_name)
        approved = False
    existing = load_all(ws)
    new = []
    for i, it in enumerate(items, 1):
        it["id"] = it.get("id") or f"n{len(existing) + len(new) + 1:04d}"
        it.setdefault("status", "open")
        try:
            new.append(Note.model_validate(it))
        except ValidationError as exc:
            errors.append(f"note {i}: {exc.errors()[0]['msg']} at {'.'.join(map(str, exc.errors()[0]['loc']))}")
    if errors:
        raise InputError("SCHEMA_INVALID", f"{len(errors)} note(s) are invalid; nothing was stored.", details={"errors": errors})
    ids = {n.id for n in existing}
    existing += [n for n in new if n.id not in ids]
    save_all(ws, existing)
    return {"ingested": len(new), "total": len(existing), "approved": approved, "notes": [n.id for n in new]}


def window(n: Note) -> tuple[float, float]:
    if n.start is not None and n.end is not None:
        return n.start, n.end
    t = n.at or 0.0
    return t - 1.0, t + 1.0


def note_order(ws: Workspace, edl: Any, notes: list[Note], new_name: str) -> dict[str, Any]:
    from videocli.providers import orders as om

    tm = edl.timemap()
    items = []
    for n in notes:
        a, b = window(n)
        clips = [sp.id for sp in tm.spans if sp.out_start < b and sp.out_end > a]
        items.append({"id": n.id, "images": [],
                      "question": (f"Note ({n.category}, {n.priority}) at {a:.2f}-{b:.2f}s: \"{n.text}\". Clips there: {', '.join(clips) or 'none'}. "
                                   f"Turn it into videocli commands on EDL '{new_name}' that change ONLY what the note mentions "
                                   f"(allowed: {', '.join(x.strip() for x in ALLOWED)}). If the note is ambiguous, answer action=question with your question."),
                      "answer_schema": {"action": "apply|question|decline", "ops": ["string"], "question?": "string"},
                      "clips": clips})
    return om.create(ws, "note_ops", items, meta={"edl": edl.name, "new": new_name})


def check_scope(op: str, new_name: str, allowed_clips: set[str], all_clips: set[str]) -> str | None:
    """None when ``op`` is a permitted command on the new EDL that only names clips in the note's window."""
    if op.startswith("videocli "):
        op = op[len("videocli "):]
    if not op.startswith(ALLOWED):
        return f"'{op.split()[0]}' is not an allowed command"
    words = shlex.split(op)
    if len(words) < 3 or words[2] != new_name:
        return f"operations must target EDL '{new_name}'"
    named = {w for w in words if w in all_clips}
    outside = named - allowed_clips
    if outside:
        return f"touches clips outside the note: {', '.join(sorted(outside))}"
    return None
