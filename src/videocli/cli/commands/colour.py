"""Colour commands (phase 7): scopes, grade input|set|auto|match|look|show|preview|reset, lut build."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import typer

from videocli.cli.commands.edit import _load, _save
from videocli.cli.output import artifact, console, emit, formatter
from videocli.cli.registry import command, sub_app
from videocli.cli.state import state


def _emit_edl(name: str, data: dict[str, Any], path, nxt: list[str] | None = None, warnings=None) -> None:
    emit({**data, "dry_run": state.dry_run}, artifacts=[artifact("edl", path)] if path else [], warnings=warnings or [],
         next=nxt or [f"videocli scopes {name} --shots", f"videocli grade preview {name} --split"])


def register(app: typer.Typer) -> None:
    W: dict[str, Any] = {"writes": True, "dry_run": True, "exit_codes": (0, 2, 4), "phase": 7}

    @command(app, "scopes", summary="Colour as numbers per shot or clip: luma percentiles, clipping, channel means, saturation, white balance, shot-to-shot difference", exit_codes=(0, 2, 4), phase=7)
    def scopes(
        target: str = typer.Argument(..., metavar="FILE|NAME", help="Media file, or an EDL name (measures each clip with its grade)."),
        shots: bool = typer.Option(False, "--shots", help="Per shot (file) or per clip (EDL)."),
        at: Optional[str] = typer.Option(None, "--at", metavar="T", help="One frame."),
        frm: Optional[str] = typer.Option(None, "--from", metavar="T"),
        to: Optional[str] = typer.Option(None, "--to", metavar="T"),
        ungraded: bool = typer.Option(False, "--ungraded", help="EDL: measure before the grade."),
        sheet: bool = typer.Option(False, "--sheet", help="EDL: also write before/after stills."),
    ) -> None:
        """Measure colour."""
        from videocli.cli.commands.perception import resolve_source, window_opts
        from videocli.color import scopes as sc
        from videocli.color import workflow as wf
        from videocli.edl import io

        ws = state.workspace()
        arts = []
        if io.NAME_RE.match(target) and io.exists(ws, target):
            edl = io.load(ws, target)
            res = wf.scopes_for_edl(ws, edl, graded=not ungraded)
            data: dict[str, Any] = {"edl": target, "graded": not ungraded, "shots": res, "shot_difference": wf.shot_difference(res)}
            if sheet:
                data["split"] = wf.preview_split(ws, edl, ws.dir("reports/grades"))
                arts = [artifact("still", s["path"]) for s in data["split"]]
            emit(data, artifacts=arts, next=[f"videocli grade auto {target} --balance --match"])
            return
        from videocli.cli.commands.eyes import _ensure_scenes
        from videocli.media import probe as probemod
        from videocli.media.frames import extract_many

        src = resolve_source(target)
        pr = probemod.load(ws, src)
        a, b = window_opts(frm, to)
        lo, hi = a or 0.0, min(b if b is not None else pr.duration, pr.duration)
        if at is not None:
            from videocli.core.timeutil import parse_time

            ranges = [("t", parse_time(at), parse_time(at))]
        elif shots:
            doc = _ensure_scenes(src, target)
            ranges = [(s["id"], s["start"], s["end"]) for s in doc["scenes"] if s["end"] > lo and s["start"] < hi]
        else:
            ranges = [("all", lo, hi)]
        out = []
        for rid, s0, s1 in ranges:
            times = [s0] if s1 <= s0 else [round(s0 + (s1 - s0) * (i + 0.5) / 3, 3) for i in range(3)]
            paths = extract_many(src.path, times, ws.source_dir(src.id) / "frames", width=wf.SAMPLE_W)
            import numpy as np

            rgb = np.concatenate([sc.load_rgb(p) for p in paths])
            out.append({"shot": rid, "start": s0, "end": s1, **sc.measure(rgb)})
        out = sc.finish(out)
        emit({"source_id": src.id, "shots": out, "shot_difference": wf.shot_difference(out)})

    grade = sub_app("grade", "Colour grades as editable numbers, compiled to one LUT per clip.")
    app.add_typer(grade)

    @command(grade, "input", group="grade", summary="Input transform for a source: auto (from tags), none, hlg_to_rec709, pq_to_rec709, bt601_to_rec709 or a camera .cube", **W)
    def grade_input(name: str = typer.Argument(...), source: str = typer.Option(..., "--source", metavar="ID"),
                    transform: str = typer.Option("auto", "--transform", metavar="auto|NAME|LUT.cube")) -> None:
        from videocli.color import input as ctin
        from videocli.core.errors import InputError, UsageError
        from videocli.core.source import lookup
        from videocli.media import probe as probemod

        edl = _load(name)
        sid = source if source in edl.sources else next((k for k, v in edl.sources.items() if Path(v.path).name == source or k.startswith(source)), None)
        if sid is None:
            raise InputError("SOURCE_UNKNOWN", f"No source {source!r} in EDL {name}.", hint=f"Sources: {', '.join(edl.sources)}")
        warnings: list[dict[str, str]] = []
        if transform == "auto":
            pr = probemod.load(state.workspace(), lookup(state.workspace(), edl.sources[sid].path))
            transform, warnings = ctin.choose_auto(pr)
        elif transform.lower().endswith(".cube"):
            if not Path(transform).is_file():
                raise InputError("FILE_NOT_FOUND", f"No LUT at {transform}.")
            transform = "lut:" + str(Path(transform).resolve())
        elif transform not in ctin.KNOWN:
            raise UsageError("BAD_ARGUMENT", f"Unknown transform {transform!r}.", hint=f"Use {', '.join(ctin.KNOWN)} or a .cube file.")
        edl.sources[sid].input_transform = None if transform == "none" else transform
        _emit_edl(name, {"source": sid, "transform": transform}, _save(edl, "grade input", source=sid, transform=transform), warnings=warnings)

    @command(grade, "set", group="grade", summary="Add an op to a clip's grade, a shared group grade, or the timeline look (e.g. --op 'cdl slope=1.05,1,0.95 sat=0.9')", **W)
    def grade_set(
        name: str = typer.Argument(...),
        clip: Optional[str] = typer.Option(None, "--clip", metavar="ID"),
        group: Optional[str] = typer.Option(None, "--group", metavar="KEY", help="Shared grade key; with --clip, assigns the clip to it."),
        look: bool = typer.Option(False, "--look", help="The timeline look, applied after every clip grade."),
        op: list[str] = typer.Option([], "--op", help="Op in text form; repeatable."),
        replace: bool = typer.Option(False, "--replace", help="Replace the grade's ops instead of appending."),
    ) -> None:
        from videocli.color.ops import parse_op
        from videocli.core.errors import UsageError
        from videocli.edl.model import Grade
        from videocli.edl.ops import get_clip

        edl = _load(name)
        if sum(bool(x) for x in (clip or group, look)) != 1:
            raise UsageError("BAD_ARGUMENT", "Choose --clip ID, --group KEY (or both) or --look.")
        ops = [parse_op(o) for o in op]
        if look:
            key = "look"
        elif group:
            key = group
        else:
            c = get_clip(edl, clip)  # type: ignore[arg-type]
            key = c.grade or f"g_{c.id}"
        if clip:
            get_clip(edl, clip).grade = key
        g = edl.grades.get(key) or Grade()
        g.ops = ops if replace else [*g.ops, *ops]
        edl.grades[key] = g
        users = [c.id for c in edl.tracks.main if c.grade == key]
        _emit_edl(name, {"grade": key, "ops": g.ops, "clips": users}, _save(edl, "grade set", grade=key, ops=op))

    @command(grade, "auto", group="grade", summary="Balance white and exposure per shoot and match shots to a reference, within skin and clipping limits", **W)
    def grade_auto(name: str = typer.Argument(...), balance: bool = typer.Option(False, "--balance"), match: bool = typer.Option(False, "--match"),
                   reference: Optional[str] = typer.Option(None, "--reference", metavar="CLIP_ID|IMAGE"),
                   strength: float = typer.Option(0.8, "--strength", min=0.0, max=1.0)) -> None:
        from videocli.color import workflow as wf

        edl = _load(name)
        if not balance and not match:
            balance = match = True
        img = Path(reference) if reference and Path(reference).is_file() else None
        res = wf.auto(state.workspace(), edl, balance=balance, match=match, reference=None if img else reference,
                      strength=strength, reference_image=img, force=state.force)
        warnings = []
        if res["skin_clamped"]:
            warnings.append({"code": "SKIN_CLAMPED", "message": f"Matching was limited to protect skin tones on {', '.join(res['skin_clamped'])}."})
        _emit_edl(name, res, _save(edl, "grade auto", balance=balance, match=match, strength=strength), warnings=warnings)

    @command(grade, "match", group="grade", summary="Fit a CDL that moves one clip toward another clip or an image", **W)
    def grade_match(name: str = typer.Argument(...), clip: str = typer.Option(..., "--clip", metavar="ID"),
                    to: str = typer.Option(..., "--to", metavar="CLIP_ID|IMAGE"), strength: float = typer.Option(1.0, "--strength", min=0.0, max=1.0)) -> None:
        import numpy as np

        from videocli.color import workflow as wf
        from videocli.color.match import match_cdl
        from videocli.color.ops import apply_stack, lut_ops
        from videocli.color.render import stack_for
        from videocli.edl.model import Grade
        from videocli.edl.ops import get_clip

        ws = state.workspace()
        edl = _load(name)
        c = get_clip(edl, clip)
        cur, _ = wf.clip_pixels(ws, edl, c)
        cur = np.clip(apply_stack(cur, lut_ops(stack_for(edl, c.grade))), 0, 1)
        if Path(to).is_file():
            ref = wf.media_pixels(ws, Path(to))
        else:
            r = get_clip(edl, to)
            ref, _ = wf.clip_pixels(ws, edl, r)
            ref = np.clip(apply_stack(ref, lut_ops(stack_for(edl, r.grade))), 0, 1)
        op = match_cdl(cur, ref, strength)
        clamped = op.pop("_clamped", False)
        key = c.grade or f"g_{c.id}"
        g = edl.grades.get(key) or Grade()
        g.ops = [*g.ops, op]
        edl.grades[key] = g
        c.grade = key
        warnings = [{"code": "SKIN_CLAMPED", "message": "The match was limited to keep skin hue within tolerance."}] if clamped else []
        _emit_edl(name, {"grade": key, "op": op}, _save(edl, "grade match", clip=clip, to=to, strength=strength), warnings=warnings)

    @command(grade, "look", group="grade", summary="Timeline look from a .cube, or fitted from a reference frame or video (contrast, saturation, warmth)", **W)
    def grade_look(name: str = typer.Argument(...), frm: str = typer.Option(..., "--from", metavar="REF"),
                   strength: float = typer.Option(1.0, "--strength", min=0.0, max=1.0)) -> None:
        from videocli.color import workflow as wf
        from videocli.core.errors import file_not_found
        from videocli.edl.model import Grade

        ref = Path(frm)
        if not ref.is_file():
            raise file_not_found(frm)
        edl = _load(name)
        ops = wf.look_from(state.workspace(), edl, ref, strength)
        edl.grades["look"] = Grade(ops=ops)
        _emit_edl(name, {"grade": "look", "ops": ops}, _save(edl, "grade look", ref=str(ref), strength=strength))

    @command(grade, "show", group="grade", summary="Grades, their ops, and which clips use them", exit_codes=(0, 4), phase=7)
    def grade_show(name: str = typer.Argument(...), clip: Optional[str] = typer.Option(None, "--clip", metavar="ID")) -> None:
        from videocli.color.render import stack_for

        edl = _load(name)
        if clip:
            from videocli.edl.ops import get_clip

            c = get_clip(edl, clip)
            emit({"clip": clip, "grade": c.grade, "stack": stack_for(edl, c.grade),
                  "input_transform": edl.sources[c.source].input_transform})
            return
        emit({"grades": {k: {"ops": g.ops, "clips": [c.id for c in edl.tracks.main if c.grade == k]} for k, g in edl.grades.items()},
              "input_transforms": {sid: s.input_transform for sid, s in edl.sources.items() if s.input_transform},
              "ungraded": [c.id for c in edl.tracks.main if not c.grade]})

    @command(grade, "preview", group="grade", summary="Before/after stills (--split) or N ranked candidates on one sheet (--candidates N)", writes=True, dry_run=False, exit_codes=(0, 2, 4), phase=7)
    def grade_preview(name: str = typer.Argument(...), clip: Optional[str] = typer.Option(None, "--clip", metavar="ID"),
                      split: bool = typer.Option(True, "--split/--no-split"), candidates: Optional[int] = typer.Option(None, "--candidates", min=2, max=8)) -> None:
        from videocli.color import workflow as wf
        from videocli.core.fsutil import rel

        ws = state.workspace()
        edl = _load(name)
        out_dir = ws.dir("reports/grades")
        if candidates:
            res = wf.candidates(ws, edl, candidates, out_dir, clip)
            res["sheet"] = rel(res["sheet"])
            emit(res, artifacts=[artifact("sheet", res["sheet"])],
                 next=[f"videocli grade auto {name} --strength {res['candidates'][0]['strength']}"])
            return
        res = wf.preview_split(ws, edl, out_dir, clip)
        for r in res:
            r["path"] = rel(r["path"])
        emit({"stills": res}, artifacts=[artifact("still", r["path"]) for r in res])

    @command(grade, "reset", group="grade", summary="Remove a clip's grade (or every grade with no --clip)", **W)
    def grade_reset(name: str = typer.Argument(...), clip: Optional[str] = typer.Option(None, "--clip", metavar="ID")) -> None:
        from videocli.edl.ops import get_clip

        edl = _load(name)
        if clip:
            c = get_clip(edl, clip)
            key = c.grade
            c.grade = None
            if key and key.startswith("g_") and not any(x.grade == key for x in edl.tracks.main):
                edl.grades.pop(key, None)
        else:
            for c in edl.tracks.main:
                c.grade = None
            for o in edl.tracks.overlay:
                o.grade = None
            edl.grades = {}
        _emit_edl(name, {"reset": clip or "all"}, _save(edl, "grade reset", clip=clip))

    lut = sub_app("lut", "Compiled LUTs.")
    app.add_typer(lut)

    @command(lut, "build", group="lut", summary="Write the compiled .cube for a clip's grade or the look (opens in Resolve and other tools)", writes=True, dry_run=False, exit_codes=(0, 2, 4), phase=7)
    def lut_build(name: str = typer.Argument(...), clip: Optional[str] = typer.Option(None, "--clip", metavar="ID"),
                  look: bool = typer.Option(False, "--look"), output: str = typer.Option(..., "-o", "--output", metavar="OUT.cube"),
                  size: int = typer.Option(33, "--size", help="33 or 65.")) -> None:
        from videocli.color import lut as lutmod
        from videocli.color.render import stack_for
        from videocli.core.errors import UsageError
        from videocli.edl.ops import get_clip

        if size not in (33, 65):
            raise UsageError("BAD_ARGUMENT", "--size must be 33 or 65.")
        edl = _load(name)
        if look == bool(clip):
            raise UsageError("BAD_ARGUMENT", "Choose --clip ID or --look.")
        ops = (edl.grades["look"].ops if "look" in edl.grades else []) if look else stack_for(edl, get_clip(edl, clip).grade)  # type: ignore[arg-type]
        table = lutmod.compile_stack(ops, size)
        problems = lutmod.check_table(table, size)
        if problems:
            from videocli.core.errors import RuntimeFailure

            raise RuntimeFailure("RENDER_FAILED", f"Broken LUT: {', '.join(problems)}.")
        lutmod.write_cube(Path(output), table, size, title=f"videocli {name} {clip or 'look'}")
        emit({"output": output, "size": size, "ops": ops}, artifacts=[artifact("lut", output)])

    @formatter("scopes")
    def _fmt_scopes(d: dict[str, Any]) -> None:
        c = console()
        for s in d["shots"]:
            lu = s.get("luma", {})
            c.print(f"{s.get('clip') or s.get('shot')}: luma {lu.get('p1')}/{lu.get('p50')}/{lu.get('p99')}  sat {s.get('saturation')}  "
                    f"wb {s.get('white_balance', {}).get('kelvin_shift')}K  Δ {s.get('delta_to_previous')}")
