"""Deep perception commands (phase 5): shots, people, speakers, music, slides, log, orders, suggest."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import typer

from videocli.cli.commands.perception import limit_list, q, resolve_source, window_opts
from videocli.cli.output import artifact, console, emit, formatter
from videocli.cli.registry import command, sub_app
from videocli.cli.state import state

COND = re.compile(r"^\s*([a-z_]+)\s*(=|!=|>=|<=|>|<)\s*(\S+)\s*$")


def _field(shot: dict[str, Any], name: str) -> Any:
    if name in ("camera",):
        name = "motion"
    if name == "people":
        return len(shot.get("faces") or [])
    if name in shot and not isinstance(shot.get(name), dict):
        return shot[name]
    return (shot.get("metrics") or {}).get(name)


def match_where(shot: dict[str, Any], where: str) -> bool:
    from videocli.core.errors import UsageError

    for alt in re.split(r"\s+or\s+", where.strip()):
        ok = True
        for cond in re.split(r"\s+and\s+", alt):
            m = COND.match(cond)
            if not m:
                raise UsageError("BAD_ARGUMENT", f"Cannot parse condition {cond!r}.", hint="Use field=value, field>number, joined by and/or.")
            f, op, val = m.groups()
            v = _field(shot, f)
            try:
                num = float(val)
                if v is None:
                    ok = False
                    break
                vv = float(v)
                res = {"=": vv == num, "!=": vv != num, ">": vv > num, "<": vv < num, ">=": vv >= num, "<=": vv <= num}[op]
            except (TypeError, ValueError):
                sv = str(v).lower() if v is not None else ""
                res = {"=": sv == val.lower(), "!=": sv != val.lower()}.get(op, False)
            if not res:
                ok = False
                break
        if ok:
            return True
    return False


def compact(s: dict[str, Any]) -> str:
    people = ",".join(f["person"] for f in s.get("faces") or []) or "—"
    m = (s.get("metrics") or {}).get("motion")
    desc = f' | "{s["description"]}"' if s.get("description") else ""
    loc = {"interior": "int", "exterior": "ext"}.get(s.get("location") or "", s.get("location") or "")
    return (f"{s['id']} [{s['start']:07.2f}-{s['end']:07.2f}] {s.get('size') or '?':3} {s.get('type') or '-':8} {s.get('motion') or '-':8} {loc:3} | "
            f"{people} | motion {(f'{m:.2f}').lstrip('0') if m is not None else '-'}{desc}")


def _shots_doc(src, file: str) -> dict[str, Any]:
    from videocli.analyze.base import load_artifact
    from videocli.core.errors import InputError

    doc = load_artifact(state.workspace(), src, "shots", required=False)
    if doc is None:
        raise InputError("ARTIFACT_MISSING", f"No shot records for {file}.", hint=f"Run: videocli analyze {q(file)} --profile artistic")
    return doc


def register(app: typer.Typer) -> None:
    P5: dict[str, Any] = {"phase": 5}

    # ------------------------------------------------------------------ shots
    @command(app, "shots", summary="Shot records (size, type, camera motion, people, metrics, description) with a filter language", exit_codes=(0, 2, 4), **P5)
    def shots(file: str = typer.Argument(...), where: Optional[str] = typer.Option(None, "--where", metavar="EXPR", help="e.g. \"size=CU and motion=static\", \"duration>4\""),
              sort: Optional[str] = typer.Option(None, "--sort", metavar="FIELD", help="FIELD or -FIELD (descending)."),
              fmt: str = typer.Option("records", "--format", help="records or compact."), limit: Optional[int] = typer.Option(None, "--limit", min=1),
              frm: Optional[str] = typer.Option(None, "--from", metavar="T"), to: Optional[str] = typer.Option(None, "--to", metavar="T")) -> None:
        """List shots."""
        src = resolve_source(file)
        doc = _shots_doc(src, file)
        a, b = window_opts(frm, to)
        items = [s for s in doc["shots"] if (a is None or s["end"] > a) and (b is None or s["start"] < b)]
        if where:
            items = [s for s in items if match_where(s, where)]
        if sort:
            key = sort.lstrip("-")
            items.sort(key=lambda s: (_field(s, key) is None, _field(s, key) if _field(s, key) is not None else 0), reverse=sort.startswith("-"))
        shown, total, trunc = limit_list(items, limit)
        data: dict[str, Any] = {"source_id": src.id, "total": total, "truncated": trunc}
        if fmt == "compact":
            data["text"] = "\n".join(compact(s) for s in shown) + ("\n" if shown else "")
        else:
            data["shots"] = shown
        missing = sum(1 for s in doc["shots"] if not s.get("description"))
        emit(data, next=[f"videocli log {q(file)} --only-missing"] if missing else [f"videocli find {q(file)} \"…\" --mode both"])

    @formatter("shots")
    def _fmt(d: dict[str, Any]) -> None:
        import sys

        if "text" in d:
            sys.stdout.write(d["text"])
        else:
            for s in d["shots"]:
                sys.stdout.write(compact(s) + "\n")

    # ------------------------------------------------------------------ people
    @command(app, "people", summary="People found by local face grouping (p1, p2, …); --name attaches a name you supply", writes=True, dry_run=True, exit_codes=(0, 2, 4), **P5)
    def people(file: str = typer.Argument(...), name: list[str] = typer.Option([], "--name", metavar='p1="Maya"'),
               forget: bool = typer.Option(False, "--forget", help="Delete face data for this source (privacy).")) -> None:
        from videocli.analyze.base import load_artifact
        from videocli.core.errors import InputError
        from videocli.core.fsutil import read_json, write_json

        ws = state.workspace()
        src = resolve_source(file)
        if forget:
            p = ws.source_dir(src.id) / "faces.json"
            if not state.dry_run:
                p.unlink(missing_ok=True)
            emit({"forgotten": src.id, "dry_run": state.dry_run})
            return
        doc = load_artifact(ws, src, "faces", required=False)
        if doc is None:
            raise InputError("ARTIFACT_MISSING", f"No face data for {file}.", hint=f"Run: videocli analyze {q(file)} --only faces")
        names_path = ws.root / "project" / "people.json"
        names = read_json(names_path) if names_path.is_file() else {}
        for kv in name:
            k, _, v = kv.partition("=")
            names[k.strip()] = v.strip().strip('"')
        if name and not state.dry_run:
            write_json(names_path, names)
        out = [{**p, "name": names.get(p["id"])} for p in doc.get("people", [])]
        emit({"source_id": src.id, "people": out, "provenance": doc.get("provenance"), "dry_run": state.dry_run})

    # ------------------------------------------------------------------ speakers, music, slides
    @command(app, "speakers", summary="Speaker talk time and turns (diarize group)", exit_codes=(0, 3, 4), **P5)
    def speakers(file: str = typer.Argument(...)) -> None:
        from videocli.analyze.base import load_artifact
        from videocli.analyze.hearing import speakers_summary
        from videocli.core.errors import InputError

        src = resolve_source(file)
        doc = load_artifact(state.workspace(), src, "diarize", required=False)
        if doc is None:
            raise InputError("ARTIFACT_MISSING", f"No speaker analysis for {file}.", hint=f"Run: videocli analyze {q(file)} --only diarize")
        turns = doc.get("turns", [])
        overlaps = sum(1 for a, b in zip(turns, turns[1:], strict=False) if b["start"] < a["end"] and a["speaker"] != b["speaker"])
        warnings = [{"code": "OVERLAPPING_SPEECH", "message": f"{overlaps} overlapping turns."}] if overlaps > max(3, len(turns) // 10) else []
        emit({"speakers": speakers_summary(turns), "turns": len(turns)}, warnings=warnings)

    @command(app, "music", summary="Tempo, beats, downbeats, sections and bar energy of a music file or a source's audio", exit_codes=(0, 1, 4), **P5)
    def music(file: str = typer.Argument(...)) -> None:
        from videocli.analyze import base

        ws = state.workspace()
        src = resolve_source(file)
        ctx = base.RunContext(ws=ws, settings=state.settings, force=state.force)
        res = base.run_analyzers(src, ["music"], ctx, explicit={"music"})
        doc = base.load_artifact(ws, src, "music") or {}
        body = {k: doc[k] for k in ("tempo", "beats", "downbeats", "bars", "sections", "bar_energy", "engine") if k in doc}
        warnings = [{"code": "MUSIC_UNRELIABLE", "message": w} for w in doc.get("warnings", [])]
        if body.get("engine") == "builtin":
            warnings.append({"code": "MUSIC_LIGHT_ENGINE", "message": "Built-in beat tracker: sections are unlabelled. Install the music group for labelled sections."})
        emit({**body, "cached": "music" in res.cached}, warnings=warnings, artifacts=[artifact("music", ws.source_dir(src.id) / "music.json")])

    @command(app, "slides", summary="Slide changes in a screen recording or lecture, with the words spoken over each", exit_codes=(0, 1, 4), **P5)
    def slides(file: str = typer.Argument(...), limit: Optional[int] = typer.Option(None, "--limit", min=1)) -> None:
        from videocli.analyze import base

        ws = state.workspace()
        src = resolve_source(file)
        ctx = base.RunContext(ws=ws, settings=state.settings, force=state.force)
        base.run_analyzers(src, ["slides"], ctx, explicit={"slides"})
        doc = base.load_artifact(ws, src, "slides") or {}
        shown, total, trunc = limit_list(doc.get("slides", []), limit)
        emit({"total": total, "truncated": trunc, "slides": shown})

    # ------------------------------------------------------------------ layout
    @command(app, "layout", summary="What is where on screen per stable window: text blocks (OCR), faces, objects and free space", exit_codes=(0, 1, 3, 4), **P5)
    def layout(file: str = typer.Argument(...),
               frm: Optional[str] = typer.Option(None, "--from", metavar="T"),
               to: Optional[str] = typer.Option(None, "--to", metavar="T"),
               at: Optional[str] = typer.Option(None, "--at", metavar="T", help="Only the window showing this moment."),
               find: Optional[str] = typer.Option(None, "--find", metavar="TEXT", help="Where and when this text is on screen."),
               lines: bool = typer.Option(False, "--lines", help="Include every OCR line (default: blocks only)."),
               limit: Optional[int] = typer.Option(None, "--limit", min=1)) -> None:
        """Screen layout, source time. Boxes are [x, y, w, h] fractions of the frame."""
        from videocli.analyze import base
        from videocli.core.timeutil import parse_time
        from videocli.layout.ocr import text_similarity

        ws = state.workspace()
        src = resolve_source(file)
        ctx = base.RunContext(ws=ws, settings=state.settings, force=state.force)
        base.run_analyzers(src, ["layout"], ctx, explicit={"layout"})
        doc = base.load_artifact(ws, src, "layout") or {}
        wins = doc.get("windows", [])
        a, b = window_opts(frm, to)
        if at is not None:
            t = parse_time(at)
            wins = [w for w in wins if w["start"] <= t < w["end"]] or wins[-1:]
        if a is not None or b is not None:
            wins = [w for w in wins if (b is None or w["start"] < b) and (a is None or w["end"] > a)]
        if find:
            hits = []
            for w in wins:
                for kind in ("text", "blocks"):
                    for d in w.get(kind, []):
                        sim = text_similarity(find, d["text"])
                        if sim >= 0.75 or find.lower() in d["text"].lower():
                            hits.append({"window": w["id"], "start": w["start"], "end": w["end"], "box": d["box"],
                                         "text": d["text"], "match": round(sim, 2), "kind": "line" if kind == "text" else "block"})
            seen: set[tuple[str, tuple[float, ...]]] = set()
            hits = [h for h in hits if not ((h["window"], tuple(h["box"])) in seen or seen.add((h["window"], tuple(h["box"]))))]
            hits.sort(key=lambda h: (-h["match"], h["start"]))
            shown, total, trunc = limit_list(hits, limit)
            emit({"query": find, "total": total, "truncated": trunc, "hits": shown, "frame": doc.get("frame")},
                 warnings=ctx.warnings)
            return
        if not lines:
            wins = [{k: v for k, v in w.items() if k != "text"} for w in wins]
        shown, total, trunc = limit_list(wins, limit)
        emit({"provenance": doc.get("provenance"), "frame": doc.get("frame"), "total": total, "truncated": trunc, "windows": shown},
             warnings=ctx.warnings, next=[f"videocli layout {q(file)} --find \"TEXT\""])

    @formatter("layout")
    def _fmt_layout(d: dict[str, Any]) -> None:
        c = console()
        if "hits" in d:
            for h in d["hits"]:
                c.print(f"  {h['start']:7.2f}-{h['end']:7.2f}  {h['window']}  box {h['box']}  {h['text'][:70]}")
            c.print(f"{d['total']} hit(s) for {d['query']!r}")
            return
        for w in d["windows"]:
            big = sorted((bk for bk in w.get("blocks", []) if len(bk["text"]) >= 4), key=lambda bk: -bk["box"][2] * bk["box"][3])
            texts = "; ".join(bk["text"][:40] for bk in big[:3])
            c.print(f"  {w['id']} {w['start']:7.2f}-{w['end']:7.2f}  text {len(w.get('blocks', []))}  faces {len(w['faces'])}  "
                    f"objects {len(w.get('objects', []))}  free {len(w['free'])}  busy {w['busy']:.0%}  {texts}")
        c.print(f"{d['total']} window(s)" + (" (truncated)" if d["truncated"] else ""))

    # ------------------------------------------------------------------ log
    @command(app, "log", summary="Describe every shot through work orders the calling agent (or another provider) answers", writes=True, dry_run=True, estimate=True, exit_codes=(0, 2, 3, 4, 6), **P5)
    def log(path: str = typer.Argument(..., help="Media file or folder."), only_missing: bool = typer.Option(False, "--only-missing"),
            batch: int = typer.Option(20, "--batch", min=1, max=100, help="Shots per work order.")) -> None:
        """Shot logging."""
        from videocli.cli.commands.perception import MEDIA_EXT
        from videocli.core.fsutil import rel, write_json
        from videocli.media.frames import extract_many
        from videocli.media.sheets import Tile, compose
        from videocli.providers import orders as om

        ws = state.workspace()
        p = Path(path)
        files = sorted(f for f in p.rglob("*") if f.suffix.lower() in MEDIA_EXT) if p.is_dir() else [p]
        prov = om.provider(state.provider or state.settings.provider)
        totals = {"shots": 0, "described": 0, "pending": 0, "orders": []}
        pending_err = None
        for f in files:
            src = resolve_source(str(f))
            doc = _shots_doc(src, str(f))
            todo = [s for s in doc["shots"] if not (only_missing and s.get("description"))]
            totals["shots"] += len(doc["shots"])
            if state.estimate:
                totals["pending"] += len(todo)
                continue
            items = []
            for s in todo:
                sheet = ws.source_dir(src.id) / "sheets" / f"shot-{s['id']}.jpg"
                if not sheet.is_file():
                    d = s["end"] - s["start"]
                    ts = [round(s["start"] + d * k, 3) for k in (0.1, 0.5, 0.9)]
                    fr = extract_many(src.path, ts, ws.source_dir(src.id) / "frames", width=320)
                    compose([Tile(x, f"{s['id']} {t:.1f}s", t) for x, t in zip(fr, ts, strict=False)], sheet, cols=3, width=960)
                items.append({"id": f"{src.id}:{s['id']}", "images": [rel(sheet)],
                              "question": "Describe this shot for an editor in one sentence; give its size and whether it is usable.",
                              "answer_schema": {"description": "string", "shot_size": "ECU|CU|MCU|MS|WS|EWS", "angle?": "high|eye|low|overhead",
                                                "location?": "interior|exterior", "usable": "boolean"}})
            for i in range(0, len(items), batch):
                if state.dry_run:
                    totals["orders"].append({"items": len(items[i:i + batch]), "dry_run": True})
                    continue
                order = om.create(ws, "shot_log", items[i:i + batch], meta={"source_id": src.id},
                                  brief="Look at each sheet (first, middle, last frame). Be literal; say what is visible.")
                totals["orders"].append(order["id"])
                ans = om.answers(order)
                changed = False
                for s in doc["shots"]:
                    a = ans.get(f"{src.id}:{s['id']}")
                    if not a:
                        continue
                    v = a["answer"]
                    s["description"] = v["description"]
                    s["size"] = v.get("shot_size", s.get("size"))
                    s["angle"] = v.get("angle", s.get("angle"))
                    s["location"] = v.get("location", s.get("location"))
                    s["usable"] = bool(v.get("usable"))
                    s.setdefault("provenance", {})["description"] = f"{a['provider']}" + (f":{a['model']}" if a.get("model") else "")
                    changed = True
                if changed:
                    write_json(ws.source_dir(src.id) / "shots.json", doc)
                open_n = len(om.open_items(order))
                totals["pending"] += open_n
                if open_n and pending_err is None:
                    try:
                        prov.answer(ws, order, " ".join(["videocli", "log", q(path)] + (["--only-missing"] if only_missing else [])))
                    except Exception as exc:  # noqa: BLE001 - Pending or dependency errors surface after all orders exist
                        pending_err = exc
            totals["described"] += sum(1 for s in doc["shots"] if s.get("description"))
        if state.estimate:
            emit({"estimate": {"shots": totals["shots"], "items": totals["pending"], "images": totals["pending"],
                               "orders": -(-totals["pending"] // batch)}})
            return
        if pending_err is not None:
            raise pending_err
        emit(totals, next=[f"videocli shots {q(path)} --format compact"])

    # ------------------------------------------------------------------ orders
    orders = sub_app("orders", "Work orders waiting for answers.")
    app.add_typer(orders)

    @command(orders, "list", group="orders", summary="Work orders and how many items are open", exit_codes=(0,), **P5)
    def orders_list(open_only: bool = typer.Option(False, "--open")) -> None:
        from videocli.providers import orders as om

        ws = state.workspace()
        out = [{"id": o["id"], "kind": o["kind"], "status": o["status"], "items": len(o["items"]), "open": len(om.open_items(o)), "created": o["created"]}
               for o in om.all_orders(ws) if not open_only or (o["status"] == "pending")]
        emit({"total": len(out), "orders": out}, next=[f"videocli orders show {o['id']}" for o in out if o["open"]][:3])

    @command(orders, "show", group="orders", summary="An order's items: images, question and the answer schema to follow", exit_codes=(0, 4), **P5)
    def orders_show(oid: str = typer.Argument(..., metavar="ID"), limit: Optional[int] = typer.Option(None, "--limit", min=1)) -> None:
        from videocli.providers import orders as om

        o = om.load(state.workspace(), oid)
        items = om.open_items(o)
        shown, total, trunc = limit_list(items, limit)
        emit({"id": o["id"], "kind": o["kind"], "brief": o.get("brief"), "open": total, "truncated": trunc, "items": shown,
              "answer_format": 'one JSON object per line: {"id": ITEM_ID, "answer": {…}}'},
             next=[f"videocli orders submit {oid} answers.jsonl"])

    @command(orders, "submit", group="orders", summary="Store answers (JSON lines); each is validated against its schema, partial submissions are fine", writes=True, dry_run=False, exit_codes=(0, 2, 4), **P5)
    def orders_submit(oid: str = typer.Argument(..., metavar="ID"), answers_file: str = typer.Argument(..., metavar="ANSWERS.jsonl"),
                      model: Optional[str] = typer.Option(None, "--model", help="Model that produced the answers (provenance).")) -> None:
        from videocli.core.errors import file_not_found
        from videocli.providers import orders as om

        p = Path(answers_file)
        if not p.is_file():
            raise file_not_found(answers_file)
        res = om.submit(state.workspace(), oid, p.read_text(encoding="utf-8").splitlines(), provider=state.provider or "agent", model=model)
        o = om.load(state.workspace(), oid)
        emit(res, next=[o.get("meta", {}).get("rerun") or "Re-run the command that created the order"] if not res["open"] else [f"videocli orders show {oid}"])

    @command(orders, "cancel", group="orders", summary="Cancel an order", writes=True, dry_run=False, exit_codes=(0, 4), **P5)
    def orders_cancel(oid: str = typer.Argument(..., metavar="ID")) -> None:
        from videocli.providers import orders as om

        ws = state.workspace()
        o = om.load(ws, oid)
        o["status"] = "cancelled"
        om.save(ws, o)
        emit({"id": oid, "status": "cancelled"})

    # ------------------------------------------------------------------ suggest
    @command(app, "suggest", summary="Candidates with evidence (never changes an EDL): retakes, dead-shots, chapters", exit_codes=(0, 2, 4), **P5)
    def suggest(file: str = typer.Argument(...), what: str = typer.Argument(..., metavar="retakes|dead-shots|chapters"),
                limit: Optional[int] = typer.Option(None, "--limit", min=1)) -> None:
        from videocli.analyze.base import load_artifact
        from videocli.core.errors import UsageError
        from videocli.judge.suggest import chapters, dead_shots, retakes

        ws = state.workspace()
        src = resolve_source(file)
        arts = {n: load_artifact(ws, src, n, required=False) for n in ("transcript", "silence", "scenes", "shots", "slides")}
        if what == "retakes":
            items = retakes(arts["transcript"])
        elif what == "dead-shots":
            items = dead_shots(arts["shots"], arts["transcript"])
        elif what == "chapters":
            items = chapters(arts["transcript"], arts["silence"], arts["slides"], arts["scenes"])
        else:
            raise UsageError("BAD_ARGUMENT", "Choose retakes, dead-shots or chapters.")
        shown, total, trunc = limit_list(items, limit)
        nxt = []
        if what == "retakes" and shown:
            r = shown[0]
            nxt.append(f"videocli edl cut EDL --from {r['drop']['start']} --to {r['drop']['end']} --reason retake")
        emit({"kind": what, "total": total, "truncated": trunc, "suggestions": shown}, next=nxt)
