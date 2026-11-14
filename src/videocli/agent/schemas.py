"""JSON Schemas for the envelope, every file type, and the data of the main commands.

Pydantic models are the source; ``videocli schema`` exports them. Command-data models allow
extra keys (new fields are additive under semantic versioning) but require the documented ones.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class Artifact(BaseModel):
    kind: str
    path: str


class Warning_(BaseModel):
    code: str
    message: str


class Envelope(BaseModel):
    """Success envelope (SPEC §6.1)."""

    ok: Literal[True]
    command: str
    version: str
    data: Any
    artifacts: list[Artifact]
    warnings: list[Warning_]
    next: list[str]


class ErrorBody(BaseModel):
    code: str
    message: str
    hint: str | None = None
    retryable: bool
    details: dict[str, Any]


class ErrorEnvelope(BaseModel):
    ok: Literal[False]
    command: str
    version: str
    error: ErrorBody


# --------------------------------------------------------------------------- artifacts


class Word(_Open):
    start: float
    end: float
    word: str
    p: float | None = None


class Segment(_Open):
    id: int
    start: float
    end: float
    text: str
    words: list[Word] = Field(default_factory=list)


class _Header(_Open):
    schema_: str = Field(alias="schema")
    key: str
    created: str
    tool_version: str
    params: dict[str, Any]


class TranscriptFile(_Header):
    source_id: str
    language: str | None = None
    model: str
    duration: float
    word_count: int
    segments: list[Segment]


class Range_(_Open):
    start: float
    end: float


class SilenceFile(_Header):
    source_id: str
    duration: float
    silence_seconds: float
    speech_seconds: float
    silences: list[Range_]
    speech: list[Range_]


class LoudnessFile(_Header):
    source_id: str
    integrated_lufs: float | None
    lra_lu: float | None
    true_peak_db: float | None
    momentary_per_second: list[float]


class ScenesFile(_Header):
    source_id: str
    duration: float
    scenes: list[Range_]


class IndexFile(_Open):
    schema_: str = Field(alias="schema")
    source_id: str
    path: str
    summary: dict[str, Any]
    artifacts: dict[str, Any]


class CheckOut(_Open):
    name: str
    status: Literal["pass", "warn", "fail", "skip"]
    message: str
    items: list[dict[str, Any]]


class VerifyReport(_Open):
    schema_: str = Field(alias="schema")
    output: str
    edl: str
    passed: bool
    failed: list[str]
    warned: list[str]
    duration: float
    checks: list[CheckOut]


# --------------------------------------------------------------------------- command data


class AnalyzeData(_Open):
    source_id: str
    path: str
    ran: list[str]
    cached: list[str]
    skipped: list[dict[str, str]]
    seconds: dict[str, float]
    summary: dict[str, Any]


class ListData(_Open):
    total: int
    truncated: bool


class TranscriptData(ListData):
    source_id: str
    format: str


class SilencesData(ListData):
    silences: list[Range_]


class FindMatch(_Open):
    start: float
    end: float
    segment: int
    text: str


class FindData(ListData):
    query: str
    matches: list[FindMatch]


class EdlShowData(_Open):
    name: str
    tracks: dict[str, int]
    source_duration: float
    output_duration: float
    removed_seconds: float
    removed_percent: float
    shortest_clip: float
    longest_cut: dict[str, Any] | None
    by_reason: dict[str, float]


class LintIssue(_Open):
    rule: str
    severity: Literal["error", "warn"]


class LintData(_Open):
    name: str
    passed: bool
    issues: list[LintIssue]


class EdlOpData(_Open):
    before: dict[str, Any]
    after: dict[str, Any]
    added: list[str]
    removed: list[str]


class RenderData(_Open):
    output: str
    edl: str
    preset: str
    output_duration: float
    frames: int
    nodes: dict[str, int]


class Tile(_Open):
    row: int
    col: int
    time: float


class SheetOut(_Open):
    path: str
    width: int
    height: int
    tiles: list[Tile]


class SheetData(_Open):
    mode: str
    tiles: int
    sheets: list[SheetOut]


class DoctorData(_Open):
    python: str
    platform: str
    ffmpeg: dict[str, Any]
    ffprobe: dict[str, Any]
    models: dict[str, bool]
    groups: dict[str, bool]
    licences: list[dict[str, str]]
    gpu: dict[str, Any]
    ready: bool
    fix: list[str]


def _file_models() -> dict[str, type[BaseModel]]:
    from videocli.edl.model import EDL
    from videocli.media.probe import ProbeResult

    return {
        "envelope": Envelope,
        "error": ErrorEnvelope,
        "probe": ProbeResult,
        "edl": EDL,
        "transcript": TranscriptFile,
        "silence": SilenceFile,
        "loudness": LoudnessFile,
        "scenes": ScenesFile,
        "index": IndexFile,
        "verify": VerifyReport,
    }


#: Command name → model of its ``data``.
COMMAND_DATA: dict[str, type[BaseModel]] = {
    "analyze": AnalyzeData,
    "transcript": TranscriptData,
    "silences": SilencesData,
    "find": FindData,
    "edl show": EdlShowData,
    "plan": EdlShowData,
    "edl lint": LintData,
    "edl cut": EdlOpData,
    "edl keep": EdlOpData,
    "edl restore": EdlOpData,
    "edl trim": EdlOpData,
    "edl split": EdlOpData,
    "edl move": EdlOpData,
    "render": RenderData,
    "verify": VerifyReport,
    "sheet": SheetData,
    "doctor": DoctorData,
}


def models() -> dict[str, type[BaseModel]]:
    out = dict(_file_models())
    from videocli.media.probe import ProbeResult

    for cmd, m in COMMAND_DATA.items():
        out[f"command:{cmd}"] = m
    out["command:probe"] = ProbeResult
    return out


def json_schema(name: str) -> dict[str, Any]:
    from videocli.core.errors import UsageError

    ms = models()
    if name not in ms:
        raise UsageError("BAD_ARGUMENT", f"No schema named {name!r}.", hint=f"Names: {', '.join(ms)}")
    sch = ms[name].model_json_schema(by_alias=True)
    sch["$id"] = f"https://videocli.dev/schema/{name.replace(':', '/').replace(' ', '-')}.json"
    return sch


def validate_data(command: str, data: Any) -> None:
    """Validate a command's ``data`` against its model (used by contract tests)."""
    m = models().get(f"command:{command}")
    if m is not None:
        m.model_validate(data)
