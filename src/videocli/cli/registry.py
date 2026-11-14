"""Command registration with metadata used by ``videocli commands`` and the global-option rules."""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import typer

from videocli.cli.state import state


@dataclass
class CommandMeta:
    name: str
    summary: str
    writes: bool = False
    dry_run: bool = False  # supports --dry-run
    estimate: bool = False  # supports --estimate
    exit_codes: tuple[int, ...] = (0, 1, 2, 3, 4)
    phase: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


META: dict[str, CommandMeta] = {}


def command(
    app: typer.Typer,
    name: str,
    *,
    group: str | None = None,
    summary: str,
    writes: bool = False,
    dry_run: bool | None = None,
    estimate: bool = False,
    exit_codes: tuple[int, ...] = (0, 1, 2, 3, 4),
    phase: int = 0,
    **typer_kwargs: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register ``fn`` as ``group name`` with metadata, setting ``state.command`` on entry."""
    full = f"{group} {name}" if group else name
    meta = CommandMeta(
        full, summary, writes, (not writes) if dry_run is None else dry_run, estimate, tuple(sorted(set(exit_codes))), phase
    )
    META[full] = meta

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            from videocli.core.errors import UsageError

            state.command = full
            if state.estimate and not meta.estimate:
                raise UsageError("NOT_SUPPORTED", f"'{full}' does not support --estimate.")
            if state.dry_run and meta.writes and not meta.dry_run:
                raise UsageError("NOT_SUPPORTED", f"'{full}' does not support --dry-run.")
            return fn(*args, **kwargs)

        app.command(name, help=typer_kwargs.pop("help", None) or (fn.__doc__ or summary), **typer_kwargs)(wrapper)
        return fn

    return deco


def sub_app(name: str, help: str) -> typer.Typer:
    return typer.Typer(name=name, help=help, no_args_is_help=True, add_completion=False)
