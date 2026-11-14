"""Compositing and sound commands (phase 6): edl overlay|audio|transition|split-edit|speed|reframe|stabilize|sound, export."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import typer

from videocli.cli.commands.edit import _load, _op_emit, _save, _time
from videocli.cli.output import artifact, emit
from videocli.cli.registry import command
from videocli.cli.state import state


def _source(file: str):
    from videocli.core.source import lookup
    from videocli.edl.build import source_ref

    ws = state.workspace()
    src = lookup(ws, file)
    ref, _ = source_ref(ws, src)
    return src.id, ref


def register(app: typer.Typer) -> None:
    edl_app = next(g.typer_instance for g in app.registered_groups if g.typer_instance and g.typer_instance.info.name == "edl")
    W = dict(writes=True, dry_run=True, exit_codes=(0, 2, 4), phase=6)

    @command(edl_app, "overlay", group="edl", summary="Put B-roll over a spine clip (cover) or picture-in-picture (pip), anchored so it follows the clip", **W)
    def edl_overlay(
        name: str = typer.Argument(...),
        source: str = typer.Option(..., "--source", metavar="FILE", help="Overlay media."),
        frm: str = typer.Option(..., "--from", metavar="T", help="Overlay source in-point."),
        to: str = typer.Option(..., "--to", metavar="T", help="Overlay source out-point."),
        on: str = typer.Option(..., "--on", metavar="CLIP_ID", help="Spine clip it is anchored to."),
        offset: float = typer.Option(0.0, "--offset", metavar="S", help="Seconds after the clip's first frame (output time)."),
        mode: str = typer.Option("cover", "--mode", help="cover or pip."),
        pos: Optional[str] = typer.Option(None, "--pos", help="pip corner tl|tr|bl|br or X,Y (0-1 or pixels)."),
        scale: Optional[float] = typer.Option(None, "--scale", min=0.05, max=1.0, help="pip width as a share of the frame (default 0.33)."),
        sound: bool = typer.Option(False, "--sound", help="Keep the overlay's own sound (default: muted)."),
        mute: bool = typer.Option(True, "--mute", hidden=True),
        opacity: float = typer.Option(1.0, "--opacity", min=0.0, max=1.0),
    ) -> None:
        from videocli.edl import ops

        edl = _load(name)
        sid, ref = _source(source)
        summ = ops.overlay(edl, sid, ref, _time(frm), _time(to), on=on, offset=offset, mode=mode, pos=pos, scale=scale,  # type: ignore[arg-type]
                           mute=not sound, opacity=opacity)
        _op_emit(name, edl, summ, _save(edl, "overlay", item=summ["item"], on=on))

    @command(edl_app, "audio", group="edl", summary="Add music, an effect or room tone at an output time, with gain, ducking under speech, fades and --fit", **W)
    def edl_audio(
        name: str = typer.Argument(...),
        source: str = typer.Option(..., "--source", metavar="FILE"),
        role: str = typer.Option("music", "--role", help="music, sfx or tone."),
        at: float = typer.Option(0.0, "--at", metavar="OUT_T", help="Output time where it starts."),
        frm: Optional[str] = typer.Option(None, "--from", metavar="T", help="Source in-point."),
        to: Optional[str] = typer.Option(None, "--to", metavar="T", help="Source out-point."),
        gain: float = typer.Option(0.0, "--gain", metavar="DB"),
        duck: Optional[float] = typer.Option(None, "--duck", metavar="DB", help="Lower by this much under speech (e.g. -10)."),
        fade: float = typer.Option(0.0, "--fade", metavar="S", min=0.0),
        fit: bool = typer.Option(False, "--fit", help="Loop or trim to the end of the edit."),
    ) -> None:
        from videocli.edl import ops

        edl = _load(name)
        sid, ref = _source(source)
        summ = ops.audio(edl, sid, ref, role=role, at=at, a=_time(frm) or 0.0, b=_time(to), gain_db=gain, duck_db=duck, fade=fade, fit=fit)
        _op_emit(name, edl, summ, _save(edl, "audio", item=summ["item"]))

    @command(edl_app, "transition", group="edl", summary="Transition after a spine clip: dissolve, fade (through black), dip (through a colour) or cut; shortens the edit by the overlap", **W)
    def edl_transition(
        name: str = typer.Argument(...), clip_id: str = typer.Argument(..., metavar="CLIP_ID"),
        ttype: str = typer.Option("dissolve", "--type", help="dissolve, fade, dip or cut (removes)."),
        duration: float = typer.Option(0.5, "--duration", metavar="S", min=0.04, max=10.0),
        color: Optional[str] = typer.Option(None, "--color", help="dip colour: black or white."),
    ) -> None:
        from videocli.core.errors import UsageError
        from videocli.edl import ops

        if ttype not in ("dissolve", "fade", "dip", "cut"):
            raise UsageError("BAD_ARGUMENT", "--type must be dissolve, fade, dip or cut.")
        edl = _load(name)
        summ = ops.transition(edl, clip_id, ttype, duration, color)
        _op_emit(name, edl, summ, _save(edl, "transition", clip=clip_id, type=ttype, duration=duration))

    @command(edl_app, "split-edit", group="edl", summary="J/L cut: sound starts before (--lead) or runs past (--lag) the picture cut", **W)
    def edl_split_edit(name: str = typer.Argument(...), clip_id: str = typer.Argument(..., metavar="CLIP_ID"),
                       lead: Optional[float] = typer.Option(None, "--lead", metavar="S", min=0.0, max=10.0),
                       lag: Optional[float] = typer.Option(None, "--lag", metavar="S", min=0.0, max=10.0)) -> None:
        from videocli.edl import ops

        edl = _load(name)
        summ = ops.split_edit(edl, clip_id, lead, lag)
        _op_emit(name, edl, summ, _save(edl, "split-edit", clip=clip_id, lead=lead, lag=lag))

    @command(edl_app, "speed", group="edl", summary="Constant speed (--rate), a ramp (--ramp POS:RATE,…) or a freeze of the last frame (--freeze S)", **W)
    def edl_speed(name: str = typer.Argument(...), clip_id: str = typer.Argument(..., metavar="CLIP_ID"),
                  rate: Optional[float] = typer.Option(None, "--rate"),
                  ramp: Optional[str] = typer.Option(None, "--ramp", metavar="0:1.0,0.6:2.5,1:1.0"),
                  freeze: Optional[float] = typer.Option(None, "--freeze", metavar="S", min=0.0, max=60.0)) -> None:
        from videocli.edl import ops

        edl = _load(name)
        summ = ops.speed(edl, clip_id, rate=rate, ramp=ramp, freeze=freeze)
        _op_emit(name, edl, summ, _save(edl, "speed", clip=clip_id, rate=rate, ramp=ramp, freeze=freeze))

    @command(edl_app, "reframe", group="edl", summary="Vertical, square or 4:5 output with a crop per shot (center, or subject from face data)", **W)
    def edl_reframe(name: str = typer.Argument(...),
                    aspect: str = typer.Option("9:16", "--aspect", help="9:16, 1:1, 4:5, 4:3 or 16:9."),
                    mode: str = typer.Option("center", "--mode", help="center or subject."),
                    clip: Optional[str] = typer.Option(None, "--clip", metavar="CLIP_ID", help="Set one clip's crop centre with --x."),
                    x: Optional[float] = typer.Option(None, "--x", min=0.0, max=1.0, help="Crop centre, 0 (left) to 1 (right).")) -> None:
        from videocli.edl import ops

        edl = _load(name)
        warnings = []
        if mode == "subject" and clip is None:
            from videocli.core.cache import read_artifact

            ws = state.workspace()
            crops = {}
            for c in edl.tracks.main:
                doc = read_artifact(ws.source_dir(c.source) / "faces.json")
                if not doc:
                    continue
                xs = [f["x"] for f in doc.get("faces", []) if c.start <= f.get("t", -1) < c.end]
                if xs:
                    xs.sort()
                    crops[c.id] = round(xs[len(xs) // 2], 3)  # one crop per shot: the median face position
            if not crops:
                warnings.append({"code": "REFRAME_CENTER_FALLBACK", "message": "No face data (run analyze --only faces, phase 5); using a centre crop."})
        summ = ops.reframe(edl, aspect, mode, clip, x)
        if mode == "subject" and clip is None and edl.reframe is not None:
            edl.reframe.crops.update(crops)  # type: ignore[possibly-undefined]
        path = _save(edl, "reframe", aspect=aspect, mode=mode, clip=clip, x=x)
        emit({**summ, "dry_run": state.dry_run}, artifacts=[artifact("edl", path)] if path else [], warnings=warnings,
             next=[f"videocli render {name} --preview -o {name}-{aspect.replace(':', 'x')}.mp4"])

    @command(edl_app, "stabilize", group="edl", summary="Stabilise a clip (two-pass, baked and cached); --strength 0 removes it", **W)
    def edl_stabilize(name: str = typer.Argument(...), clip_id: str = typer.Argument(..., metavar="CLIP_ID"),
                      strength: float = typer.Option(0.5, "--strength", min=0.0, max=1.0)) -> None:
        from videocli.edl import ops

        edl = _load(name)
        summ = ops.stabilize(edl, clip_id, strength)
        _op_emit(name, edl, summ, _save(edl, "stabilize", clip=clip_id, strength=strength))

    @command(edl_app, "sound", group="edl", summary="Dialogue clean-up (denoise, levelling, compression, high-pass) and room tone under gaps", **W)
    def edl_sound(name: str = typer.Argument(...),
                  denoise: Optional[float] = typer.Option(None, "--denoise", min=0.0, max=1.0, metavar="F"),
                  level: Optional[bool] = typer.Option(None, "--level/--no-level", help="Even out levels across speakers."),
                  compress: Optional[bool] = typer.Option(None, "--compress/--no-compress", help="Gentle compression."),
                  highpass: Optional[float] = typer.Option(None, "--highpass", metavar="HZ", help="High-pass cutoff (0 removes)."),
                  room_tone: Optional[str] = typer.Option(None, "--room-tone", metavar="auto|FILE|none")) -> None:
        from videocli.edl import ops

        edl = _load(name)
        rt = room_tone
        if rt not in (None, "auto", "none", "off", ""):
            sid, ref = _source(rt)  # type: ignore[arg-type]
            ops.add_source(edl, sid, ref)
            rt = sid
        summ = ops.sound(edl, denoise=denoise, level=level, compress=compress, highpass=highpass, room_tone=rt)
        path = _save(edl, "sound", **{k: v for k, v in summ["sound"].items()})
        emit({**summ, "dry_run": state.dry_run}, artifacts=[artifact("edl", path)] if path else [],
             next=[f"videocli render {name} --preview -o {name}-preview.mp4"])

    # ------------------------------------------------------------------ export
    @command(app, "export", summary="Hand the timeline to an editor: OpenTimelineIO, Final Cut XML or CMX 3600 (with a round-check)",
             writes=True, dry_run=False, exit_codes=(0, 1, 2, 3, 4), phase=6)
    def export_cmd(
        name: str = typer.Argument(...),
        to: str = typer.Option(..., "--to", help="otio, fcpxml or edl (CMX 3600)."),
        output: Optional[str] = typer.Option(None, "-o", "--output", metavar="PATH"),
        relative_to: Optional[str] = typer.Option(None, "--relative-to", metavar="DIR", help="Write media paths relative to DIR."),
    ) -> None:
        """Export an EDL."""
        from videocli.export import export

        edl = _load(name)
        res = export(edl, to, Path(output) if output else None, Path(relative_to) if relative_to else None)
        warnings: list[dict[str, Any]] = []
        if res.get("left_out"):
            warnings.append({"code": "EXPORT_PARTIAL", "message": f"Not in the {to} file: {'; '.join(res['left_out'])}."})
        emit(res, artifacts=[artifact(f"export-{to}", res["output"])], warnings=warnings)
