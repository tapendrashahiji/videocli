"""Director loop commands (phase 10): critique, review build, notes, variants, tournament, direct."""

from __future__ import annotations

import contextlib
import io
import json
import shlex
from pathlib import Path
from typing import Any, Optional

import typer

from videocli.cli.commands.edit import _load
from videocli.cli.output import artifact, emit
from videocli.cli.registry import command, sub_app
from videocli.cli.state import state

P10: dict[str, Any] = {"phase": 10}


def run_inner(argv: list[str]) -> dict[str, Any]:
    """Run a videocli command in-process (JSON), keeping the outer command's state intact."""
    from videocli.cli.app import main

    saved = dict(state.__dict__)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = main(["--json", "--progress", "none", *(["--workspace", state.workspace_path] if state.workspace_path else []), *argv])
    finally:
        state.__dict__.update(saved)
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    doc = json.loads(lines[-1]) if lines else {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": "no output"}}
    doc["_exit"] = code
    return doc


def _audit_summary(name: str) -> dict[str, Any]:
    r = run_inner(["audit", name, "--fail-on", "error"])
    rep = r["data"] if r["ok"] else r["error"].get("details", {})
    fails = sum(1 for m in rep.get("metrics", []) if m["status"] == "fail") + sum(1 for b in rep.get("brief", []) if b["status"] == "fail")
    return {"audit_fails": fails, "audit_issues": len(rep.get("issues", [])), "metrics": {m["metric"]: m["value"] for m in rep.get("metrics", [])}}


def apply_notes(name: str, new: str, only: list[str] | None, dry_run: bool) -> dict[str, Any]:
    from videocli.core.errors import InputError
    from videocli.edl import io as edlio
    from videocli.judge import notes as nm
    from videocli.providers import orders as om

    ws = state.workspace()
    edl = _load(name)
    allnotes = nm.load_all(ws)
    todo = [n for n in allnotes if n.edl == name and n.status == "open" and (not only or n.id in only)]
    if not todo:
        raise InputError("ARTIFACT_MISSING", f"No open notes for {name}.", hint="Run: videocli notes ingest notes.json")
    order = nm.note_order(ws, edl, todo, new)
    om.provider(state.provider or state.settings.provider).answer(ws, order, f"videocli notes apply {name} -o {new}" + (" --dry-run" if dry_run else ""))
    answers = om.answers(order)
    tm = edl.timemap()
    all_clips = {c.id for c in edl.tracks.main} | {o.id for o in edl.tracks.overlay} | {g.id for g in edl.tracks.graphics}
    plan = []
    for n in todo:
        a = answers[n.id]["answer"]
        lo, hi = nm.window(n)
        allowed = {sp.id for sp in tm.spans if sp.out_start < hi and sp.out_end > lo}
        allowed |= {o.id for o in edl.tracks.overlay if o.anchor in allowed} | {g.id for g in edl.tracks.graphics if g.anchor in allowed}
        rejected = [(op, nm.check_scope(op, new, allowed, all_clips)) for op in a.get("ops", [])]
        plan.append({"note": n.id, "text": n.text, "action": a["action"], "ops": [op for op, err in rejected if err is None],
                     "rejected": [{"op": op, "reason": err} for op, err in rejected if err], "question": a.get("question")})
    if dry_run:
        return {"plan": plan, "dry_run": True}
    before = _audit_summary(name)
    if not edlio.exists(ws, new) or state.force:
        cp = run_inner(["edl", "copy", name, new, "--force"])
        if not cp["ok"]:
            raise InputError("EDL_INVALID", f"Could not copy {name} to {new}: {cp['error']['message']}")
    results = []
    by_id = {n.id: n for n in allnotes}
    for p in plan:
        n = by_id[p["note"]]
        if p["action"] == "question":
            n.status, n.question = "question", p["question"] or "Unclear note"
        elif p["action"] == "decline":
            n.status = "declined"
        else:
            ran = []
            for op in p["ops"]:
                words = shlex.split(op[len("videocli "):] if op.startswith("videocli ") else op)
                r = run_inner(words)
                ran.append({"op": op, "ok": r["ok"], "error": None if r["ok"] else r["error"]["code"]})
            if p["ops"] and all(x["ok"] for x in ran):
                n.status, n.ops = "applied", p["ops"]
            elif not p["ops"]:
                n.status, n.question = "question", "No operation fits this note within its scope."
            p["ran"] = ran
        results.append({**p, "status": n.status})
    nm.save_all(ws, allnotes)
    lint = run_inner(["edl", "lint", new])
    after = _audit_summary(new)
    worse = [k for k, v in after["metrics"].items() if k in before["metrics"] and abs(v - before["metrics"][k]) > 1e-9 and after["audit_fails"] > before["audit_fails"]]
    return {"new": new, "results": results, "lint_passed": lint["ok"], "audit": {"before": before, "after": after}, "worse_metrics": worse}


def register(app: typer.Typer) -> None:
    W: dict[str, Any] = {"writes": True, "dry_run": False, "exit_codes": (0, 1, 2, 4, 6), **P10}

    # ------------------------------------------------------------------ critique
    @command(app, "critique", summary="Independent critique: a pack without the builder's reasoning, a rubric (story, pacing, shot choice first), problems with timecodes", **W)
    def critique(name: str = typer.Argument(...), refs: list[str] = typer.Option([], "--refs", metavar="REF"),
                 fresh: bool = typer.Option(False, "--fresh", help="Require a provider or context separate from the builder.")) -> None:
        from videocli.editorial.brief import load_brief
        from videocli.judge import critique as cr
        from videocli.providers import orders as om

        ws = state.workspace()
        edl = _load(name)
        prov = state.provider or state.settings.provider
        existing = [o for o in om.all_orders(ws) if o["kind"] == "critique" and o["meta"].get("edl") == name and o["status"] != "cancelled"
                    and o["meta"].get("revision") == len(edl.history)]
        if existing:
            order = existing[-1]
            pack = json.loads((Path(order["meta"]["pack"]) / "pack.json").read_text()) | {"dir": order["meta"]["pack"]}
        else:
            a = run_inner(["audit", name]) if load_brief(ws).style else None
            rep = (a["data"] if a and a["ok"] else (a or {}).get("error", {}).get("details")) if a else None
            pack = cr.build_pack(ws, edl, state.settings, refs=refs, audit_report=rep)
            order = cr.make_order(ws, pack, fresh=fresh, provider=prov)
            order["meta"]["revision"] = len(edl.history)
            om.save(ws, order)
        om.provider(prov).answer(ws, order, f"videocli critique {name}" + (" --fresh" if fresh else ""))
        doc, path = cr.store(ws, edl, om.load(ws, order["id"]), pack)
        emit(doc, artifacts=[artifact("critique", path)], next=[f"videocli review build {name}"])

    # ------------------------------------------------------------------ review
    review = sub_app("review", "Static review pages for people.")
    app.add_typer(review)

    @command(review, "build", group="review", summary="One HTML page with the preview, cuts and a notes form (no server); --variants for a blind side-by-side choice", **W)
    def review_build(name: str = typer.Argument(...), output: Optional[str] = typer.Option(None, "-o", "--output", metavar="DIR"),
                     variants: Optional[str] = typer.Option(None, "--variants", metavar="A,B")) -> None:
        from videocli.core.fsutil import rel
        from videocli.judge.review import build

        ws = state.workspace()
        edl = _load(name)
        vs = [_load(v.strip()) for v in variants.split(",") if v.strip()] if variants else None
        out = Path(output) if output else ws.dir("review") / name
        res = build(ws, edl, out, state.settings, vs)
        emit({**res, "page": rel(res["page"])}, artifacts=[artifact("review", res["page"])],
             next=["Open the page, export notes.json, then: videocli notes ingest notes.json"])

    # ------------------------------------------------------------------ notes
    notes = sub_app("notes", "Human notes tied to timecodes.")
    app.add_typer(notes)

    @command(notes, "ingest", group="notes", summary="Store notes from the review page (notes.json) or typed lines ('12.4 pacing high: hold longer')", writes=True, dry_run=False, exit_codes=(0, 2, 4), **P10)
    def notes_ingest(file: str = typer.Argument(...), edl: Optional[str] = typer.Option(None, "--edl", metavar="NAME")) -> None:
        from videocli.judge import notes as nm
        from videocli.judge.loop import load_state, save_state

        ws = state.workspace()
        res = nm.ingest(ws, Path(file), edl)
        if res["approved"]:
            name = edl or json.loads(Path(file).read_text()).get("edl")
            st = load_state(ws, name)
            st["approved"] = True
            save_state(ws, st)
        emit(res, next=["videocli notes list NAME --open", "videocli notes apply NAME -o NEW --dry-run"])

    @command(notes, "list", group="notes", summary="Notes for an EDL and their status (open, applied, declined, question)", exit_codes=(0,), **P10)
    def notes_list(name: str = typer.Argument(...), open_only: bool = typer.Option(False, "--open")) -> None:
        from videocli.judge import notes as nm

        items = [n.model_dump(by_alias=True, exclude_none=True) for n in nm.load_all(state.workspace()) if n.edl == name and (not open_only or n.status == "open")]
        emit({"total": len(items), "notes": items})

    @command(notes, "apply", group="notes", summary="Turn open notes into EDL commands (work order) that change only what each note mentions; ambiguous notes become questions", writes=True, dry_run=True, exit_codes=(0, 2, 4, 6), **P10)
    def notes_apply(name: str = typer.Argument(...), output: str = typer.Option(..., "-o", "--output", metavar="NEW"),
                    only: Optional[str] = typer.Option(None, "--only", metavar="ID,…")) -> None:
        res = apply_notes(name, output, [x.strip() for x in only.split(",")] if only else None, state.dry_run)
        warnings = []
        if res.get("worse_metrics"):
            warnings.append({"code": "NOTE_MADE_WORSE", "message": f"Audit got worse on: {', '.join(res['worse_metrics'])}."})
        qs = [r for r in res.get("results", []) if r.get("status") == "question"]
        if qs:
            warnings.append({"code": "NOTES_NEED_ANSWERS", "message": f"{len(qs)} note(s) became questions for the reviewer."})
        emit(res, warnings=warnings, next=[f"videocli render {output} --preview -o {output}.mp4", f"videocli review build {output}"] if not state.dry_run else [])

    # ------------------------------------------------------------------ variants and tournament
    @command(app, "variants", summary="N versions that differ along one axis (pace, music, open, coverage, look), sharing cached render nodes", writes=True, dry_run=True, exit_codes=(0, 2, 4), **P10)
    def variants_cmd(name: str = typer.Argument(...), vary: str = typer.Option(..., "--vary"), n: int = typer.Option(2, "-n", min=2, max=8)) -> None:
        from videocli.edl import io as edlio
        from videocli.judge.loop import variants

        ws = state.workspace()
        vs = variants(ws, _load(name), vary, n)
        paths = [] if state.dry_run else [edlio.save(ws, v, "variant", axis=vary, base=name) for v in vs]
        emit({"variants": [{"name": v.name, "duration": round(v.output_duration(), 3), "clips": len(v.tracks.main)} for v in vs], "dry_run": state.dry_run},
             artifacts=[artifact("edl", p) for p in paths], next=[f"videocli tournament {' '.join(v.name for v in vs)}"])

    @command(app, "tournament", summary="Pairwise blind comparisons (both orders) through a work order; ranking by wins", **W)
    def tournament(names: list[str] = typer.Argument(..., metavar="A B [C ...]")) -> None:
        from videocli.core.errors import UsageError
        from videocli.core.fsutil import rel
        from videocli.judge.loop import rank, tournament_order
        from videocli.providers import orders as om
        from videocli.render.run import render

        if len(names) < 2:
            raise UsageError("BAD_ARGUMENT", "A tournament needs at least two EDLs.")
        ws = state.workspace()
        previews = {}
        for nme in names:
            p = ws.dir("reports/tournament") / f"{nme}.mp4"
            if not p.exists():
                render(ws, _load(nme), p, preset_name="preview", settings=state.settings, jobs=int(state.settings.jobs))
            previews[nme] = rel(p)
        order = tournament_order(ws, names, previews)
        om.provider(state.provider or state.settings.provider).answer(ws, order, "videocli tournament " + " ".join(names))
        table = rank(names, om.answers(order))
        emit({"ranking": table, "winner": table[0]["edl"], "comparisons": len(order["items"])})

    # ------------------------------------------------------------------ direct
    @command(app, "direct", summary="Run the artistic loop: assemble → refine → preview → verify → audit → critique → review gate → apply notes, round by round (resumable)", **W)
    def direct(output: str = typer.Option(..., "-o", "--output", metavar="NAME"), rounds: int = typer.Option(3, "--rounds", min=1, max=8),
               gate: str = typer.Option("human", "--gate", help="human (stop for a review page each round) or critic."),
               budget_minutes: Optional[float] = typer.Option(None, "--budget-minutes", min=1), approve: bool = typer.Option(False, "--approve")) -> None:
        from videocli.core.errors import CheckFailed, UsageError
        from videocli.core.fsutil import rel, write_json
        from videocli.edl import io as edlio
        from videocli.judge import loop

        if gate not in ("human", "critic"):
            raise UsageError("BAD_ARGUMENT", "--gate must be human or critic.")
        ws = state.workspace()
        st = loop.load_state(ws, output)
        if approve:
            st["approved"] = True
        reason = loop.stop_reason(st, rounds, budget_minutes)
        if reason:
            st["status"] = f"stopped: {reason}"
            loop.save_state(ws, st)
            open_notes = run_inner(["notes", "list", output, "--open"]).get("data", {}).get("total", 0)
            emit({"name": output, "status": st["status"], "rounds": st["rounds"], "open_notes": open_notes})
            return
        stage = st.get("stage", "build")
        if stage == "build":
            if not edlio.exists(ws, output):
                for step in (["selects"], ["assemble", "-o", output], ["assemble", "-o", output, "--stage", "picture"]):
                    r = run_inner(step)
                    if not r["ok"]:
                        loop.save_state(ws, st)
                        from videocli.core.errors import VideoCliError

                        err = r["error"]
                        cls = {6: __import__("videocli.core.errors", fromlist=["Pending"]).Pending}.get(r["_exit"], VideoCliError)
                        raise cls(err["code"], err["message"], hint=err.get("hint"), details=err.get("details"))
                run_inner(["refine", output, "--passes", "coverage,split-edits,pacing"])
            st["round"] += 1
            st["stage"] = "check"
            loop.save_state(ws, st)
        if st.get("stage") == "check":
            prev = ws.dir("reports/direct") / f"{output}-r{st['round']}.mp4"
            r = run_inner(["render", output, "--preview", "-o", str(prev), "--force"])
            v = run_inner(["verify", str(prev), "--edl", output, "--skip", "loudness"]) if r["ok"] else r
            if not v["ok"]:
                loop.save_state(ws, st)
                raise CheckFailed("VERIFY_FAILED", f"Round {st['round']} preview failed verify; the loop stops until it passes.",
                                  hint=v["error"].get("hint"), details=v["error"].get("details"))
            aud = _audit_summary(output)
            st["current"] = {"round": st["round"], "preview": rel(prev), **aud}
            st["stage"] = "critique"
            loop.save_state(ws, st)
        if st.get("stage") == "critique":
            c = run_inner(["critique", output, "--fresh"])
            if not c["ok"] and c["_exit"] == 6:
                loop.save_state(ws, st)
                from videocli.core.errors import Pending

                raise Pending(c["error"]["code"], c["error"]["message"], hint=c["error"].get("hint"), details=c["error"].get("details"))
            if c["ok"]:
                st["current"]["critic"] = c["data"]["weighted"]
                st["current"]["top_problems"] = [p["evidence"] for p in c["data"]["problems"][:3]]
            st["stage"] = "gate"
            loop.save_state(ws, st)
        if st.get("stage") == "gate":
            cur = st["current"]
            if not any(r_.get("round") == cur["round"] for r_ in st["rounds"]):
                st["rounds"].append(cur)
                write_json(ws.dir("reports/direct") / f"{output}-round-{cur['round']}.json", cur)
            if gate == "human":
                page = run_inner(["review", "build", output])
                st["stage"] = "apply"
                loop.save_state(ws, st)
                raise loop.review_pending(output, page.get("data", {}).get("page", "review page"), cur["round"])
            # critic gate: the critic's top problems become notes
            from videocli.judge import notes as nm

            probs = run_inner(["critique", output]).get("data", {}).get("problems", [])[:3]
            prev_top = st["rounds"][-2].get("top_problems") if len(st["rounds"]) > 1 else None
            if not probs or prev_top == cur.get("top_problems"):
                st["status"] = "stopped: critic problems stopped changing"
                loop.save_state(ws, st)
                emit({"name": output, "status": st["status"], "rounds": st["rounds"]})
                return
            allnotes = nm.load_all(ws)
            for p in probs:
                allnotes.append(nm.Note(id=f"n{len(allnotes) + 1:04d}", edl=output, at=p["at"], category=p["category"] if p["category"] in nm.CATS else "other",
                                        priority="high", text=f"{p['evidence']} — {p['fix']}", author="critic"))
            nm.save_all(ws, allnotes)
            st["stage"] = "apply"
            loop.save_state(ws, st)
        if st.get("stage") == "apply":
            from videocli.judge import notes as nm

            if st.get("approved"):
                st["status"] = "stopped: approved"
                loop.save_state(ws, st)
                emit({"name": output, "status": st["status"], "rounds": st["rounds"]})
                return
            if not any(n.edl == output and n.status == "open" for n in nm.load_all(ws)):
                page = st["current"].get("preview")
                raise loop.review_pending(output, page or "review page", st["round"])
            new = f"{output}.r{st['round'] + 1}"
            res = apply_notes(output, new, None, False)
            run_inner(["edl", "copy", new, output, "--force"])
            st["current"]["applied"] = [r_["note"] for r_ in res["results"] if r_["status"] == "applied"]
            st["stage"] = "build"
            st["round"] += 0
            loop.save_state(ws, st)
            emit({"name": output, "status": "round applied", "round": st["round"], "applied": st["current"]["applied"]},
                 next=[f"videocli direct -o {output} --gate {gate}"])
            return
        emit({"name": output, "status": st.get("status"), "stage": st.get("stage")})
