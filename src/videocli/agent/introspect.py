"""``videocli commands``: the interface described from the Typer app itself, so it cannot drift."""

from __future__ import annotations

from typing import Any

import typer

_TYPES = {str: "text", int: "integer", float: "number", bool: "flag"}


def _type_name(p: Any) -> str:
    """Click exposes ``p.type``; typer >= 0.26 exposes ``p.type_descriptor.annotation``."""
    t = getattr(p, "type", None)
    tname = getattr(t, "name", None)
    if isinstance(tname, str) and tname:
        return {"string": "text", "float": "number", "boolean": "flag"}.get(tname, tname)
    ann: Any = getattr(getattr(p, "type_descriptor", None), "annotation", None)
    origin = getattr(ann, "__origin__", None)
    if origin is list:
        ann = ann.__args__[0]
    args = [a for a in getattr(ann, "__args__", ()) if a is not type(None)]
    if args and origin is not list:
        ann = args[0]
    return _TYPES.get(ann, "text")


def _param(p: Any) -> dict[str, Any]:
    kind = "argument" if getattr(p, "param_type_name", "option") == "argument" else "option"
    t = _type_name(p)
    metavar = getattr(p, "metavar", None)
    if metavar in ("T", "T1,T2,...", "OUT_T"):
        t = "time"
    elif metavar == "A-B":
        t = "range"
    out: dict[str, Any] = {"name": (p.opts[0] if kind == "option" else p.name.upper()), "type": t, "required": bool(p.required)}
    if kind == "option":
        if len(p.opts) > 1 or p.secondary_opts:
            out["aliases"] = list(p.opts[1:]) + list(p.secondary_opts)
        if getattr(p, "is_flag", False):
            out["type"] = "flag"
        if getattr(p, "multiple", False):
            out["repeatable"] = True
        if p.default not in (None, False, (), []) and not callable(p.default):
            out["default"] = p.default
    if metavar:
        out["metavar"] = metavar
    help_ = getattr(p, "help", None)
    if help_:
        out["help"] = help_
    return out


def describe(app: typer.Typer, name: str | None = None) -> dict[str, Any]:
    from videocli.cli.registry import META
    from videocli.core.errors import ERROR_CODES, UsageError

    root = typer.main.get_command(app)
    out: list[dict[str, Any]] = []

    def walk(cmd: Any, prefix: str) -> None:
        subs = getattr(cmd, "commands", None)
        if subs:
            for sub_name, sub in sorted(subs.items()):
                walk(sub, f"{prefix} {sub_name}".strip())
            return
        if prefix.startswith("_") or prefix not in META:
            return
        meta = META[prefix]
        params = [p for p in cmd.params if p.name not in ("help",)]
        out.append({
            "name": prefix,
            "summary": meta.summary,
            "args": [_param(p) for p in params if getattr(p, "param_type_name", "") == "argument"],
            "options": [_param(p) for p in params if getattr(p, "param_type_name", "") == "option"],
            "writes": meta.writes,
            "dry_run": meta.dry_run,
            "estimate": meta.estimate,
            "exit_codes": list(meta.exit_codes),
        })

    walk(root, "")
    if name:
        out = [c for c in out if c["name"] == name]
        if not out:
            raise UsageError("BAD_ARGUMENT", f"No command named {name!r}.", hint="Run: videocli commands")
    glob = [_param(p) for p in root.params if p.name != "help"]
    return {"commands": out, "global_options": glob, "error_codes": dict(ERROR_CODES), "total": len(out)}
