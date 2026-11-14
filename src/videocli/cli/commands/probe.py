"""``probe FILE`` (phase 0)."""

from __future__ import annotations

from typing import Any

import typer

from videocli.cli.output import artifact, console, emit, formatter
from videocli.cli.registry import command
from videocli.cli.state import state


def register(app: typer.Typer) -> None:
    @command(app, "probe", summary="Read container, streams, duration and colour metadata of a media file", exit_codes=(0, 1, 3, 4))
    def probe(file: str = typer.Argument(..., help="Media file (or a registered source id).")) -> None:
        """Probe a media file and register it as a source."""
        from videocli.core.source import lookup
        from videocli.core.source import register as reg
        from videocli.media import probe as probemod

        ws = state.workspace()
        warnings = []
        if len(file) == 12 and all(c in "0123456789abcdef" for c in file):
            src = lookup(ws, file)
        else:
            r = reg(ws, file)
            src = r.source
            if r.changed_from:
                warnings.append({"code": "SOURCE_CHANGED", "message": f"{file} changed since it was registered (was {r.changed_from}, now {src.id}); analysis is redone for the new content."})
        result, path, cached = probemod.probe(ws, src, force=state.force)
        data = result.model_dump(by_alias=True)
        data["cached"] = cached
        warnings += [w.model_dump() for w in result.warnings]
        nxt = [f"videocli analyze {_q(file)}"] if result.audio else [f"videocli sheet {_q(file)} --every 30"]
        emit(data, artifacts=[artifact("probe", path)], warnings=warnings, next=nxt)

    @formatter("probe")
    def _fmt(d: dict[str, Any]) -> None:
        c = console()
        c.print(f"[bold]{d['path']}[/bold]  [dim]{d['source_id']}[/dim]")
        c.print(f"  {d['container']}  {d['duration']:.3f}s  {d['size'] / 1e6:.1f} MB")
        v = d.get("video")
        if v:
            col = v["color"]
            tag = col.get("hdr") or "sdr"
            c.print(f"  video  {v['codec']} {v['display_width']}x{v['display_height']} @ {v['fps']}{' VFR' if v['vfr'] else ''}  {v['pix_fmt']} {v['bit_depth']}-bit {tag}{' rot ' + str(v['rotation']) if v['rotation'] else ''}")
        for a in d.get("audio", []):
            c.print(f"  audio  #{a['index']} {a['codec']} {a['sample_rate']} Hz {a['channels']} ch")
        for s in d.get("subtitles", []):
            c.print(f"  subs   #{s['index']} {s['codec']} {s.get('language') or ''}")


def _q(s: str) -> str:
    import shlex

    return shlex.quote(s)
