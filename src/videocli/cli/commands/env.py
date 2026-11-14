"""Environment commands: doctor, setup, config, cache, jobs (phase 0)."""

from __future__ import annotations

from typing import Any, Optional

import typer

from videocli.cli.output import console, emit, formatter, kv_table
from videocli.cli.registry import command, sub_app
from videocli.cli.state import state


def register(app: typer.Typer) -> None:
    # ------------------------------------------------------------------ doctor
    @command(app, "doctor", summary="Report installed tools, groups, models and the commands that fix what is missing", exit_codes=(0,))
    def doctor() -> None:
        """Report what is installed and what would make videocli ready."""
        from videocli.setup.doctor import report

        data = report(model=state.settings.transcript__model)
        warnings = data.pop("_warnings", [])
        nxt = data["fix"] or ["videocli probe FILE"]
        emit(data, warnings=warnings, next=nxt)

    @formatter("doctor")
    def _fmt_doctor(d: dict[str, Any]) -> None:
        ok = lambda b: "[green]ok[/green]" if b else "[red]missing[/red]"  # noqa: E731
        c = console()
        c.print(f"[bold]videocli doctor[/bold]  python {d['python']}  {d['platform']}")
        ff, fp = d["ffmpeg"], d["ffprobe"]
        c.print(f"  ffmpeg   {ok(ff['ok'])}  {ff.get('version') or ''}  {ff.get('path') or ff.get('message', '')}")
        if ff.get("missing_features"):
            c.print(f"           missing: {', '.join(ff['missing_features'])}")
        c.print(f"  ffprobe  {ok(fp['ok'])}  {fp.get('version') or ''}  {fp.get('path') or fp.get('message', '')}")
        c.print("  groups   " + "  ".join(f"{n}:{'✓' if v else '·'}" for n, v in d["groups"].items()))
        c.print("  models   " + "  ".join(f"{n}:{'✓' if v else '·'}" for n, v in d["models"].items()))
        c.print(f"  gpu      cuda={'yes' if d['gpu']['cuda'] else 'no'}")
        c.print(f"  ready    {'[green]yes[/green]' if d['ready'] else '[yellow]no[/yellow]'}")

    # ------------------------------------------------------------------ setup
    @command(
        app, "setup", summary="Install a profile's dependency groups and models", writes=True, dry_run=True, exit_codes=(0, 1, 2, 3),
    )
    def setup(
        profile: Optional[str] = typer.Option(None, "--profile", help="lecture, finishing, artistic or full."),
        with_: Optional[str] = typer.Option(None, "--with", metavar="LIST", help="Extra groups, comma-separated."),
        offline_check: bool = typer.Option(False, "--offline-check", help="Only report what is missing; download nothing."),
        restore: bool = typer.Option(False, "--restore", help="Reinstall the groups setup added before (after reinstalling videocli)."),
    ) -> None:
        """Install dependency groups and models (asks for a profile on a terminal)."""
        import sys

        from videocli.setup import install, profiles

        extra = [g.strip() for g in (with_ or "").split(",") if g.strip()]
        if restore:
            extra += profiles.recorded_groups()
            profile = profile or "lecture"
        asked = False
        if profile is None and not extra:
            if not state.json and sys.stdin.isatty() and not offline_check and not state.dry_run:
                profile = install.ask_profile()
                asked = profile is not None
            profile = profile or "lecture"
        groups = profiles.resolve(profile, extra)
        model = state.settings.transcript__model
        actions = install.plan(groups, model)
        warnings = []
        if offline_check or state.dry_run:
            results = [{**a, "result": "planned" if a.get("status") != "blocked" else "blocked"} for a in actions]
        else:
            results = install.execute(actions, model)
            profiles.record_groups(groups)
        for r in results:
            if r.get("result") == "blocked":
                warnings.append({"code": "SETUP_BLOCKED", "message": f"{r.get('message')} {r.get('hint') or ''}".strip()})
        status = {g: profiles.check_group(g, deep=False).ok for g in groups}
        not_selected = [g for g in profiles.GROUPS if g not in groups]
        nxt = ["videocli doctor"]
        if not_selected:
            nxt.append(f"videocli setup --with {','.join(not_selected[:3])}   # add more groups later")
        emit(
            {
                "profile": profile,
                "asked": asked,
                "groups": groups,
                "group_status": status,
                "actions": [{k: v for k, v in r.items() if k != "command"} | ({"command": " ".join(r["command"])} if r.get("command") else {}) for r in results],
                "offline_check": offline_check,
                "dry_run": state.dry_run,
                "reinstall_command": profiles.reinstall_command(),
            },
            warnings=warnings,
            next=nxt,
        )

    # ------------------------------------------------------------------ keys
    keys_app = sub_app("keys", "API keys for optional cloud services (typed by a person; never shown).")
    app.add_typer(keys_app)

    @command(keys_app, "status", group="keys", summary="Which API keys are set and where (never the key itself)", exit_codes=(0,))
    def keys_status() -> None:
        from videocli.setup import keys

        rows = keys.status()
        nxt = [f"videocli keys set {r['service']}   # a person runs this; input is hidden" for r in rows if not r["set"]]
        emit({"keys": rows}, next=nxt)

    @formatter("keys status")
    def _fmt_keys(d: dict[str, Any]) -> None:
        c = console()
        for r in d["keys"]:
            where = f"set ({r['source']})" if r["set"] else "not set"
            c.print(f"  {r['service']:12} {where:20} env {r['env']}")

    @command(keys_app, "set", group="keys", summary="Type an API key at a terminal (hidden) and store it in the project .env (gitignored) or --user", writes=True, dry_run=False, exit_codes=(0, 2))
    def keys_set(service: str = typer.Argument(..., help="elevenlabs"),
                 user: bool = typer.Option(False, "--user", help="Store for every project (user config folder) instead of this project.")) -> None:
        """Ask for an API key with hidden input. Refuses when not run by a person at a terminal."""
        from videocli.setup import keys

        res = keys.set_interactive(service, "user" if user else "project")
        emit(res, next=[f"videocli analyze FILE --backend {service} --force"])

    @command(keys_app, "remove", group="keys", summary="Delete a stored API key", writes=True, dry_run=False, exit_codes=(0, 2))
    def keys_remove(service: str = typer.Argument(...), user: bool = typer.Option(False, "--user")) -> None:
        from videocli.setup import keys

        emit(keys.remove(service, "user" if user else "project"))

    # ------------------------------------------------------------------ config
    cfg = sub_app("config", "Show or change settings.")
    app.add_typer(cfg)

    @command(cfg, "show", group="config", summary="Print effective settings and where each value came from", exit_codes=(0, 2))
    def config_show(key: Optional[str] = typer.Argument(None, help="Show only this key.")) -> None:
        from videocli.core import config

        loaded = state.settings
        rows = []
        for d in config.describe():
            if key and d["key"] != key:
                continue
            rows.append({"key": d["key"], "value": loaded.get(d["key"]), "source": loaded.sources[d["key"]], "env": d["env"], "description": d["description"]})
        if key and not rows:
            from videocli.core.errors import UsageError

            raise UsageError("BAD_ARGUMENT", f"Unknown setting '{key}'.")
        from videocli.core.workspace import resolve_root

        emit({"settings": rows, "files": {"project": str(resolve_root(state.workspace_path) / "config.toml"), "user": str(config.user_config_path())}})

    @formatter("config show")
    def _fmt_cfg(d: dict[str, Any]) -> None:
        from rich.table import Table

        t = Table(box=None)
        for col in ("key", "value", "source"):
            t.add_column(col)
        for r in d["settings"]:
            t.add_row(r["key"], repr(r["value"]), r["source"])
        console().print(t)

    @command(cfg, "set", group="config", summary="Set a setting in the project (or user) config file", writes=True, dry_run=True, exit_codes=(0, 2),
             context_settings={"ignore_unknown_options": True})
    def config_set(
        key: str = typer.Argument(..., help="Dotted key, e.g. silence.threshold_db"),
        value: str = typer.Argument(..., help="New value"),
        user: bool = typer.Option(False, "--user", help="Write the user file instead of the project file."),
    ) -> None:
        from videocli.core import config

        typed = config.coerce_value(key, value)
        path = config.user_config_path() if user else state.workspace().config_file
        current = config.read_toml(path)
        current[key] = typed
        if not state.dry_run:
            config.write_toml(path, current)
        emit({"key": key, "value": typed, "file": str(path), "dry_run": state.dry_run}, next=[f"videocli config show {key}"])

    # ------------------------------------------------------------------ cache
    cache = sub_app("cache", "Inspect and free workspace caches.")
    app.add_typer(cache)

    @command(cache, "status", group="cache", summary="Disk use of each cache area", exit_codes=(0,))
    def cache_status() -> None:
        from videocli.core.maintenance import cache_status as status

        ws = state.existing_workspace()
        emit(status(ws, state.settings.cache__max_gb), next=["videocli cache prune --older-than 30d"])

    @formatter("cache status")
    def _fmt_cache(d: dict[str, Any]) -> None:
        from videocli.core.fsutil import human_bytes

        kv_table([(a["area"], f"{human_bytes(a['bytes'])}  ({a['entries']} entries)") for a in d["areas"]], title=d.get("workspace") or "no workspace")
        console().print(f"total {human_bytes(d['total_bytes'])}")

    @command(cache, "prune", group="cache", summary="Remove cache entries unused for a while, then least-recently-used over the size limit", writes=True, dry_run=True, exit_codes=(0, 2))
    def cache_prune(older_than: Optional[str] = typer.Option(None, "--older-than", help="e.g. 30d, 12h")) -> None:
        from videocli.core.maintenance import cache_prune as prune
        from videocli.core.timeutil import parse_duration_spec

        ws = state.existing_workspace()
        secs = parse_duration_spec(older_than) if older_than else None
        emit(prune(ws, secs, state.settings.cache__max_gb, dry_run=state.dry_run))

    @command(cache, "clear", group="cache", summary="Remove all caches (render nodes, bakes, LUTs, analysis artifacts, logs); keeps edits and project files", writes=True, dry_run=True, exit_codes=(0,))
    def cache_clear() -> None:
        from videocli.core.maintenance import cache_clear as clear

        emit(clear(state.existing_workspace(), dry_run=state.dry_run), next=["videocli cache status"])

    # ------------------------------------------------------------------ jobs
    @command(app, "jobs", summary="List unfinished long-running jobs (interrupted renders resume when re-run)", exit_codes=(0,))
    def jobs() -> None:
        from videocli.core import jobs as jobmod

        ws = state.existing_workspace()
        items = jobmod.unfinished(ws) if ws else []
        nxt = [" ".join(["videocli", *j["argv"]]) for j in items if j.get("status") == "interrupted"][:5]
        emit({"total": len(items), "jobs": items}, next=nxt)
