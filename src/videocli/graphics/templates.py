"""Template model, discovery and parameter validation; the brand kit (phase 8)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from videocli.core.errors import InputError, UsageError
from videocli.core.fsutil import read_json, write_json
from videocli.core.workspace import Workspace

BUILTIN = Path(__file__).parent / "templates"
FONTS = Path(__file__).resolve().parents[1] / "render" / "bakers" / "fonts"
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
BAKERS = ("titles", "manim", "html", "generic")


class ParamSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = "string"  # string | number | color | choice | file | bool | list
    required: bool = False
    default: Any = None
    max_chars: int | None = None
    min: float | None = None
    max: float | None = None
    choices: list[str] | None = None


class Timing(BaseModel):
    default: float = 4.0
    min: float = 0.5
    in_: float = Field(default=0.4, alias="in")
    out: float = 0.3
    model_config = ConfigDict(populate_by_name=True)


class Template(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    schema_: str = Field(default="videocli.template/v1", alias="schema")
    name: str
    baker: str = "titles"
    design: str | None = None
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    duration: Timing = Field(default_factory=Timing)
    placement: dict[str, Any] = Field(default_factory=dict)
    aspects: list[str] = Field(default_factory=lambda: ["16:9", "9:16", "1:1", "4:5"])
    entry: str | None = None  # manim scene file / html file / generic command name
    folder: str | None = None


class Brand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    font: str | None = None
    font_bold: str | None = None
    colors: dict[str, str] = Field(default_factory=lambda: {"primary": "#111111", "secondary": "#ffffff", "accent": "#e63946"})
    logo: str | None = None
    safe_margin: float = Field(default=0.05, ge=0, le=0.2)


def search_dirs(ws: Workspace | None) -> list[Path]:
    from platformdirs import user_data_dir

    dirs = []
    if ws is not None:
        dirs.append(ws.root / "project" / "templates")
    dirs.append(Path(user_data_dir("videocli", appauthor=False)) / "templates")
    dirs.append(BUILTIN)
    return dirs


def load_manifest(folder: Path) -> Template:
    p = folder / "template.json"
    try:
        t = Template.model_validate(read_json(p))
    except (OSError, ValueError, ValidationError) as exc:
        raise InputError("TEMPLATE_INVALID", f"Template manifest {p} is invalid: {str(exc).splitlines()[0]}") from exc
    if t.baker not in BAKERS:
        raise InputError("TEMPLATE_INVALID", f"Template {t.name}: unknown baker {t.baker!r}.")
    t.folder = str(folder)
    return t


def discover(ws: Workspace | None) -> dict[str, Template]:
    found: dict[str, Template] = {}
    for d in reversed(search_dirs(ws)):  # project overrides user overrides built-in
        if not d.is_dir():
            continue
        for sub in sorted(d.iterdir()):
            if (sub / "template.json").is_file():
                t = load_manifest(sub)
                found[t.name] = t
    return found


def get(ws: Workspace | None, name: str) -> Template:
    ts = discover(ws)
    if name not in ts:
        raise UsageError("BAD_ARGUMENT", f"No template named {name!r}.", hint=f"Templates: {', '.join(sorted(ts))}")
    return ts[name]


# --------------------------------------------------------------------------- brand


def brand_path(ws: Workspace) -> Path:
    return ws.root / "project" / "brand.json"


def load_brand(ws: Workspace | None) -> Brand:
    if ws is None or not brand_path(ws).is_file():
        return Brand()
    try:
        return Brand.model_validate(read_json(brand_path(ws)))
    except (ValueError, ValidationError) as exc:
        raise InputError("SCHEMA_INVALID", f"brand.json is invalid: {exc}") from exc


def save_brand(ws: Workspace, b: Brand) -> Path:
    write_json(brand_path(ws), b.model_dump())
    return brand_path(ws)


def font_file(brand: Brand, bold: bool = False, text: str = "") -> str:
    if re.search(r"[ऀ-ॿ]", text):
        return str(FONTS / "NotoSansDevanagari-Regular.ttf")
    if bold and brand.font_bold:
        return brand.font_bold
    if brand.font:
        return brand.font
    return str(FONTS / ("NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf"))


# --------------------------------------------------------------------------- params


def parse_kv(items: list[str]) -> dict[str, str]:
    out = {}
    for it in items:
        if "=" not in it:
            raise UsageError("BAD_ARGUMENT", f"--param needs key=value (got {it!r}).")
        k, v = it.split("=", 1)
        out[k.strip()] = v
    return out


def resolve_params(t: Template, given: dict[str, Any], brand: Brand) -> dict[str, Any]:
    """Validate and complete parameters: types, required, limits, brand.* defaults. Fails before any bake."""
    unknown = set(given) - set(t.params)
    if unknown:
        raise UsageError("BAD_ARGUMENT", f"Template {t.name} has no parameter(s) {', '.join(sorted(unknown))}.",
                         hint=f"Parameters: {', '.join(t.params)}")
    out: dict[str, Any] = {}
    for name, spec in t.params.items():
        v = given.get(name, spec.default)
        if v is None:
            if spec.required:
                raise UsageError("BAD_ARGUMENT", f"Template {t.name} needs --param {name}=…")
            continue
        if isinstance(v, str) and v.startswith("brand."):
            key = v[len("brand."):]
            v = brand.logo if key == "logo" else brand.colors.get(key)
            if v is None:
                if spec.required:
                    raise UsageError("BAD_ARGUMENT", f"The brand kit has no {key}.", hint="Run: videocli brand set …")
                continue
        if spec.type == "number":
            try:
                v = float(v)
            except (TypeError, ValueError) as exc:
                raise UsageError("BAD_ARGUMENT", f"{t.name}.{name} must be a number.") from exc
            if (spec.min is not None and v < spec.min) or (spec.max is not None and v > spec.max):
                raise UsageError("BAD_ARGUMENT", f"{t.name}.{name}={v} is outside {spec.min}..{spec.max}.")
        elif spec.type == "color":
            if not COLOR_RE.match(str(v)):
                raise UsageError("BAD_ARGUMENT", f"{t.name}.{name} must be a #rrggbb colour (got {v!r}).")
        elif spec.type == "choice":
            if spec.choices and v not in spec.choices:
                raise UsageError("BAD_ARGUMENT", f"{t.name}.{name} must be one of {', '.join(spec.choices)}.")
        elif spec.type == "bool":
            v = str(v).lower() in ("1", "true", "yes", "on") if isinstance(v, str) else bool(v)
        elif spec.type == "file":
            if not Path(str(v)).is_file():
                raise UsageError("BAD_ARGUMENT", f"{t.name}.{name}: no file at {v}.")
            v = str(Path(str(v)).resolve())
        elif spec.type == "list":
            v = json.loads(v) if isinstance(v, str) and v.startswith("[") else v
        else:
            v = str(v)
        out[name] = v
    return out


def text_of(params: dict[str, Any]) -> str:
    return " ".join(str(params[k]) for k in ("title", "subtitle", "text", "label", "author") if params.get(k))
