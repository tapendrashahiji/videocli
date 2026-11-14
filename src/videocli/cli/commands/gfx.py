"""Motion graphics commands (phase 8): gfx …, brand set, captions style."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

import typer

from videocli.cli.commands.edit import _load, _save, _transcripts
from videocli.cli.output import artifact, console, emit, formatter
from videocli.cli.registry import command, sub_app
from videocli.cli.state import state


def describe_issue(i: dict[str, Any]) -> str:
    """One readable line for a lint issue: what, which item, and the key numbers."""
    who = i.get("item") or i.get("clip") or ", ".join(i.get("items", []))
    bits = []
    if i.get("covers"):
        c = i["covers"][0]
        what = c.get("text") or "a face"
        bits.append(f"covers {what[:40]!r}" + (f" (+{len(i['covers']) - 1} more)" if len(i["covers"]) > 1 else ""))
    if "needed" in i:
        bits.append(f"{i.get('duration')}s, needs {i['needed']}s")
    if i.get("text") and i["rule"] == "crop_cuts_text":
        bits.append(f"cuts {i['text'][0]!r}; x {i.get('x')} → {i.get('suggested_x')}")
    if "at" in i and not bits:
        bits.append(f"at {i['at']}s")
    return f"{i['rule']:22} {who or '':8} {'; '.join(bits)}"


def print_lint(d: dict[str, Any]) -> None:
    from videocli.cli.output import console

    c = console()
    state_ = "[green]passed[/green]" if d.get("passed") else "[red]failed[/red]"
    c.print(f"{d['name']}: {state_}  ({d['errors']} error(s), {d['warnings']} warning(s))")
    for i in d.get("issues", []):
        color = "red" if i["severity"] == "error" else "yellow"
        c.print(f"  [{color}]{i['severity']:5}[/{color}] {describe_issue(i)}", highlight=False, markup=True)
        if i.get("fix"):
            c.print(f"        [dim]fix:[/dim] {i['fix']}", highlight=False)


def register(app: typer.Typer) -> None:
    W: dict[str, Any] = {"writes": True, "dry_run": True, "exit_codes": (0, 2, 4), "phase": 8}
    gfx = sub_app("gfx", "Titles, lower thirds, callouts and other graphics from templates, timed to the spoken word.")
    app.add_typer(gfx)

    @command(gfx, "templates", group="gfx", summary="Available templates (project, user, built-in) with their parameters", exit_codes=(0,), phase=8)
    def gfx_templates(baker: Optional[str] = typer.Option(None, "--baker", help="titles, manim, html or generic.")) -> None:
        from videocli.graphics import templates as tp
        from videocli.setup.profiles import check_group

        ws = state.existing_workspace()
        out = []
        for name, t in sorted(tp.discover(ws).items()):
            if baker and t.baker != baker:
                continue
            available = True if t.baker in ("titles", "generic") else check_group(t.baker, deep=False).ok
            out.append({"name": name, "baker": t.baker, "available": available, "duration": t.duration.default,
                        "params": {k: {kk: vv for kk, vv in v.model_dump().items() if vv not in (None, False)} for k, v in t.params.items()},
                        "folder": t.folder})
        emit({"total": len(out), "templates": out}, next=["videocli gfx add NAME --template lower-third --param title=… --on c0001"])

    @formatter("gfx templates")
    def _fmt_t(d: dict[str, Any]) -> None:
        c = console()
        for t in d["templates"]:
            c.print(f"{t['name']:16} {t['baker']:8} {'' if t['available'] else '(not installed) '}params: {', '.join(t['params'])}")

    @command(gfx, "add", group="gfx", summary="Add a graphic anchored to a clip (--on) or to a spoken phrase (--at-word, --land end)", **W)
    def gfx_add(
        name: str = typer.Argument(...),
        template: str = typer.Option(..., "--template", "-t"),
        param: list[str] = typer.Option([], "--param", metavar="k=v", help="Template parameter (repeatable)."),
        on: Optional[str] = typer.Option(None, "--on", metavar="CLIP_ID"),
        offset: float = typer.Option(0.0, "--offset", metavar="S"),
        at_word: Optional[str] = typer.Option(None, "--at-word", metavar="PHRASE"),
        land: str = typer.Option("start", "--land", help="start (appear on the word) or end (reveal finishes on it)."),
        duration: Optional[float] = typer.Option(None, "--duration", metavar="S", min=0.2),
        layer: int = typer.Option(0, "--layer", min=0, max=9),
        place: str = typer.Option("auto", "--place", help="auto: move off on-screen text and faces (needs layout data); keep: template position."),
    ) -> None:
        from videocli.core.errors import UsageError
        from videocli.edl.model import Graphic
        from videocli.edl.ops import get_clip
        from videocli.graphics import templates as tp
        from videocli.graphics.timing import place_at_word, reading_time

        ws = state.workspace()
        edl = _load(name)
        t = tp.get(ws, template)
        given = tp.parse_kv(param)
        params = tp.resolve_params(t, given, tp.load_brand(ws))  # validate before writing
        info: dict[str, Any] = {}
        if (on is None) == (at_word is None):
            raise UsageError("BAD_ARGUMENT", "Give exactly one of --on CLIP_ID or --at-word PHRASE.")
        if at_word is not None:
            info = place_at_word(edl, _transcripts(edl), at_word, land=land, reveal=t.duration.in_)
            anchor, off = info["anchor"], info["offset"]
        else:
            get_clip(edl, on)  # type: ignore[arg-type]
            anchor, off = on, offset
        text = tp.text_of(params)
        dur = duration or max(t.duration.default, t.duration.min, reading_time(text) if text else 0.0)
        if place not in ("auto", "keep"):
            raise UsageError("BAD_ARGUMENT", "--place must be auto or keep.")
        placement: dict[str, Any] = {}
        if place == "auto" and "box_x" not in given and "box_y" not in given:
            from videocli.layout.place import auto_place

            sp = edl.timemap().span(anchor)
            if sp is not None:
                placement = auto_place(ws, edl, t.name, given, sp.out_start + off, sp.out_start + off + dur)
                if placement.get("moved"):
                    given = {**given, **{k: str(v) for k, v in placement["params"].items()}}
        gid = edl.next_id("x")
        edl.tracks.graphics.append(Graphic(id=gid, baker=t.baker, template=t.name, params=given, anchor=anchor, offset=off,  # type: ignore[arg-type]
                                           duration=round(dur, 3), layer=layer))
        path = _save(edl, "gfx add", item=gid, template=t.name)
        emit({"item": gid, "template": t.name, "anchor": anchor, "offset": off, "duration": round(dur, 3), "timing": info,
              "placement": placement, "dry_run": state.dry_run},
             artifacts=[artifact("edl", path)] if path else [],
             next=[f"videocli gfx lint {name}", f"videocli gfx preview {name} {gid} --still {round(t.duration.in_ + 0.2, 2)}"])

    @command(gfx, "list", group="gfx", summary="Graphics in an EDL with their output times", exit_codes=(0, 4), phase=8)
    def gfx_list(name: str = typer.Argument(...)) -> None:
        edl = _load(name)
        tm = edl.timemap()
        out = []
        for g in edl.tracks.graphics:
            sp = tm.span(g.anchor)
            at = round(sp.out_start + g.offset, 3) if sp else None
            out.append({"id": g.id, "template": g.template, "baker": g.baker, "anchor": g.anchor, "offset": g.offset, "at": at,
                        "duration": g.duration, "layer": g.layer, "params": g.params, "orphan": sp is None})
        emit({"total": len(out), "graphics": out})

    @command(gfx, "set", group="gfx", summary="Change a graphic's parameters, duration, offset or layer", **W)
    def gfx_set(name: str = typer.Argument(...), item: str = typer.Argument(..., metavar="ITEM_ID"),
                param: list[str] = typer.Option([], "--param", metavar="k=v"), duration: Optional[float] = typer.Option(None, "--duration", min=0.2),
                offset: Optional[float] = typer.Option(None, "--offset"), layer: Optional[int] = typer.Option(None, "--layer", min=0, max=9),
                place: Optional[str] = typer.Option(None, "--place", help="auto: move off on-screen text and faces; keep: back to the template position.")) -> None:
        from videocli.core.errors import InputError, UsageError
        from videocli.graphics import templates as tp

        ws = state.workspace()
        edl = _load(name)
        g = next((x for x in edl.tracks.graphics if x.id == item), None)
        if g is None:
            raise InputError("ITEM_NOT_FOUND", f"No graphic {item!r} in {name}.")
        new = {**g.params, **tp.parse_kv(param)}
        tp.resolve_params(tp.get(ws, g.template), new, tp.load_brand(ws))
        g.params = new
        if duration is not None:
            g.duration = duration
        if offset is not None:
            g.offset = offset
        if layer is not None:
            g.layer = layer
        placement: dict[str, Any] = {}
        if place is not None:
            if place not in ("auto", "keep"):
                raise UsageError("BAD_ARGUMENT", "--place must be auto or keep.")
            base = {k: v for k, v in g.params.items() if k not in ("box_x", "box_y")}
            g.params = base
            if place == "auto":
                from videocli.layout.place import auto_place

                sp = edl.timemap().span(g.anchor)
                if sp is not None:
                    placement = auto_place(ws, edl, g.template, base, sp.out_start + g.offset, sp.out_start + g.offset + g.duration)
                    if placement.get("moved"):
                        g.params = {**base, **{k: str(v) for k, v in placement["params"].items()}}
        path = _save(edl, "gfx set", item=item)
        emit({"item": item, "params": g.params, "duration": g.duration, "offset": g.offset, "placement": placement, "dry_run": state.dry_run},
             artifacts=[artifact("edl", path)] if path else [])

    @command(gfx, "remove", group="gfx", summary="Remove a graphic", **W)
    def gfx_remove(name: str = typer.Argument(...), item: str = typer.Argument(..., metavar="ITEM_ID")) -> None:
        from videocli.core.errors import InputError

        edl = _load(name)
        before = len(edl.tracks.graphics)
        edl.tracks.graphics = [g for g in edl.tracks.graphics if g.id != item]
        if len(edl.tracks.graphics) == before:
            raise InputError("ITEM_NOT_FOUND", f"No graphic {item!r} in {name}.")
        path = _save(edl, "gfx remove", item=item)
        emit({"removed": item, "dry_run": state.dry_run}, artifacts=[artifact("edl", path)] if path else [])

    @command(gfx, "preview", group="gfx", summary="One composited frame (--still T, seconds into the graphic) or a short clip of the item over the real footage", writes=True, dry_run=False, exit_codes=(0, 1, 2, 4), phase=8)
    def gfx_preview(name: str = typer.Argument(...), item: str = typer.Argument(..., metavar="ITEM_ID"),
                    still: Optional[float] = typer.Option(None, "--still", metavar="T"), clip: bool = typer.Option(False, "--clip")) -> None:
        import cv2
        import numpy as np

        from videocli.core.errors import InputError, UsageError
        from videocli.core.fsutil import rel
        from videocli.graphics import templates as tp
        from videocli.graphics.titles import frame
        from videocli.media.frames import extract_many

        ws = state.workspace()
        edl = _load(name)
        g = next((x for x in edl.tracks.graphics if x.id == item), None)
        if g is None:
            raise InputError("ITEM_NOT_FOUND", f"No graphic {item!r} in {name}.")
        t = tp.get(ws, g.template)
        brand = tp.load_brand(ws)
        params = tp.resolve_params(t, g.params, brand)
        sp = edl.timemap().span(g.anchor)
        if sp is None:
            raise InputError("ITEM_NOT_FOUND", f"{item} is anchored to a missing clip.")
        W, H = edl.resolution
        out_dir = ws.dir("reports/gfx")
        if clip:
            from videocli.media import ffmpeg
            from videocli.render.bakers.graphics import graphic_node, run_graphic
            from videocli.render.graph import record

            n = max(1, round(g.duration * __import__("videocli.core.timeutil", fromlist=["Rate"]).Rate.parse(edl.rate).fps))
            node = graphic_node(t, params, brand, (W, H), edl.rate, n, g.id)
            tmp = out_dir / f"{item}-bake"
            if tmp.exists():
                shutil.rmtree(tmp)
            tmp.mkdir()
            fname = run_graphic(node, tmp, [])
            record(tmp, fname)
            src = edl.sources[sp.source]
            s0 = sp.start + g.offset * sp.speed
            out = out_dir / f"{name}-{item}.mp4"
            ffmpeg.run(["-y", "-ss", f"{s0:.3f}", "-t", f"{g.duration:.3f}", "-i", ffmpeg.fpath(src.path), "-i", ffmpeg.fpath(tmp / fname),
                        "-filter_complex", f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[b];"
                        "[b][1:v]overlay=format=auto[v]", "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", ffmpeg.fpath(out)], log_name="gfx-preview")
            emit({"item": item, "path": rel(out)}, artifacts=[artifact("video", out)])
            return
        if still is None:
            still = min(g.duration, t.duration.in_ + 0.3)
        if not 0 <= still <= g.duration:
            raise UsageError("BAD_ARGUMENT", f"--still must be within 0..{g.duration}.")
        src = edl.sources[sp.source]
        src_t = sp.start + (g.offset + still) * sp.speed
        base = np.zeros((H, W, 3), dtype=np.uint8)
        if src.has_video:
            p = extract_many(Path(src.path), [round(src_t, 3)], ws.source_dir(sp.source) / "frames", width=W)[0]
            img = cv2.imread(str(p))
            if img is not None:
                base = cv2.resize(img, (W, H))
        over = np.asarray(frame(t, params, brand, W, H, still, g.duration))
        a = over[:, :, 3:4].astype(np.float32) / 255
        comp = (base.astype(np.float32) * (1 - a) + over[:, :, 2::-1].astype(np.float32) * a).astype(np.uint8)
        out = out_dir / f"{name}-{item}-{still:.2f}.jpg"
        cv2.imwrite(str(out), comp)
        emit({"item": item, "still": still, "path": rel(out)}, artifacts=[artifact("still", out)])

    @command(gfx, "lint", group="gfx", summary="Graphics readability and placement: length, safe area, reading time, collisions, captions, contrast", exit_codes=(0, 4, 5), phase=8)
    def gfx_lint(name: str = typer.Argument(...)) -> None:
        from videocli.core.errors import CheckFailed
        from videocli.graphics.lint import lint

        edl = _load(name)
        res = lint(edl, state.workspace())
        if not res["passed"]:
            raise CheckFailed("GFX_LINT_FAILED", f"{res['errors']} graphics error(s) in {name}.", details=res,
                              hint=next((f"Run: {i['fix']}" for i in res["issues"] if i.get("fix")), "See details.issues"))
        emit(res)

    @formatter("gfx lint")
    def _fmt_gfx_lint(d: dict[str, Any]) -> None:
        print_lint(d)

    @command(gfx, "new-template", group="gfx", summary="Scaffold a working template folder for a baker", writes=True, dry_run=False, exit_codes=(0, 2), phase=8)
    def gfx_new_template(directory: str = typer.Argument(..., metavar="DIR"), baker: str = typer.Option("titles", "--baker")) -> None:
        from videocli.core.errors import UsageError
        from videocli.core.fsutil import write_json

        d = Path(directory)
        if (d / "template.json").exists():
            raise UsageError("BAD_ARGUMENT", f"{d} already holds a template.")
        d.mkdir(parents=True, exist_ok=True)
        name = d.name
        if baker == "titles":
            manifest = {"schema": "videocli.template/v1", "name": name, "baker": "titles", "design": "lower-third",
                        "params": {"title": {"type": "string", "required": True, "max_chars": 32}, "subtitle": {"type": "string", "default": ""},
                                   "accent": {"type": "color", "default": "brand.accent"}, "animation": {"type": "choice", "choices": ["slide", "fade", "wipe"], "default": "fade"}},
                        "duration": {"default": 4.0, "min": 2.0, "in": 0.4, "out": 0.3}, "placement": {"anchor": "bottom-left", "safe_area": True}}
        elif baker == "manim":
            manifest = {"schema": "videocli.template/v1", "name": name, "baker": "manim", "entry": "scene.py",
                        "params": {"equation": {"type": "string", "required": True}}, "duration": {"default": 4.0, "min": 1.0, "in": 0.5, "out": 0.5}}
            (d / "scene.py").write_text(
                "import json, os\nfrom manim import *\n\n\nclass Main(Scene):\n    def construct(self):\n"
                "        p = json.load(open(os.environ['VIDEOCLI_PARAMS']))\n        eq = MathTex(p['equation'])\n"
                "        self.play(Write(eq), run_time=0.8)\n        self.wait(max(0.1, p['_duration'] - 1.6))\n        self.play(FadeOut(eq), run_time=0.8)\n",
                encoding="utf-8")
        elif baker == "html":
            manifest = {"schema": "videocli.template/v1", "name": name, "baker": "html", "entry": "index.html",
                        "params": {"title": {"type": "string", "required": True}}, "duration": {"default": 3.0, "min": 1.0, "in": 0.4, "out": 0.4}}
            (d / "index.html").write_text("<!doctype html><html><body style='margin:0;background:transparent'>"
                                          "<h1 id='t' style='font:700 8vh sans-serif;color:#fff'></h1>"
                                          "<script>document.getElementById('t').textContent=(window.props||{}).title||''</script></body></html>\n",
                                          encoding="utf-8")
        elif baker == "generic":
            manifest = {"schema": "videocli.template/v1", "name": name, "baker": "generic", "entry": name,
                        "params": {}, "duration": {"default": 3.0, "min": 0.5, "in": 0.0, "out": 0.0}}
        else:
            raise UsageError("BAD_ARGUMENT", "--baker must be titles, manim, html or generic.")
        write_json(d / "template.json", manifest)
        emit({"template": name, "baker": baker, "folder": str(d)}, artifacts=[artifact("template", d / "template.json")],
             next=["Move the folder under .videocli/project/templates/ to use it in this project", "videocli gfx templates"])

    # ------------------------------------------------------------------ brand
    brand = sub_app("brand", "The project's brand kit: font, colours, logo, safe margin.")
    app.add_typer(brand)

    @command(brand, "set", group="brand", summary="Set the brand kit used by every graphic (re-render to update them all)", writes=True, dry_run=True, exit_codes=(0, 2), phase=8)
    def brand_set(font: Optional[str] = typer.Option(None, "--font", metavar="FILE"), font_bold: Optional[str] = typer.Option(None, "--font-bold", metavar="FILE"),
                  colors: Optional[str] = typer.Option(None, "--colors", metavar="#111,#fff,#e63", help="primary, secondary, accent"),
                  logo: Optional[str] = typer.Option(None, "--logo", metavar="FILE"),
                  safe_margin: Optional[float] = typer.Option(None, "--safe-margin", min=0.0, max=0.2)) -> None:
        from videocli.core.errors import UsageError
        from videocli.graphics import templates as tp

        ws = state.workspace()
        b = tp.load_brand(ws)
        for f in (font, font_bold, logo):
            if f and not Path(f).is_file():
                raise UsageError("BAD_ARGUMENT", f"No file at {f}.")
        if font:
            b.font = str(Path(font).resolve())
        if font_bold:
            b.font_bold = str(Path(font_bold).resolve())
        if logo:
            b.logo = str(Path(logo).resolve())
        if colors:
            cs = [c.strip() for c in colors.split(",")]
            for c in cs:
                if not tp.COLOR_RE.match(c):
                    raise UsageError("BAD_ARGUMENT", f"{c!r} is not a #rrggbb colour.")
            for k, c in zip(("primary", "secondary", "accent"), cs, strict=False):
                b.colors[k] = c
        if safe_margin is not None:
            b.safe_margin = safe_margin
        path = None if state.dry_run else tp.save_brand(ws, b)
        emit({"brand": b.model_dump(), "dry_run": state.dry_run}, artifacts=[artifact("brand", path)] if path else [])

    # ------------------------------------------------------------------ captions style
    def _check_caption_params(p: dict[str, str]) -> None:
        from videocli.core.errors import UsageError

        def bad(msg: str) -> UsageError:
            return UsageError("BAD_ARGUMENT", msg, hint="Layout: max_chars=10-80 lines=1|2 position=auto|bottom|top margin=0-0.45")

        checks = {
            "max_chars": (int, 10, 80), "lines": (int, 1, 2), "margin": (float, 0.0, 0.45), "size": (float, 6, 400),
        }
        for k, (kind, lo, hi) in checks.items():
            if k in p:
                try:
                    v = kind(p[k])
                except ValueError:
                    raise bad(f"{k} must be a number, got {p[k]!r}.") from None
                if not lo <= v <= hi:
                    raise bad(f"{k} must be between {lo} and {hi}, got {p[k]}.")
        if "box" in p and str(p["box"]).lower() not in ("0", "1", "true", "false", "yes", "no", "on", "off"):
            raise bad(f"box must be 1 or 0, got {p['box']!r}.")
        if "position" in p and p["position"] not in ("auto", "bottom", "top"):
            raise bad(f"position must be auto, bottom or top, got {p['position']!r}.")

    @command(app, "captions-style", summary="Caption style for burned captions: clean, bold-words, karaoke or custom (--param size=… color=… accent=… max_chars=… lines=1|2 position=auto|bottom|top margin=… box=1)", **W)
    def captions_style(name: str = typer.Argument(...),
                       preset: Optional[str] = typer.Option(None, "--preset", help="clean, bold-words, karaoke or custom (default: keep the current one)."),
                       param: list[str] = typer.Option([], "--param", metavar="k=v", help="Changes only these keys; the others are kept."),
                       unset: list[str] = typer.Option([], "--unset", metavar="KEY", help="Remove a parameter (back to its default)."),
                       burn: Optional[bool] = typer.Option(None, "--burn/--no-burn", help="Default: keep the current mode.")) -> None:
        from videocli.core.errors import UsageError
        from videocli.graphics.templates import parse_kv

        if preset is not None and preset not in ("clean", "bold-words", "karaoke", "custom"):
            raise UsageError("BAD_ARGUMENT", "--preset must be clean, bold-words, karaoke or custom.")
        new = parse_kv(param)
        _check_caption_params(new)
        edl = _load(name)
        if preset is not None:
            edl.captions.preset = preset  # type: ignore[assignment]
        edl.captions.params = {k: v for k, v in {**edl.captions.params, **new}.items() if k not in unset}
        if burn is not None:
            edl.captions.mode = "burn" if burn else "sidecar"
        elif edl.captions.mode == "none":
            edl.captions.mode = "burn"  # styling captions the first time turns burning on
        preset = edl.captions.preset
        path = _save(edl, "captions style", preset=preset)
        emit({"preset": preset, "params": edl.captions.params, "mode": edl.captions.mode, "dry_run": state.dry_run},
             artifacts=[artifact("edl", path)] if path else [], next=[f"videocli render {name} --burn-captions -o {name}.mp4"])
