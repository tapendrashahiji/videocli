"""Self-description: agent-guide, skill install, schema, commands (phase 4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import typer

from videocli.cli.output import artifact, console, emit, formatter
from videocli.cli.registry import command, sub_app
from videocli.cli.state import state


def register(app: typer.Typer) -> None:
    @command(app, "agent-guide", summary="Print the packaged agent skill (or one topic of it)", exit_codes=(0, 2))
    def agent_guide(topic: str = typer.Option("core", "--topic", help="core, or a later topic (see data.topics).")) -> None:
        """The agent skill text."""
        from videocli.agent import guide

        emit({"topic": topic, "topics": guide.topics(), "text": guide.text(topic)}, next=["videocli skill install"])

    @formatter("agent-guide")
    def _fmt_guide(d: dict[str, Any]) -> None:
        import sys

        sys.stdout.write(d["text"])

    skill = sub_app("skill", "Install the agent skill where agents find it.")
    app.add_typer(skill)

    @command(skill, "install", group="skill", summary="Write the skill to .claude/skills/videocli/SKILL.md (or an AGENTS.md section)", writes=True, dry_run=True, exit_codes=(0, 2))
    def skill_install(
        target: str = typer.Option("claude", "--target", help="claude or generic (AGENTS.md)."),
        scope: str = typer.Option("project", "--scope", help="project (current folder) or user (home)."),
        dir_: Optional[str] = typer.Option(None, "--dir", metavar="PATH", help="Install under this folder instead."),
    ) -> None:
        """Install the skill (idempotent)."""
        from videocli.agent import guide

        res = guide.install(target, scope, Path(dir_) if dir_ else None, dry_run=state.dry_run)
        emit(res, artifacts=[] if state.dry_run else [artifact("skill", res["path"])], next=["videocli doctor"])

    @command(app, "schema", summary="JSON Schema for the envelope, a file type, or a command's data", exit_codes=(0, 2))
    def schema(
        name: Optional[str] = typer.Argument(None, help="e.g. envelope, edl, probe, verify, 'command:render'. Omit to list."),
        all_: bool = typer.Option(False, "--all", help="Every schema."),
        out_dir: Optional[str] = typer.Option(None, "-o", "--output", metavar="DIR", help="With --all: write one file per schema."),
    ) -> None:
        """Export schemas."""
        from videocli.agent import schemas
        from videocli.core.fsutil import write_json

        names = list(schemas.models())
        if all_:
            docs = {n: schemas.json_schema(n) for n in names}
            if out_dir:
                d = Path(out_dir)
                arts = []
                for n, sch in docs.items():
                    p = d / (n.replace("command:", "command-").replace(" ", "-") + ".schema.json")
                    write_json(p, sch)
                    arts.append(artifact("schema", p))
                emit({"total": len(docs), "dir": str(d)}, artifacts=arts)
            else:
                emit({"total": len(docs), "schemas": docs})
            return
        if name is None:
            emit({"total": len(names), "names": names}, next=["videocli schema edl"])
            return
        emit({"name": name, "schema": schemas.json_schema(name)})

    @command(app, "commands", summary="Every command with its arguments, options, exit codes, and the error-code registry", exit_codes=(0, 2))
    def commands(name: Optional[str] = typer.Option(None, "--name", metavar="CMD", help="Only this command, e.g. 'edl cut'.")) -> None:
        """Describe the CLI."""
        from videocli.agent.introspect import describe
        from videocli.cli.app import app as root

        emit(describe(root, name))

    @formatter("commands")
    def _fmt_cmds(d: dict[str, Any]) -> None:
        c = console()
        for cmd in d["commands"]:
            c.print(f"[bold]{cmd['name']:16}[/bold] {cmd['summary']}")
