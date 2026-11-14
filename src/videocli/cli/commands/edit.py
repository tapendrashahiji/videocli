"""Planning and output commands: plan, edl …, render, captions (phase 2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import typer

from videocli.cli.commands.perception import q
from videocli.cli.output import artifact, console, emit, formatter
from videocli.cli.registry import command, sub_app
from videocli.cli.state import state


def _kv(spec: str, allowed: dict[str, float]) -> dict[str, float]:
    from videocli.core.errors import UsageError

    out = dict(allowed)
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        if "=" not in part:
            raise UsageError("BAD_ARGUMENT", f"Expected key=value in '{spec}'.")
        k, v = part.split("=", 1)
        k = k.strip()
        if k not in allowed:
            raise UsageError("BAD_ARGUMENT", f"Unknown key '{k}' in '{spec}'.", hint=f"Allowed: {', '.join(allowed)}")
        try:
            out[k] = float(v)
        except ValueError as exc:
            raise UsageError("BAD_ARGUMENT", f"'{k}' must be a number in '{spec}'.") from exc
    return out


def _transcripts(edl) -> dict[str, Any]:
    from videocli.core.cache import read_artifact

    ws = state.workspace()
    return {sid: read_artifact(ws.source_dir(sid) / "transcript.json") for sid in edl.sources}


def _load(name: str):
    from videocli.edl import io

    return io.load(state.workspace(), name)


def _save(edl, op: str, **details: Any):
    from videocli.edl import io

    if state.dry_run:
        return None
    return io.save(state.workspace(), edl, op, **details)


def _time(v: Optional[str]) -> Optional[float]:
    from videocli.core.timeutil import parse_time

    return None if v is None else parse_time(v)


def _op_emit(name: str, edl, summary: dict[str, Any], path) -> None:
    from videocli.edl import lint as lintmod

    res = lintmod.lint(edl, transcripts=_transcripts(edl), min_clip=state.settings.plan__min_clip,
                       min_cut=state.settings.plan__min_cut, check_files=False)
    summary["lint"] = {"passed": res["passed"], "errors": res["errors"], "warnings": res["warnings"]}
    summary["dry_run"] = state.dry_run
    nxt = [f"videocli edl lint {name}"] if not res["passed"] else [f"videocli render {name} --preview -o {name}-preview.mp4"]
    emit(summary, artifacts=[artifact("edl", path)] if path else [], next=nxt)


def register(app: typer.Typer) -> None:
    # ------------------------------------------------------------------ plan
    @command(app, "plan", summary="Write a new EDL from rule planners (silence, fillers, ranges); never renders", writes=True, dry_run=True, exit_codes=(0, 2, 4))
    def plan_cmd(
        file: str = typer.Argument(..., help="Media file or source id."),
        output: str = typer.Option(..., "-o", "--output", metavar="NAME", help="Name of the EDL to write."),
        cut_silence: Optional[str] = typer.Option(None, "--cut-silence", metavar="min=S,pad=S",
                                                  help="Remove silences ≥ min (default 1.0 s), keeping pad (0.15 s) each side."),
        cut_fillers: Optional[str] = typer.Option(None, "--cut-fillers", metavar="[words=a,b]",
                                                  help="Remove filler words (default list for the language, or words=um,uh)."),
        cut_range: list[str] = typer.Option([], "--cut-range", metavar="A-B", help="Remove a source range (repeatable)."),
        keep_only: list[str] = typer.Option([], "--keep-only", metavar="A-B", help="Keep only these ranges (repeatable)."),
        min_clip: Optional[float] = typer.Option(None, "--min-clip", metavar="S", min=0, help="Drop clips shorter than this."),
        min_cut: Optional[float] = typer.Option(None, "--min-cut", metavar="S", min=0, help="Ignore cuts shorter than this."),
        no_snap: bool = typer.Option(False, "--no-snap", help="Do not move boundaries to word edges."),
    ) -> None:
        """Plan an edit as an EDL file."""
        from videocli.core.errors import InputError
        from videocli.core.source import lookup
        from videocli.core.timeutil import parse_range
        from videocli.edl import io
        from videocli.edl.build import build_plan
        from videocli.edl.planners import PlanOptions

        ws = state.workspace()
        if io.exists(ws, output) and not state.force:
            raise InputError("EDL_EXISTS", f"An EDL named {output!r} already exists.", hint=f"Choose another -o name, or pass --force to replace it, or edit it with: videocli edl show {output}")
        s = state.settings
        fillers = None
        if cut_fillers is not None:
            fillers = []
            spec = cut_fillers.strip()
            if spec:
                if not spec.startswith("words="):
                    from videocli.core.errors import UsageError

                    raise UsageError("BAD_ARGUMENT", "--cut-fillers takes an optional words=a,b list.")
                fillers = [w.strip() for w in spec[len("words="):].replace(";", ",").split(",") if w.strip()]
        opts = PlanOptions(
            cut_silence=_kv(cut_silence, {"min": 1.0, "pad": 0.15}) if cut_silence is not None else None,
            cut_fillers=fillers,
            cut_ranges=[parse_range(r) for r in cut_range],
            keep_only=[parse_range(r) for r in keep_only],
            min_clip=s.plan__min_clip if min_clip is None else min_clip,
            min_cut=s.plan__min_cut if min_cut is None else min_cut,
            filler_pad=s.plan__filler_pad,
            snap=not no_snap,
        )
        src = lookup(ws, file)
        edl, info, warnings = build_plan(ws, src, output, opts, need_transcript=fillers is not None)
        path = None
        if state.dry_run:
            io.snap(edl)
        else:
            path = io.save(ws, edl, "plan", args={k: v for k, v in {
                "cut_silence": opts.cut_silence, "cut_fillers": fillers, "cut_range": cut_range or None,
                "keep_only": keep_only or None, "min_clip": opts.min_clip, "min_cut": opts.min_cut, "snap": opts.snap}.items() if v is not None})
            edl = io.load(ws, output)
        from videocli.edl import ops

        data = ops.show(edl)
        data["plan"] = info
        data["dry_run"] = state.dry_run
        emit(data, artifacts=[artifact("edl", path)] if path else [], warnings=warnings,
             next=[f"videocli edl lint {output}", f"videocli edl show {output} --clips --limit 20", f"videocli render {output} -o {output}.mp4"])

    # ------------------------------------------------------------------ edl group
    edl_app = sub_app("edl", "Inspect and change an EDL. Times in cut, keep and restore are source times.")
    app.add_typer(edl_app)

    @command(edl_app, "show", group="edl", summary="Summary of an EDL; --clips lists spine clips with source and output times", exit_codes=(0, 4))
    def edl_show(name: str = typer.Argument(...), clips: bool = typer.Option(False, "--clips"), limit: Optional[int] = typer.Option(None, "--limit", min=1)) -> None:
        from videocli.edl import ops

        edl = _load(name)
        data = ops.show(edl, with_clips=clips, limit=limit or state.settings.list_limit)
        nxt = [f"videocli edl lint {name}"]
        if clips and data.get("truncated"):
            nxt.append(f"videocli edl show {name} --clips --limit {data['total']}")
        emit(data, next=nxt)

    @formatter("edl show")
    def _fmt_show(d: dict[str, Any]) -> None:
        c = console()
        c.print(f"[bold]{d['name']}[/bold]  {d['rate']}  {d['resolution'][0]}x{d['resolution'][1]}  clips={d['tracks']['main']}")
        c.print(f"  source {d['source_duration']:.2f}s → output {d['output_duration']:.2f}s  (removed {d['removed_seconds']:.2f}s, {d['removed_percent']}%)")
        if d["by_reason"]:
            c.print("  removed by reason: " + ", ".join(f"{k} {v:.1f}s" for k, v in d["by_reason"].items()))
        for cl in d.get("clips", []):
            c.print(f"  {cl['id']}  src {cl['start']:.2f}-{cl['end']:.2f}  out {cl['out_start']:.2f}-{cl['out_end']:.2f}  {cl.get('reason') or ''}{' [locked]' if cl.get('locked') else ''}")

    def range_op(opname: str):
        def run(name: str, frm: str, to: str, reason: Optional[str], source: Optional[str]) -> None:
            from videocli.edl import ops

            edl = _load(name)
            a, b = _time(frm), _time(to)
            if opname == "cut":
                summ = ops.cut(edl, a, b, source=source, reason=reason or "manual")  # type: ignore[arg-type]
            elif opname == "keep":
                summ = ops.keep(edl, a, b, source=source, reason=reason or "manual")  # type: ignore[arg-type]
            else:
                summ = ops.restore(edl, a, b, source=source)  # type: ignore[arg-type]
            path = _save(edl, opname, **{"from": a, "to": b, "reason": reason, "source": source})
            _op_emit(name, edl, summ, path)

        return run

    for opname, summary in (("cut", "Remove a source-time range from the spine"),
                            ("keep", "Make sure a source-time range is in the spine"),
                            ("restore", "Bring back a previously removed source-time range")):
        runner = range_op(opname)

        def make(runner=runner, opname=opname):
            def fn(
                name: str = typer.Argument(..., help="EDL name."),
                frm: str = typer.Option(..., "--from", metavar="T", help="Source time."),
                to: str = typer.Option(..., "--to", metavar="T", help="Source time."),
                reason: Optional[str] = typer.Option(None, "--reason", metavar="TEXT"),
                source: Optional[str] = typer.Option(None, "--source", metavar="ID", help="Needed when the EDL has several sources."),
            ) -> None:
                runner(name, frm, to, reason, source)

            fn.__doc__ = summary
            fn.__name__ = f"edl_{opname}"
            return fn

        command(edl_app, opname, group="edl", summary=summary, writes=True, dry_run=True, exit_codes=(0, 2, 4))(make())

    @command(edl_app, "trim", group="edl", summary="Set a clip's source start and/or end", writes=True, dry_run=True, exit_codes=(0, 2, 4))
    def edl_trim(name: str = typer.Argument(...), clip_id: str = typer.Argument(..., metavar="CLIP_ID"),
                 start: Optional[str] = typer.Option(None, "--start", metavar="T", help="New source start."),
                 end: Optional[str] = typer.Option(None, "--end", metavar="T", help="New source end.")) -> None:
        from videocli.edl import ops

        edl = _load(name)
        summ = ops.trim(edl, clip_id, start=_time(start), end=_time(end))
        _op_emit(name, edl, summ, _save(edl, "trim", clip=clip_id, start=_time(start), end=_time(end)))

    @command(edl_app, "split", group="edl", summary="Split a clip at a source time; the second part gets a new id", writes=True, dry_run=True, exit_codes=(0, 2, 4))
    def edl_split(name: str = typer.Argument(...), clip_id: str = typer.Argument(..., metavar="CLIP_ID"),
                  at: str = typer.Option(..., "--at", metavar="T", help="Source time inside the clip.")) -> None:
        from videocli.edl import ops

        edl = _load(name)
        summ = ops.split(edl, clip_id, _time(at))  # type: ignore[arg-type]
        _op_emit(name, edl, summ, _save(edl, "split", clip=clip_id, at=_time(at)))

    @command(edl_app, "move", group="edl", summary="Move a clip before or after another (or to start/end)", writes=True, dry_run=True, exit_codes=(0, 2, 4))
    def edl_move(name: str = typer.Argument(...), clip_id: str = typer.Argument(..., metavar="CLIP_ID"),
                 before: Optional[str] = typer.Option(None, "--before", metavar="CLIP_ID|start"),
                 after: Optional[str] = typer.Option(None, "--after", metavar="CLIP_ID|end")) -> None:
        from videocli.edl import ops

        edl = _load(name)
        summ = ops.move(edl, clip_id, before=before, after=after)
        _op_emit(name, edl, summ, _save(edl, "move", clip=clip_id, before=before, after=after))

    @command(edl_app, "lock", group="edl", summary="Protect an item from automatic planners (--unlock to release)", writes=True, dry_run=True, exit_codes=(0, 4))
    def edl_lock(name: str = typer.Argument(...), item_id: str = typer.Argument(..., metavar="ITEM_ID"),
                 unlock: bool = typer.Option(False, "--unlock")) -> None:
        from videocli.edl import ops

        edl = _load(name)
        summ = ops.lock(edl, item_id, not unlock)
        path = _save(edl, "unlock" if unlock else "lock", item=item_id)
        emit({**summ, "dry_run": state.dry_run}, artifacts=[artifact("edl", path)] if path else [])

    @command(edl_app, "lint", group="edl", summary="Check an EDL: clipped words, tiny clips and cuts, overlaps, bounds, changed sources, orphan anchors", exit_codes=(0, 4, 5))
    def edl_lint(name: str = typer.Argument(...)) -> None:
        from videocli.core.errors import CheckFailed
        from videocli.edl import lint as lintmod

        edl = _load(name)
        res = lintmod.lint(edl, transcripts=_transcripts(edl), min_clip=state.settings.plan__min_clip, min_cut=state.settings.plan__min_cut)
        if not res["passed"]:
            first_fix = next((i["fix"] for i in res["issues"] if i.get("fix") and i["severity"] == "error"), None)
            raise CheckFailed("LINT_FAILED", f"EDL {name} has {res['errors']} lint error(s).",
                              hint=(f"Run: {first_fix}" if first_fix else "See details.issues"), details=res)
        emit(res, next=[f"videocli render {name} --preview -o {name}-preview.mp4", f"videocli render {name} -o {name}.mp4"])

    @formatter("edl lint")
    def _fmt_lint(d: dict[str, Any]) -> None:
        c = console()
        c.print(f"{d['name']}: {'[green]passed[/green]' if d['passed'] else '[red]failed[/red]'}  ({d['errors']} errors, {d['warnings']} warnings)")
        for i in d["issues"]:
            c.print(f"  {i['severity']:5} {i['rule']}  {i.get('clip') or i.get('item') or i.get('clips') or ''}  {i.get('fix') or ''}")

    @command(edl_app, "diff", group="edl", summary="Compare two EDLs clip by clip", exit_codes=(0, 4))
    def edl_diff(a: str = typer.Argument(...), b: str = typer.Argument(...)) -> None:
        from videocli.edl import ops

        emit(ops.diff(_load(a), _load(b)))

    @command(edl_app, "copy", group="edl", summary="Copy an EDL to a new name (a revision to experiment on)", writes=True, dry_run=True, exit_codes=(0, 4))
    def edl_copy(a: str = typer.Argument(...), b: str = typer.Argument(...)) -> None:
        from videocli.core.errors import InputError
        from videocli.edl import io

        ws = state.workspace()
        if io.exists(ws, b) and not state.force:
            raise InputError("EDL_EXISTS", f"An EDL named {b!r} already exists.", hint="Pass --force to replace it.")
        edl = _load(a)
        edl.name = b
        path = _save(edl, "copy", **{"from": a})
        emit({"from": a, "to": b, "dry_run": state.dry_run}, artifacts=[artifact("edl", path)] if path else [], next=[f"videocli edl show {b}"])

    # ------------------------------------------------------------------ render
    @command(app, "render", summary="Render an EDL with the cached render graph (resumable)", writes=True, dry_run=True, estimate=True, exit_codes=(0, 1, 2, 3, 4))
    def render_cmd(
        name: str = typer.Argument(..., help="EDL name."),
        output: str = typer.Option(..., "-o", "--output", metavar="OUT", help="Output file (.mp4, .mov or .mkv)."),
        preset: Optional[str] = typer.Option(None, "--preset", help="source, 1080p, 720p or preview (default: the EDL's output.preset)."),
        preview: bool = typer.Option(False, "--preview", help="Same graph at the preview preset (480p, fast)."),
        around_cuts: Optional[float] = typer.Option(None, "--around-cuts", metavar="S", help="Only S seconds each side of every join, as one labelled reel."),
        burn_captions: bool = typer.Option(False, "--burn-captions", help="Burn captions re-timed from the transcript."),
        no_normalize: bool = typer.Option(False, "--no-normalize", help="Skip loudness normalization."),
    ) -> None:
        """Render an EDL to a file."""
        from videocli.render.run import render

        edl = _load(name)
        out = Path(output)
        res = render(
            state.workspace(), edl, out,
            preset_name="preview" if preview else (preset or edl.output.preset),
            settings=state.settings, jobs=int(state.settings.jobs), burn_captions=burn_captions,
            normalize=not no_normalize, around_cuts=around_cuts,
            transcripts=_transcripts(edl) if burn_captions else None, argv=state.argv,
            dry_run=state.dry_run, want_estimate=state.estimate,
        )
        if state.estimate or state.dry_run:
            emit(res, next=[f"videocli render {name} -o {q(output)}"])
            return
        nxt = [f"videocli verify {q(output)} --edl {name}"]
        if around_cuts is None:
            nxt.append(f"videocli captions {name} -o {q(out.with_suffix('.srt'))}")
        emit(res, artifacts=[artifact("video", out)], next=nxt)

    @formatter("render")
    def _fmt_render(d: dict[str, Any]) -> None:
        c = console()
        if d.get("estimate"):
            e = d["estimate"]
            c.print(f"estimate: {e['output_duration']:.1f}s output, ~{e['seconds']:.0f}s to render, ~{e['disk_bytes'] / 1e6:.0f} MB, {e['cached']}/{e['nodes']} nodes cached")
        elif d.get("dry_run"):
            for n in d["nodes"]:
                c.print(f"  {'cached' if n['cached'] else 'run   '}  {n['kind']:8} {n['label']}")
        else:
            c.print(f"rendered {d['output']}  {d['output_duration']:.2f}s  {d['size'][0]}x{d['size'][1]}  nodes ran {d['nodes']['ran']}/{d['nodes']['total']}  in {d['seconds']:.1f}s")

    # ------------------------------------------------------------------ captions
    @command(app, "captions", summary="Write SRT or VTT captions re-timed to the edit", writes=True, dry_run=True, exit_codes=(0, 2, 4))
    def captions_cmd(
        name: str = typer.Argument(..., help="EDL name."),
        output: str = typer.Option(..., "-o", "--output", metavar="OUT.srt|OUT.vtt"),
        max_chars: Optional[int] = typer.Option(None, "--max-chars", min=10, max=120),
        max_lines: Optional[int] = typer.Option(None, "--max-lines", min=1, max=4),
    ) -> None:
        """Captions for the rendered edit, from the transcript."""
        from videocli.core.errors import InputError, UsageError
        from videocli.core.fsutil import atomic_write_text
        from videocli.index.views import srt, vtt
        from videocli.render import captions as capmod

        out = Path(output)
        if out.suffix.lower() not in (".srt", ".vtt"):
            raise UsageError("BAD_ARGUMENT", "Output must end in .srt or .vtt.")
        edl = _load(name)
        trs = _transcripts(edl)
        if not any(trs.values()):
            raise InputError("ARTIFACT_MISSING", f"No transcript for the sources of {name}.", hint="Run: videocli analyze FILE")
        cues = capmod.build(edl, trs, max_chars=max_chars, max_lines=max_lines)
        text = vtt(cues) if out.suffix.lower() == ".vtt" else srt(cues)
        if not state.dry_run:
            atomic_write_text(out, text)
        emit({"output": str(out), "cues": len(cues), "words": sum(len(c[2].split()) for c in cues),
              "output_duration": round(edl.output_duration(), 3), "dry_run": state.dry_run},
             artifacts=[artifact("captions", out)] if not state.dry_run else [])
