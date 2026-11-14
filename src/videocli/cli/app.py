"""Root of the CLI. ``main()`` is the only place that catches exceptions and sets the exit status."""

from __future__ import annotations

import contextlib
import os
import sys
import traceback
from typing import Optional

import typer

from videocli import __version__
from videocli.cli.state import detect_json_mode, state

app = typer.Typer(
    name="videocli",
    help="A video editor for AI agents: facts as files, decisions as an EDL, rendered by ffmpeg and checked by the tool itself.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
    context_settings={"help_option_names": ["-h", "--help"]},
)

# Global options (SPEC §6.6). They may appear anywhere on the command line; main() moves
# them in front of the sub-command so ``videocli probe x --json`` works like ``videocli --json probe x``.
GLOBAL_FLAGS = {"--json", "--dry-run", "--estimate", "--force", "--quiet", "-q", "--verbose", "-v", "--version"}
GLOBAL_VALUED = {"--workspace", "--progress", "--jobs", "--provider"}


# Options whose value is optional (``--cut-silence`` alone or ``--cut-silence min=1.2``).
OPTIONAL_VALUE = {"--cut-silence", "--cut-fillers"}


def fill_optional_values(argv: list[str]) -> list[str]:
    out: list[str] = []
    for i, tok in enumerate(argv):
        if tok == "--":
            out += argv[i:]
            break
        if tok in OPTIONAL_VALUE:
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt is None or nxt.startswith("-") or "=" not in nxt:
                out.append(tok + "=")
                continue
        out.append(tok)
    return out


# Two-word spellings from the spec that map onto one command name.
ALIASES = {("captions", "style"): "captions-style"}


def apply_aliases(argv: list[str]) -> list[str]:
    words = [i for i, a in enumerate(argv) if not a.startswith("-")]
    if len(words) >= 2:
        i, j = words[0], words[1]
        if j == i + 1 and (argv[i], argv[j]) in ALIASES:
            return argv[:i] + [ALIASES[(argv[i], argv[j])]] + argv[j + 1:]
    return argv


def hoist_globals(argv: list[str]) -> list[str]:
    front: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            rest += argv[i:]
            break
        name = tok.split("=", 1)[0]
        if tok in GLOBAL_FLAGS:
            front.append(tok)
        elif name in GLOBAL_VALUED:
            if "=" in tok:
                front.append(tok)
            elif i + 1 < len(argv):
                front += [tok, argv[i + 1]]
                i += 1
            else:
                front.append(tok)
        else:
            rest.append(tok)
        i += 1
    return front + rest


def _version_cb(value: bool) -> None:
    if value:
        state.command = "version"
        from videocli.cli.output import emit

        if state.json:
            emit({"version": __version__})
        else:
            sys.stdout.write(f"videocli {__version__}\n")
        raise typer.Exit(0)


@app.callback()
def root(
    json_: bool = typer.Option(False, "--json", help="Print one JSON envelope on stdout (default when stdout is not a terminal)."),
    workspace: Optional[str] = typer.Option(None, "--workspace", metavar="PATH", help="Workspace folder (default: nearest .videocli)."),
    progress: str = typer.Option("auto", "--progress", metavar="MODE", help="Progress on stderr: auto, bar, json or none."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen without writing or rendering."),
    estimate: bool = typer.Option(False, "--estimate", help="Return expected time and disk use without doing the work."),
    force: bool = typer.Option(False, "--force", help="Redo cached work and allow overwriting outputs."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="No human output on success."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="More detail on stderr."),
    jobs: Optional[int] = typer.Option(None, "--jobs", metavar="N", min=1, max=64, help="Parallel workers."),
    provider: Optional[str] = typer.Option(None, "--provider", metavar="NAME", help="Model provider: agent, local or api."),
    version: bool = typer.Option(False, "--version", callback=_version_cb, is_eager=True, help="Print the version."),
) -> None:
    from videocli.core import progress as prog

    state.json = state.json or json_
    state.workspace_path = workspace
    state.progress = progress
    state.dry_run = dry_run
    state.estimate = estimate
    state.force = force
    state.quiet = quiet
    state.verbose = verbose
    state.jobs = jobs
    state.provider = provider
    if progress not in ("auto", "bar", "json", "none"):
        from videocli.core.errors import UsageError

        raise UsageError("BAD_ARGUMENT", f"--progress must be auto, bar, json or none (got {progress!r}).")
    prog.configure("none" if quiet and progress == "auto" else progress)


def _register() -> None:
    # Command modules import only typer and light helpers; domain code loads inside commands.
    from videocli.cli.commands import COMMAND_MODULES

    for mod in COMMAND_MODULES:
        mod.register(app)


_register()


def _guess_command(argv: list[str]) -> str:
    from videocli.cli.registry import META

    words = [a for a in argv if not a.startswith("-")]
    for n in (2, 1):
        cand = " ".join(words[:n])
        if cand in META:
            return cand
    return words[0] if words else "videocli"


def _internal_error(exc: BaseException) -> dict:
    from videocli.media import ffmpeg

    try:
        log = ffmpeg.new_log_path("internal-error")
        log.write_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), encoding="utf-8")
        log_s = str(log)
    except Exception:  # pragma: no cover
        log_s = None
    return {
        "code": "INTERNAL_ERROR",
        "message": f"{type(exc).__name__}: {exc}",
        "hint": "This is a bug in videocli. Please report it with the log file.",
        "retryable": False,
        "details": {"log": log_s},
    }


