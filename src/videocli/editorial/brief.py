"""The brief (what the edit is for) and the story (beats with intent and selects)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from videocli.core.errors import InputError
from videocli.core.fsutil import read_json, write_json
from videocli.core.workspace import Workspace


class Brief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_: str = Field(default="videocli.brief/v1", alias="schema")
    logline: str = ""
    audience: str = ""
    duration: float | None = Field(default=None, gt=0)
    aspect: str = "16:9"
    tone: str = ""
    style: str | None = None
    must_include: list[str] = Field(default_factory=list)
    must_avoid: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    music: str | None = None


class Select(BaseModel):
    model_config = ConfigDict(extra="allow")
    source: str
    start: float
    end: float
    score: float = 0.0
    why: str = ""
    text: str = ""


class Beat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    intent: str = ""
    target_seconds: float = Field(default=20.0, gt=0)
    selects: list[Select] = Field(default_factory=list)
    chosen: list[int] = Field(default_factory=list)


class Story(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_: str = Field(default="videocli.story/v1", alias="schema")
    archetype: str = "custom"
    logline: str = ""
    target_duration: float | None = None
    beats: list[Beat] = Field(default_factory=list)


ARCHETYPES: dict[str, list[tuple[str, str, float]]] = {
    "doc": [("hook", "Open on the strongest, most visual moment; raise a question.", 0.08), ("context", "Who, where, why it matters.", 0.2),
            ("turn", "The tension, problem or change.", 0.32), ("payoff", "How it resolves; the line that lands.", 0.3),
            ("close", "A resonant final image or line.", 0.1)],
    "interview": [("hook", "The most surprising answer first.", 0.1), ("who", "Introduce the person.", 0.15),
                  ("story", "Their account, in order.", 0.5), ("reflection", "What it means to them.", 0.18), ("close", "Last word.", 0.07)],
    "tutorial": [("goal", "What you will be able to do.", 0.1), ("setup", "What you need.", 0.15), ("steps", "The steps, in order.", 0.6),
                 ("recap", "Summary and next steps.", 0.15)],
    "promo": [("hook", "Grab attention in three seconds.", 0.12), ("problem", "The need.", 0.2), ("solution", "The product or idea.", 0.38),
              ("proof", "Evidence, people, results.", 0.2), ("cta", "What to do next.", 0.1)],
    "music": [("intro", "Establish mood with the intro.", 0.15), ("build", "Rising energy.", 0.3), ("peak", "The chorus: best shots, fastest cuts.", 0.35),
              ("outro", "Release.", 0.2)],
    "custom": [("beat1", "", 1.0)],
}


def brief_path(ws: Workspace):
    return ws.root / "project" / "brief.json"


def story_path(ws: Workspace):
    return ws.root / "project" / "story.json"


def load_brief(ws: Workspace, required: bool = False) -> Brief:
    p = brief_path(ws)
    if not p.is_file():
        if required:
            raise InputError("ARTIFACT_MISSING", "No brief yet.", hint='Run: videocli brief set --logline "…" --duration 180')
        return Brief()
    return Brief.model_validate(read_json(p))


def save_brief(ws: Workspace, b: Brief) -> None:
    write_json(brief_path(ws), b.model_dump(by_alias=True))


def load_story(ws: Workspace, required: bool = True) -> Story:
    p = story_path(ws)
    if not p.is_file():
        if required:
            raise InputError("ARTIFACT_MISSING", "No story yet.", hint="Run: videocli story init --archetype doc")
        return Story()
    return Story.model_validate(read_json(p))


def save_story(ws: Workspace, s: Story) -> None:
    write_json(story_path(ws), s.model_dump(by_alias=True))


def init_story(archetype: Literal["doc", "interview", "tutorial", "promo", "music", "custom"] | str, brief: Brief) -> Story:
    total = brief.duration or 180.0
    beats = [Beat(id=b, intent=i, target_seconds=round(max(2.0, total * share), 1)) for b, i, share in ARCHETYPES[archetype]]
    return Story(archetype=archetype, logline=brief.logline, target_duration=brief.duration, beats=beats)


def resolve_phrases(ws: Workspace, phrases: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Where each phrase is spoken, across every registered source (source time)."""
    from videocli.core.cache import read_artifact
    from videocli.index.search import find_phrase

    out: dict[str, list[dict[str, Any]]] = {p: [] for p in phrases}
    for sid in ws.data.get("sources", {}):
        tr = read_artifact(ws.source_dir(sid) / "transcript.json")
        if not tr:
            continue
        for p in phrases:
            for h in find_phrase(tr, p):
                out[p].append({"source": sid, "start": h["start"], "end": h["end"]})
    return out
