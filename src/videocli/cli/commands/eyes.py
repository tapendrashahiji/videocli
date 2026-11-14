"""Eyes and verify: scenes, frames, sheet, strip, verify (phase 3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import typer

from videocli.cli.commands.perception import limit_list, load_required, q, resolve_source, window_opts
from videocli.cli.output import artifact, console, emit, formatter
from videocli.cli.registry import command
from videocli.cli.state import state


def _times(spec: str) -> list[float]:
    from videocli.core.timeutil import parse_time

    return [parse_time(t) for t in spec.split(",") if t.strip()]


def _ensure_scenes(src, file: str) -> dict[str, Any]:
    from videocli.analyze import base
    from videocli.core.cache import read_artifact

    ws = state.workspace()
    doc = read_artifact(ws.source_dir(src.id) / "scenes.json")
    if doc is None:
        ctx = base.RunContext(ws=ws, settings=state.settings, options={})
        base.run_analyzers(src, ["scenes"], ctx, explicit={"scenes"})
        from videocli.index import build as index_build

        index_build.build(ws, src)
        doc = read_artifact(ws.source_dir(src.id) / "scenes.json")
    assert doc is not None
    return doc


def cut_tiles(edl, pairs: list[tuple[str, str]] | None, width: int) -> list:
    """Two tiles per join: the last frame before it and the first frame after it."""
    from videocli.core.timeutil import Rate, ms
    from videocli.media.frames import extract_many
    from videocli.media.sheets import Tile

    ws = state.workspace()
    rate = Rate.parse(edl.rate)
    tm = edl.timemap()
    tiles = []
    main = edl.tracks.main
    for n, (a, b, sp) in enumerate(zip(main, main[1:], tm.spans, strict=False), 1):
        if pairs is not None and (a.id, b.id) not in pairs:
            continue
        ta = max(a.start, a.end - rate.frame_duration * a.speed)
        tb = b.start
        fa = extract_many(Path(edl.sources[a.source].path), [ta], ws.source_dir(a.source) / "frames", width=width)[0]
        fb = extract_many(Path(edl.sources[b.source].path), [tb], ws.source_dir(b.source) / "frames", width=width)[0]
        out_t = ms(sp.out_end)
        tiles.append(Tile(fa, f"{n}: {a.id} out {ms(ta):.2f}", out_t, {"join": n, "clip": a.id, "side": "before", "source_time": ms(ta), "output_time": out_t}))
        tiles.append(Tile(fb, f"{n}: {b.id} in {ms(tb):.2f}", out_t, {"join": n, "clip": b.id, "side": "after", "source_time": ms(tb), "output_time": out_t}))
    return tiles


def register(app: typer.Typer) -> None:
    # ------------------------------------------------------------------ scenes
    @command(app, "scenes", summary="Shot boundaries (PySceneDetect), cached per parameters", exit_codes=(0, 1, 2, 4))
    def scenes(
        file: str = typer.Argument(..., help="Media file or source id."),
        detector: str = typer.Option("adaptive", "--detector", help="adaptive or content."),
        threshold: Optional[float] = typer.Option(None, "--threshold", help="Detector threshold (adaptive 3.0, content 27)."),
        min_scene: float = typer.Option(0.6, "--min-scene", metavar="S", min=0.04, help="Shortest shot in seconds."),
        limit: Optional[int] = typer.Option(None, "--limit", min=1),
        frm: Optional[str] = typer.Option(None, "--from", metavar="T"),
        to: Optional[str] = typer.Option(None, "--to", metavar="T"),
    ) -> None:
        """Detect shots."""
        from videocli.analyze import base
        from videocli.index import build as index_build
        from videocli.index.views import in_window

        ws = state.workspace()
        src = resolve_source(file)
        ctx = base.RunContext(ws=ws, settings=state.settings, force=state.force,
                              options={"detector": detector, "threshold": threshold, "min_scene": min_scene})
        res = base.run_analyzers(src, ["scenes"], ctx, explicit={"scenes"})
        if res.ran:
            index_build.build(ws, src)
        doc = load_required(src, "scenes", file)
        a, b = window_opts(frm, to)
        items = [s for s in doc["scenes"] if in_window(s["start"], s["end"], a, b)]
        shown, total, trunc = limit_list(items, limit)
        emit({"source_id": src.id, "detector": doc["params"]["detector"], "threshold": doc["params"]["threshold"],
              "cached": "scenes" in res.cached, "total": total, "truncated": trunc, "scenes": shown},
             artifacts=[artifact("scenes", ws.source_dir(src.id) / "scenes.json")],
             next=[f"videocli sheet {q(file)} --scenes"])

    @formatter("scenes")
    def _fmt_scenes(d: dict[str, Any]) -> None:
        import sys

        for s in d["scenes"]:
            sys.stdout.write(f"{s['id']} [{s['start']:.2f}-{s['end']:.2f}] {s['duration']:.2f}s\n")
        sys.stdout.write(f"{d['total']} shots\n")

    # ------------------------------------------------------------------ frames
    @command(app, "frames", summary="Still frames at given times (--at) or at an interval (--every)", writes=True, dry_run=False, exit_codes=(0, 1, 2, 4))
    def frames(
        file: str = typer.Argument(..., help="Media file or source id."),
        at: Optional[str] = typer.Option(None, "--at", metavar="T1,T2,...", help="Comma-separated times."),
        every: Optional[float] = typer.Option(None, "--every", metavar="S", help="One frame every S seconds."),
        frm: Optional[str] = typer.Option(None, "--from", metavar="T"),
        to: Optional[str] = typer.Option(None, "--to", metavar="T"),
        width: int = typer.Option(640, "--width", min=16, max=4096),
        fmt: str = typer.Option("jpg", "--format", help="jpg or png."),
        limit: Optional[int] = typer.Option(None, "--limit", min=1),
    ) -> None:
        """Extract frames (cached)."""
        from videocli.core.errors import UsageError
        from videocli.media import frames as fr
        from videocli.media import probe as probemod

        if (at is None) == (every is None):
            raise UsageError("BAD_ARGUMENT", "Give exactly one of --at or --every.")
        if fmt not in ("jpg", "png"):
            raise UsageError("BAD_ARGUMENT", "--format must be jpg or png.")
        ws = state.workspace()
        src = resolve_source(file)
        pr = probemod.load(ws, src)
        if pr.video is None:
            from videocli.core.errors import InputError

            raise InputError("NO_VIDEO", f"{file} has no video stream.")
        cache = ws.source_dir(src.id) / "frames"
        n = limit if limit is not None else int(state.settings.list_limit)
        if at is not None:
            times = _times(at)
            total = len(times)
            times = times[:n]
            paths = fr.extract_many(src.path, times, cache, width=width, fmt=fmt, jobs=int(state.settings.jobs))
            pairs = list(zip(times, paths, strict=False))
        else:
            if every is None or every <= 0:
                raise UsageError("BAD_ARGUMENT", "--every must be positive.")
            a, b = window_opts(frm, to)
            a = a or 0.0
            b = min(b if b is not None else pr.duration, pr.duration)
            total = max(0, int((b - a) / every + 0.999999))
            pairs = fr.extract_every(src.path, every, a, b, cache, width=width, fmt=fmt, limit=n)
        from videocli.core.fsutil import rel

        items = [{"time": t, "path": rel(p)} for t, p in pairs]
        emit({"source_id": src.id, "width": width, "total": total, "truncated": total > len(items), "frames": items},
             next=[f"videocli sheet {q(file)} --at {','.join(str(t) for t, _ in pairs[:6])}"] if pairs else [])

    # ------------------------------------------------------------------ sheet
    @command(app, "sheet", summary="Contact sheet: a grid of labelled stills (--every, --scenes, --at or --cuts EDL)", writes=True, dry_run=False, exit_codes=(0, 1, 2, 4))
    def sheet(
        file: Optional[str] = typer.Argument(None, help="Media file or source id (not needed with --cuts)."),
        every: Optional[float] = typer.Option(None, "--every", metavar="S"),
        scenes_: bool = typer.Option(False, "--scenes", help="The middle frame of each shot."),
        at: Optional[str] = typer.Option(None, "--at", metavar="T1,T2,..."),
        cuts: Optional[str] = typer.Option(None, "--cuts", metavar="NAME", help="Before and after frames of each join in an EDL."),
        cols: Optional[int] = typer.Option(None, "--cols", min=1, max=20),
        width: Optional[int] = typer.Option(None, "--width", min=320, max=4096, help="Sheet width in pixels (default 1600)."),
        frm: Optional[str] = typer.Option(None, "--from", metavar="T"),
        to: Optional[str] = typer.Option(None, "--to", metavar="T"),
        max_tiles: Optional[int] = typer.Option(None, "--max-tiles", min=1, max=400),
    ) -> None:
        """Make contact sheets for the agent to look at."""
        from videocli.core.errors import UsageError
        from videocli.core.fsutil import rel
        from videocli.media import frames as fr
        from videocli.media import probe as probemod
        from videocli.media.sheets import Tile, label_time, paginate

        modes = [m for m, on in (("every", every is not None), ("scenes", scenes_), ("at", at is not None), ("cuts", cuts is not None)) if on]
        if len(modes) != 1:
            raise UsageError("BAD_ARGUMENT", "Choose exactly one of --every, --scenes, --at or --cuts.")
        mode = modes[0]
        s = state.settings
        n_cols: int = cols or int(s.sheet__cols)
        sheet_w: int = width or int(s.sheet__width)
        n_max: int = max_tiles or int(s.sheet__max_tiles)
        tile_w = max(160, (sheet_w // n_cols) * 2)  # extract at ~2x tile size for crisp downscaling
        ws = state.workspace()
        a, b = window_opts(frm, to)
        nxt: list[str] = []
        if mode == "cuts":
            from videocli.edl import io

            edl = io.load(ws, cuts)  # type: ignore[arg-type]
            if n_cols % 2:
                n_cols += 1
            tiles = cut_tiles(edl, None, tile_w)
            if a is not None or b is not None:
                tiles = [t for t in tiles if (a is None or t.time >= a) and (b is None or t.time <= b)]
            stem = ws.dir("reports/sheets") / f"cuts-{cuts}"
            source_id = None
        else:
            if file is None:
                raise UsageError("BAD_ARGUMENT", "FILE is required unless --cuts is used.")
            src = resolve_source(file)
            source_id = src.id
            pr = probemod.load(ws, src)
            if pr.video is None:
                from videocli.core.errors import InputError

                raise InputError("NO_VIDEO", f"{file} has no video stream.")
            cache = ws.source_dir(src.id) / "frames"
            lo, hi = a or 0.0, min(b if b is not None else pr.duration, pr.duration)
            if mode == "every":
                if every is None or every <= 0:
                    raise UsageError("BAD_ARGUMENT", "--every must be positive.")
                pairs = fr.extract_every(src.path, every, lo, hi, cache, width=tile_w)
                tiles = [Tile(p, label_time(t), t) for t, p in pairs]
                stem_name = f"every{every:g}"
            elif mode == "at":
                times = _times(at)  # type: ignore[arg-type]
                paths = fr.extract_many(src.path, times, cache, width=tile_w, jobs=int(s.jobs))
                tiles = [Tile(p, label_time(t), t) for t, p in zip(times, paths, strict=False)]
                stem_name = "at-" + str(abs(hash(at)) % 10**8)
            else:
                doc = _ensure_scenes(src, file)
                shots = [x for x in doc["scenes"] if x["end"] > lo and x["start"] < hi]
                mids = [round((x["start"] + x["end"]) / 2, 3) for x in shots]
                paths = fr.extract_many(src.path, mids, cache, width=tile_w, jobs=int(s.jobs))
                tiles = [Tile(p, f"{x['id']} {label_time(m)}", m, {"shot": x["id"]}) for x, m, p in zip(shots, mids, paths, strict=False)]
                stem_name = "scenes"
            if a is not None or b is not None:
                stem_name += f"-{lo:g}-{hi:g}"
            stem = ws.source_dir(src.id) / "sheets" / stem_name
        if not tiles:
            raise UsageError("BAD_ARGUMENT", "Nothing to put on the sheet in that range.")
        sheets = paginate(tiles, stem, cols=n_cols, width=sheet_w, max_tiles=n_max)
        for sh in sheets:
            sh["path"] = rel(sh["path"])
        if len(sheets) > 1:
            nxt.append(f"Look at one sheet at a time; each holds up to {n_max} tiles.")
        emit({"mode": mode, "source_id": source_id, "tiles": len(tiles), "sheets": sheets},
             artifacts=[artifact("sheet", sh["path"]) for sh in sheets], next=nxt)

    @formatter("sheet")
    def _fmt_sheet(d: dict[str, Any]) -> None:
        c = console()
        for sh in d["sheets"]:
            c.print(f"{sh['path']}  {sh['width']}x{sh['height']}  {len(sh['tiles'])} tiles")

    # ------------------------------------------------------------------ strip
    @command(app, "strip", summary="Filmstrip of frames over the audio waveform for a short window (inspect one cut)", writes=True, dry_run=False, exit_codes=(0, 1, 2, 4))
    def strip(
        file: str = typer.Argument(..., help="Media file or source id."),
        frm: str = typer.Option(..., "--from", metavar="T"),
        to: str = typer.Option(..., "--to", metavar="T"),
        width: Optional[int] = typer.Option(None, "--width", min=320, max=4096),
        count: int = typer.Option(8, "--frames", min=2, max=24, help="Frames across the window."),
    ) -> None:
        """Strip for one decision point."""
        from videocli.core.errors import UsageError
        from videocli.core.fsutil import rel
        from videocli.media import frames as fr
        from videocli.media import probe as probemod
        from videocli.media.audio import decode_pcm
        from videocli.media.sheets import strip as make_strip

        a, b = window_opts(frm, to)
        assert a is not None and b is not None
        if b - a > 120:
            raise UsageError("BAD_ARGUMENT", "A strip covers at most 120 s; use sheet for longer spans.")
        ws = state.workspace()
        src = resolve_source(file)
        pr = probemod.load(ws, src)
        b = min(b, pr.duration)
        w = width or state.settings.sheet__width
        step = (b - a) / count
        times = [round(a + step * (i + 0.5), 3) for i in range(count)]
        frames = []
        if pr.video is not None:
            paths = fr.extract_many(src.path, times, ws.source_dir(src.id) / "frames", width=max(160, w // count * 2), jobs=int(state.settings.jobs))
            frames = list(zip(times, paths, strict=False))
        rate = 8000
        samples = decode_pcm(src.path, start=a, duration=b - a, rate=rate, channels=1) if pr.audio else []
        import numpy as np

        out = ws.source_dir(src.id) / "sheets" / f"strip-{a:.2f}-{b:.2f}.jpg"
        info = make_strip(frames, np.asarray(samples, dtype=np.float32), rate, a, b, out, width=w)
        info["path"] = rel(info["path"])
        emit({"source_id": src.id, **info}, artifacts=[artifact("strip", out)])

    # ------------------------------------------------------------------ verify
    @command(app, "verify", summary="Check a render against its EDL: duration, streams, sync, black, frozen, clicks, gaps, loudness, clipped words", exit_codes=(0, 1, 2, 4, 5))
    def verify_cmd(
        output: str = typer.Argument(..., metavar="OUT", help="Rendered file."),
        edl_name: str = typer.Option(..., "--edl", metavar="NAME", help="The EDL it was rendered from."),
        checks: Optional[str] = typer.Option(None, "--checks", metavar="LIST", help="Run only these checks."),
        skip: Optional[str] = typer.Option(None, "--skip", metavar="LIST", help="Skip these checks."),
        sheet_: bool = typer.Option(False, "--sheet", help="Also write a cut-review sheet for flagged joins."),
    ) -> None:
        """Verify a render."""
        from videocli.cli.commands.edit import _transcripts
        from videocli.core.errors import CheckFailed, file_not_found
        from videocli.core.fsutil import rel
        from videocli.edl import io
        from videocli.judge.verify import flagged_joins, verify

        out = Path(output)
        if not out.is_file():
            raise file_not_found(output)
        ws = state.workspace()
        edl = io.load(ws, edl_name)
        csv = lambda v: [x.strip() for x in (v or "").split(",") if x.strip()]  # noqa: E731
        report = verify(ws, out, edl, settings=state.settings, transcripts=_transcripts(edl), only=csv(checks) or None, skip=csv(skip))
        path = report.pop("_path")
        arts = [artifact("verify", path)]
        if sheet_:
            pairs = flagged_joins(edl, report)
            if pairs:
                from videocli.media.sheets import paginate

                s = state.settings
                tiles = cut_tiles(edl, pairs, max(160, s.sheet__width // 6 * 2))
                sheets = paginate(tiles, ws.dir("reports/sheets") / f"verify-{edl_name}", cols=6, width=s.sheet__width, max_tiles=s.sheet__max_tiles)
                report["sheets"] = [{**sh, "path": rel(sh["path"])} for sh in sheets]
                arts += [artifact("sheet", sh["path"]) for sh in sheets]
        if not report["passed"]:
            first = next((it.get("fix") for c in report["checks"] if c["status"] == "fail" for it in c["items"] if it.get("fix")), None)
            raise CheckFailed(
                "VERIFY_FAILED",
                f"Verify failed: {', '.join(report['failed'])}.",
                hint=(f"Run: {first}" if first else f"See {rel(path)}"),
                details={"report": rel(path), "failed": report["failed"], "warned": report["warned"],
                         "checks": [c for c in report["checks"] if c["status"] in ("fail", "warn")]},
            )
        emit(report, artifacts=arts, next=[f"videocli sheet --cuts {edl_name}"] if report["warned"] else [])

    @command(app, "compare", summary="Original and render side by side; the edited half freezes under a CUT badge where material was removed", writes=True, dry_run=False, exit_codes=(0, 1, 2, 4))
    def compare_cmd(
        output: str = typer.Argument(..., metavar="OUT", help="Rendered file."),
        edl_name: str = typer.Option(..., "--edl", metavar="NAME", help="The EDL it was rendered from."),
        out: Optional[str] = typer.Option(None, "-o", "--output", metavar="FILE.mp4", help="Default: <OUT>-compare.mp4"),
        drift: bool = typer.Option(False, "--drift", help="Don't sync: play both files from the start (any EDL)."),
    ) -> None:
        """Make a side-by-side comparison video for people to watch."""
        from videocli.core.errors import file_not_found
        from videocli.core.fsutil import rel
        from videocli.edl import io
        from videocli.judge.compare import compare

        rendered = Path(output)
        if not rendered.is_file():
            raise file_not_found(output)
        ws = state.workspace()
        edl = io.load(ws, edl_name)
        dest = Path(out) if out else rendered.with_name(rendered.stem + "-compare.mp4")
        res = compare(ws, rendered, edl, dest, drift=drift)
        res["output"] = rel(res["output"])
        emit(res, artifacts=[artifact("video", dest)], next=[f"videocli sheet --cuts {edl_name}"])

    @formatter("compare")
    def _fmt_compare(d: dict[str, Any]) -> None:
        c = console()
        if d["mode"] == "sync":
            c.print(f"compare {d['output']}  {d['duration']:.1f}s  original left, edited right, in sync")
            c.print(f"  {len(d['cuts'])} cut(s) marked, {d['removed_seconds']:.1f}s removed; sound is the original's")
        else:
            c.print(f"compare {d['output']}  {d['duration']:.1f}s  side by side (not synced); sound is the edit's")

    @formatter("verify")
    def _fmt_verify(d: dict[str, Any]) -> None:
        c = console()
        c.print(f"verify {d['output']} against {d['edl']}: {'[green]passed[/green]' if d['passed'] else '[red]failed[/red]'}")
        for ch in d["checks"]:
            color = {"pass": "green", "warn": "yellow", "fail": "red", "skip": "dim"}[ch["status"]]
            c.print(f"  [{color}]{ch['status']:4}[/{color}] {ch['name']:14} {ch['message']}")
            if ch["status"] in ("warn", "fail"):
                for it in ch.get("items", [])[:8]:
                    when = it.get("at", it.get("from"))
                    what = it.get("problem") or it.get("kind") or ""
                    detail = it.get("text") or ""
                    covers = it.get("covers")
                    if covers:
                        detail += f" → covers {str(covers[0])[:40]!r}"
                    head = f"{when:8.2f}s " if isinstance(when, (int, float)) else ""
                    c.print(f"           {head}{it.get('item') or it.get('clip') or ''} {what} {detail[:80]}", highlight=False, markup=False)
                    if it.get("fix"):
                        c.print(f"             fix: {it['fix']}", highlight=False, markup=False)
                more = len(ch.get("items", [])) - 8
                if more > 0:
                    c.print(f"           … {more} more in the report", highlight=False)