def _interrupted() -> None:
    from videocli.cli.output import emit_error

    if state.json:
        emit_error({"code": "INTERRUPTED", "message": "Interrupted.", "hint": "Re-run the same command to resume.",
                    "retryable": True, "details": {}})
    else:
        sys.stderr.write("\ninterrupted\n")


def _click_classes() -> dict[str, tuple[type, ...]]:
    """Exception classes from Click and from Typer's vendored copy of Click (typer >= 0.26)."""
    import importlib

    found: dict[str, list[type]] = {"usage": [], "noargs": [], "exit": [typer.Exit], "abort": [typer.Abort]}
    for modname in ("typer._click.exceptions", "click.exceptions"):
        try:
            m = importlib.import_module(modname)
        except ImportError:
            continue
        for key, attr in (("usage", "UsageError"), ("noargs", "NoArgsIsHelpError"), ("exit", "Exit"), ("abort", "Abort")):
            cls = getattr(m, attr, None)
            if cls is not None:
                found[key].append(cls)
    return {k: tuple(dict.fromkeys(v)) for k, v in found.items()}


def main(argv: list[str] | None = None) -> int:
    from videocli.cli.output import emit_error
    from videocli.core.errors import VideoCliError

    if sys.platform == "win32":  # pragma: no cover
        for stream in (sys.stdout, sys.stderr):
            with contextlib.suppress(AttributeError, ValueError):
                stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    raw = list(sys.argv[1:] if argv is None else argv)
    state.reset()
    state.argv = raw
    state.json = detect_json_mode(raw)
    args = apply_aliases(hoist_globals(fill_optional_values(raw)))
    state.command = _guess_command(args)
    code = 0
    try:
        rv = app(args=args, prog_name="videocli", standalone_mode=False)
        code = rv if isinstance(rv, int) else 0
        if code == 130:  # typer returns Exit(130) for KeyboardInterrupt in non-standalone mode
            _interrupted()
    except VideoCliError as exc:
        emit_error(exc.to_dict())
        code = exc.exit_code
    except KeyboardInterrupt:
        _interrupted()
        code = 130
    except BrokenPipeError:
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError:
            pass
        code = 0
    except Exception as exc:  # noqa: BLE001 - the single place that classifies failures
        k = _click_classes()
        if k["noargs"] and isinstance(exc, k["noargs"]):
            ctx = getattr(exc, "ctx", None)
            sys.stdout.write((ctx.get_help() if ctx else "") + "\n")
            code = 0
        elif isinstance(exc, k["usage"]):
            ctx = getattr(exc, "ctx", None)
            hint = f"Run: {ctx.command_path} --help" if ctx is not None else "Run: videocli --help"
            if state.json:
                emit_error({"code": "BAD_ARGUMENT", "message": exc.format_message(), "hint": hint, "retryable": False, "details": {}})  # type: ignore[attr-defined]
            else:
                exc.show()  # type: ignore[attr-defined]
            code = 2
        elif isinstance(exc, k["exit"]):
            code = int(getattr(exc, "exit_code", getattr(exc, "code", 0)) or 0)
            if code == 130:  # typer turns KeyboardInterrupt into Exit(130)
                _interrupted()
        elif isinstance(exc, k["abort"]):
            _interrupted()
            code = 130
        else:
            emit_error(_internal_error(exc))
            code = 1
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
