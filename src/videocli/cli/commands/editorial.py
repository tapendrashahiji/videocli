"""Story and style commands (phase 9): brief, style, story, selects, stringout, assemble, refine, audit."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import typer

from videocli.cli.commands.edit import _load, _save
from videocli.cli.output import artifact, emit
from videocli.cli.registry import command, sub_app
from videocli.cli.state import state

P9: dict[str, Any] = {"phase": 9}


def register(app: typer.Typer) -> None:
    W: dict[str, Any] = {"writes": True, "dry_run": True, "exit_codes": (0, 2, 4), **P9}

    # ------------------------------------------------------------------ brief
    brief = sub_app("brief", "What the edit is for: logline, audience, duration, aspect, tone, style, required and forbidden phrases.")
    app.add_typer(brief)

    @command(brief, "set", group="brief", summary="Set or update the brief", **W)
    def brief_set(logline: Optional[str] = typer.Option(None, "--logline"), audience: Optional[str] = typer.Option(None, "--audience"),
                  duration: Optional[float] = typer.Option(None, "--duration", metavar="S", min=1), aspect: Optional[str] = typer.Option(None, "--aspect"),
                  style: Optional[str] = typer.Option(None, "--style", metavar="NAME"), tone: Optional[str] = typer.Option(None, "--tone"),
                  must_include: list[str] = typer.Option([], "--must-include", metavar='"phrase"'),
                  must_avoid: list[str] = typer.Option([], "--must-avoid", metavar='"phrase"'),
                  music: Optional[str] = typer.Option(None, "--music", metavar="FILE", help="Music for the music stage of assemble.")) -> None:
        from videocli.editorial.brief import load_brief, resolve_phrases, save_brief

        ws = state.workspace()
        b = load_brief(ws)
        for k, v in {"logline": logline, "audience": audience, "duration": duration, "aspect": aspect, "style": style, "tone": tone,
                     "music": str(Path(music).resolve()) if music else None}.items():
            if v is not None:
                setattr(b, k, v)
        b.must_include = list(dict.fromkeys([*b.must_include, *must_include]))
        b.must_avoid = list(dict.fromkeys([*b.must_avoid, *must_avoid]))
        type(b).model_validate(b.model_dump(by_alias=True))
        if not state.dry_run:
            save_brief(ws, b)
        res = resolve_phrases(ws, [*b.must_include, *b.must_avoid])
        warnings = [{"code": "PHRASE_NOT_FOUND", "message": f"'{p}' is not spoken in any analyzed source."} for p, h in res.items() if not h]
        emit({"brief": b.model_dump(by_alias=True), "phrases": res, "dry_run": state.dry_run}, warnings=warnings,
             next=["videocli story init --archetype doc"])

    @command(brief, "show", group="brief", summary="The brief", exit_codes=(0,), **P9)
    def brief_show() -> None:
        from videocli.editorial.brief import load_brief

        emit({"brief": load_brief(state.workspace()).model_dump(by_alias=True)})

    # ------------------------------------------------------------------ style
    style = sub_app("style", "Style profiles: pace, grammar, music, speech, colour and sound measured from reference edits.")
    app.add_typer(style)

    @command(style, "learn", group="style", summary="Measure reference edits and merge them into a named profile", writes=True, dry_run=False, exit_codes=(0, 1, 2, 4), **P9)
    def style_learn(refs: list[str] = typer.Argument(..., metavar="REF..."), output: str = typer.Option(..., "-o", "--output", metavar="NAME")) -> None:
        from videocli.core.source import lookup
        from videocli.editorial import style as st

        ws = state.workspace()
        measured = []
        for r in refs:
            src = lookup(ws, r)
            measured.append({"path": r, **st.measure_source(ws, src, state.settings)})
        prof = st.merge(measured, output, refs)
        prof["references"] = measured
        p = st.save(ws, prof)
        emit({k: v for k, v in prof.items() if k != "references"}, artifacts=[artifact("style", p)],
             next=[f"videocli brief set --style {output}", f"videocli style compare {output} EDL"])

    @command(style, "show", group="style", summary="A style profile", exit_codes=(0, 4), **P9)
    def style_show(name: str = typer.Argument(...)) -> None:
        from videocli.editorial import style as st

        prof = st.load(state.workspace(), name)
        emit({k: v for k, v in prof.items() if k != "references"})

    @command(style, "compare", group="style", summary="An EDL's (or video's) metrics beside a profile, with deviations", exit_codes=(0, 4), **P9)
    def style_compare(name: str = typer.Argument(...), target: str = typer.Argument(..., metavar="EDL_OR_VIDEO")) -> None:
        from videocli.core.source import lookup
        from videocli.edl import io
        from videocli.editorial import style as st

        ws = state.workspace()
        prof = st.load(ws, name)
        if io.NAME_RE.match(target) and io.exists(ws, target):
            m = st.edl_metrics(ws, io.load(ws, target))
            m.pop("_shots", None)
        else:
            m = st.measure_source(ws, lookup(ws, target), state.settings)
        emit({"style": name, "target": target, "metrics": st.compare(prof, m), "measured": m})

    # ------------------------------------------------------------------ story
    story = sub_app("story", "Story structure: beats with intent and target length.")
    app.add_typer(story)

    @command(story, "init", group="story", summary="Start a story from an archetype (doc, interview, tutorial, promo, music, custom)", **W)
    def story_init(archetype: str = typer.Option("doc", "--archetype")) -> None:
        from videocli.core.errors import InputError, UsageError
        from videocli.editorial.brief import ARCHETYPES, init_story, load_brief, save_story, story_path

        if archetype not in ARCHETYPES:
            raise UsageError("BAD_ARGUMENT", f"--archetype must be one of {', '.join(ARCHETYPES)}.")
        ws = state.workspace()
        if story_path(ws).is_file() and not state.force:
            raise InputError("EDL_EXISTS", "A story already exists.", hint="Pass --force to replace it, or edit it with: videocli story set BEAT …")
        s = init_story(archetype, load_brief(ws))
        if not state.dry_run:
            save_story(ws, s)
        emit({"story": s.model_dump(by_alias=True), "dry_run": state.dry_run}, next=["videocli selects"])

    @command(story, "show", group="story", summary="The story, its beats and their selects", exit_codes=(0, 4), **P9)
    def story_show() -> None:
        from videocli.editorial.brief import load_story

        emit({"story": load_story(state.workspace()).model_dump(by_alias=True)})

    @command(story, "set", group="story", summary="Add or change a beat (intent, target seconds, position, chosen selects)", **W)
    def story_set(beat: str = typer.Argument(..., metavar="BEAT"), intent: Optional[str] = typer.Option(None, "--intent"),
                  seconds: Optional[float] = typer.Option(None, "--seconds", min=0.5), after: Optional[str] = typer.Option(None, "--after", metavar="BEAT"),
                  choose: Optional[str] = typer.Option(None, "--choose", metavar="I,J", help="Indices of the selects to use."),
                  remove: bool = typer.Option(False, "--remove")) -> None:
        from videocli.editorial.brief import Beat, load_story, save_story

        ws = state.workspace()
        s = load_story(ws)
        b = next((x for x in s.beats if x.id == beat), None)
        if remove:
            s.beats = [x for x in s.beats if x.id != beat]
        else:
            if b is None:
                b = Beat(id=beat)
                idx = next((i + 1 for i, x in enumerate(s.beats) if x.id == after), len(s.beats))
                s.beats.insert(idx, b)
            if intent is not None:
                b.intent = intent
            if seconds is not None:
                b.target_seconds = seconds
            if choose is not None:
                b.chosen = [int(x) for x in choose.split(",") if x.strip()]
        if not state.dry_run:
            save_story(ws, s)
        emit({"beats": [x.id for x in s.beats], "dry_run": state.dry_run})

    # ------------------------------------------------------------------ selects / stringout
    @command(app, "selects", summary="Rank candidate passages per story beat (content judged through a work order) — every select says why", writes=True, dry_run=False, exit_codes=(0, 2, 4, 6), **P9)
    def selects(beat: Optional[str] = typer.Option(None, "--beat", metavar="ID"), per_beat: int = typer.Option(5, "--per-beat", min=1, max=50)) -> None:
        from videocli.core.errors import InputError
        from videocli.editorial import assemble as asm
        from videocli.editorial.brief import load_story, save_story
        from videocli.providers import orders as om

        ws = state.workspace()
        s = load_story(ws)
        cands = asm.candidates(ws)
        if not cands:
            raise InputError("ARTIFACT_MISSING", "No transcribed sources to select from.", hint="Run: videocli analyze FOOTAGE --profile artistic")
        order = asm.selects_order(ws, s, cands, [beat] if beat else None)
        om.provider(state.provider or state.settings.provider).answer(ws, order, "videocli selects" + (f" --beat {beat}" if beat else ""))
        s = asm.rank(s, cands, om.answers(order), per_beat)
        save_story(ws, s)
        emit({"beats": [{"id": b.id, "selects": [x.model_dump() for x in b.selects], "chosen": b.chosen} for b in s.beats],
              "coverage": asm.coverage_report(s, cands)}, next=["videocli assemble -o rough", "videocli stringout -o strings"])

    @command(app, "stringout", summary="All selects for a beat (or every beat) in one EDL, for review", **W)
    def stringout(beat: Optional[str] = typer.Option(None, "--beat", metavar="ID"), output: str = typer.Option(..., "-o", "--output", metavar="NAME")) -> None:
        from videocli.edl import io
        from videocli.editorial import assemble as asm
        from videocli.editorial.brief import load_story

        ws = state.workspace()
        edl = asm.stringout(ws, load_story(ws), output, beat)
        path = None if state.dry_run else io.save(ws, edl, "stringout", beat=beat)
        emit({"name": output, "clips": len(edl.tracks.main), "duration": round(edl.output_duration(), 3)},
             artifacts=[artifact("edl", path)] if path else [], next=[f"videocli render {output} --preview -o {output}.mp4"])

    # ------------------------------------------------------------------ assemble
    @command(app, "assemble", summary="Build the cut in editor stages: radio (sound), picture (coverage needs), music", **W)
    def assemble(output: str = typer.Option(..., "-o", "--output", metavar="NAME"),
                 stage: str = typer.Option("radio", "--stage", help="radio, picture or music (later stages build on the existing EDL)."),
                 music: Optional[str] = typer.Option(None, "--music", metavar="FILE")) -> None:
        from videocli.core.errors import UsageError
        from videocli.edl import io
        from videocli.editorial import assemble as asm
        from videocli.editorial.brief import load_brief, load_story

        ws = state.workspace()
        b = load_brief(ws)
        info: dict[str, Any]
        if stage == "radio":
            edl, info = asm.radio(ws, load_story(ws), b, output)
        elif stage == "picture":
            from videocli.editorial import style as st

            edl = _load(output)
            limit = 8.0
            if b.style:
                limit = float((st.load(ws, b.style).get("pace") or {}).get("max_static_hold") or limit)
            info = asm.picture(ws, edl, limit)
        elif stage == "music":
            edl = _load(output)
            mf = music or b.music
            if not mf:
                raise UsageError("BAD_ARGUMENT", "No music: pass --music FILE or set it in the brief.")
            info = asm.music_stage(ws, edl, mf)
        else:
            raise UsageError("BAD_ARGUMENT", "--stage must be radio, picture or music.")
        path = None
        if not state.dry_run:
            path = io.save(ws, edl, f"assemble:{stage}")
            rev = edl.model_copy(deep=True)
            rev.name = f"{output}.{stage}"
            io.save(ws, rev, f"assemble:{stage}")
        warnings = []
        if info.get("dropped"):
            warnings.append({"code": "SELECTS_DROPPED", "message": f"{len(info['dropped'])} select(s) dropped to fit {b.duration}s."})
        for c in info.get("compromises", []):
            warnings.append({"code": "MUSIC_FIT", "message": c})
        nxt = {"radio": [f"videocli assemble -o {output} --stage picture", f"videocli render {output} --preview -o {output}.mp4"],
               "picture": [f"videocli refine {output} --passes coverage,split-edits,pacing"],
               "music": [f"videocli audit {output}" + (f" --style {b.style}" if b.style else "")]}[stage]
        emit({"name": output, "stage": stage, **info, "dry_run": state.dry_run}, warnings=warnings,
             artifacts=[artifact("edl", path)] if path else [], next=nxt)

    # ------------------------------------------------------------------ refine
    @command(app, "refine", summary="Editorial passes: coverage, split-edits, action, beats, pacing, match, punch (--dry-run lists proposals)", **W)
    def refine(name: str = typer.Argument(...), passes: str = typer.Option("coverage,split-edits,pacing", "--passes"),
               style: Optional[str] = typer.Option(None, "--style", metavar="NAME")) -> None:
        from videocli.editorial import idioms
        from videocli.editorial import style as st
        from videocli.editorial.brief import load_brief

        ws = state.workspace()
        edl = _load(name)
        sname = style or load_brief(ws).style
        prof = st.load(ws, sname) if sname else None
        changes = idioms.propose(edl, ws, [p.strip() for p in passes.split(",") if p.strip()], prof)
        if state.dry_run:
            emit({"proposed": [c.as_dict() for c in changes], "dry_run": True}, next=[f"videocli refine {name} --passes {passes}"])
            return
        applied = idioms.apply(edl, changes, ws)
        path = _save(edl, "refine", passes=passes)
        emit({"applied": applied, "proposed_only": [c.as_dict() for c in changes if c.as_dict() not in applied],
              "after": {"clips": len(edl.tracks.main), "overlays": len(edl.tracks.overlay), "duration": round(edl.output_duration(), 3)}},
             artifacts=[artifact("edl", path)] if path else [], next=[f"videocli audit {name}" + (f" --style {sname}" if sname else "")])

    # ------------------------------------------------------------------ audit
    @command(app, "audit", summary="Craft against a style profile and the brief: pace (too slow weighs most), coverage, beats, jump cuts, repeats", exit_codes=(0, 2, 4, 5), **P9)
    def audit_cmd(name: str = typer.Argument(..., metavar="NAME"), style: Optional[str] = typer.Option(None, "--style", metavar="NAME"),
                  fail_on: str = typer.Option("error", "--fail-on", help="info, warn or error: the severity that fails (exit 5).")) -> None:
        from videocli.core.errors import CheckFailed, UsageError
        from videocli.core.fsutil import rel, write_json
        from videocli.editorial import style as st
        from videocli.editorial.brief import load_brief
        from videocli.judge.audit import SEVERITY, audit, worst_severity

        if fail_on not in SEVERITY:
            raise UsageError("BAD_ARGUMENT", "--fail-on must be info, warn or error.")
        ws = state.workspace()
        edl = _load(name)
        b = load_brief(ws)
        sname = style or b.style
        prof = st.load(ws, sname) if sname else None
        rep = audit(ws, edl, prof, b)
        worst = worst_severity(rep)
        rep["passed"] = worst is None or SEVERITY[worst] < SEVERITY[fail_on]
        from datetime import datetime

        path = ws.dir("reports") / f"audit-{name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        write_json(path, rep)
        if not rep["passed"]:
            first = next((i.get("fix") for i in rep["issues"] if i.get("fix")), None)
            raise CheckFailed("AUDIT_FAILED", f"Audit of {name} found {worst}-level problems.", hint=f"Run: {first}" if first else f"See {rel(path)}",
                              details={**rep, "report": rel(path)})
        emit(rep, artifacts=[artifact("audit", path)])
