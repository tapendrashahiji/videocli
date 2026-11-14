"""The envelope (SPEC §6.1): one JSON document on stdout, or a human rendering.

Commands call :func:`emit`. Errors are rendered by :func:`emit_error`, which only
``cli.app.main`` calls.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from videocli import __version__
from videocli.cli.state import state

Formatter = Callable[[dict[str, Any]], None]
_formatters: dict[str, Formatter] = {}


def formatter(command: str) -> Callable[[Formatter], Formatter]:
    """Register a human renderer for ``command``."""

    def deco(fn: Formatter) -> Formatter:
        _formatters[command] = fn
        return fn

    return deco


def artifact(kind: str, path: Path | str) -> dict[str, str]:
    from videocli.core.fsutil import rel

    return {"kind": kind, "path": rel(path)}


def warning(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _norm_warnings(warnings: Iterable[Any] | None) -> list[dict[str, str]]:
    out = []
    for w in warnings or []:
        if isinstance(w, dict):
            out.append({"code": str(w.get("code")), "message": str(w.get("message"))})
        elif hasattr(w, "model_dump"):
            d = w.model_dump()
            out.append({"code": d["code"], "message": d["message"]})
        else:
            out.append({"code": getattr(w, "code", "WARNING"), "message": str(getattr(w, "message", w))})
    return out


def _write_stdout(text: str) -> None:
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except UnicodeEncodeError:  # pragma: no cover - legacy consoles
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.flush()


def envelope(
    data: Any = None,
    *,
    artifacts: Iterable[dict[str, str]] | None = None,
    warnings: Iterable[Any] | None = None,
    next: Iterable[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "command": state.command,
        "version": __version__,
        "data": {} if data is None else data,
        "artifacts": list(artifacts or []),
        "warnings": _norm_warnings(warnings),
        "next": list(next or []),
    }


def emit(
    data: Any = None,
    *,
    artifacts: Iterable[dict[str, str]] | None = None,
    warnings: Iterable[Any] | None = None,
    next: Iterable[str] | None = None,
) -> dict[str, Any]:
    env = envelope(data, artifacts=artifacts, warnings=warnings, next=next)
    if state.json:
        _write_stdout(json.dumps(env, ensure_ascii=False, allow_nan=False, default=str) + "\n")
    elif not state.quiet:
        render_human(env)
    return env


def error_envelope(err: dict[str, Any]) -> dict[str, Any]:
    return {"ok": False, "command": state.command, "version": __version__, "error": err}


def emit_error(err: dict[str, Any]) -> None:
    env = error_envelope(err)
    if state.json:
        _write_stdout(json.dumps(env, ensure_ascii=False, default=str) + "\n")
        return
    msg = f"error [{err['code']}]: {err['message']}\n"
    if err.get("hint"):
        msg += f"hint: {err['hint']}\n"
    details = err.get("details") or {}
    if details.get("log"):
        msg += f"log: {details['log']}\n"
    sys.stderr.write(msg)
    sys.stderr.flush()


# --------------------------------------------------------------------------- human


def console():
    from rich.console import Console

    return Console(soft_wrap=False, highlight=False)


def render_human(env: dict[str, Any]) -> None:
    fn = _formatters.get(env["command"])
    data = env["data"]
    if fn is not None:
        fn(data)
    else:
        generic(data)
    c = None
    if env["artifacts"]:
        c = c or console()
        for a in env["artifacts"]:
            c.print(f"[dim]wrote[/dim] {a['path']}  [dim]({a['kind']})[/dim]")
    if env["warnings"]:
        c = c or console()
        for w in env["warnings"]:
            c.print(f"[yellow]warning[/yellow] [{w['code']}] {w['message']}")
    if env["next"]:
        c = c or console()
        c.print("[dim]next:[/dim]")
        for n in env["next"]:
            c.print(f"  {n}")


def generic(data: Any) -> None:
    if data in (None, {}, []):
        return
    if isinstance(data, str):
        _write_stdout(data if data.endswith("\n") else data + "\n")
        return
    if isinstance(data, dict):
        # Readable "key  value" lines; nested values as compact JSON.
        import json as _json

        width = max((len(str(k)) for k in data), default=0)
        c = console()
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                text = _json.dumps(v, ensure_ascii=False, default=str)
                if len(text) > 160:
                    text = text[:157] + "…"
            else:
                text = "" if v is None else str(v)
            c.print(f"[bold]{str(k).ljust(width)}[/bold]  {text}", markup=True, highlight=False)
        return
    from rich.pretty import Pretty

    console().print(Pretty(data, expand_all=False, indent_guides=False))


def kv_table(rows: Iterable[tuple[str, Any]], title: str | None = None) -> None:
    from rich.table import Table

    t = Table(show_header=False, box=None, title=title, pad_edge=False)
    t.add_column(style="bold")
    t.add_column()
    for k, v in rows:
        t.add_row(str(k), "" if v is None else str(v))
    console().print(t)
