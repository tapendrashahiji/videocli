"""Build the ``videocli.verify/v1`` report: run checks, map items to clips, attach fixes."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from videocli import __version__
from videocli.core import progress
from videocli.core.errors import UsageError
from videocli.core.fsutil import write_json
from videocli.core.timeutil import ms
from videocli.core.workspace import Workspace, now_iso
from videocli.edl.model import EDL
from videocli.judge import checks as ck

SCHEMA = "videocli.verify/v1"


def _fix(name: str, edl: EDL, item: dict[str, Any], output: Path) -> str | None:
    n = edl.name
    if item.get("fix"):
        return item["fix"]
    clip = item.get("clip")
    if name in ("black_frames", "frozen_frames") and clip and "source_from" in item:
        return f"videocli edl cut {n} --from {item['source_from']} --to {item['source_to']}"
    if name == "silence_gaps" and "source_from" in item:
        return f"videocli edl cut {n} --from {ms(item['source_from'] + 0.15)} --to {ms(item['source_to'] - 0.15)}"
    if name in ("duration", "av_sync", "audio_clicks"):
        return f"videocli render {n} -o {output} --force"
    if name == "loudness":
        return f"videocli render {n} -o {output} --force"
    return None


def _map_item(edl: EDL, item: dict[str, Any]) -> dict[str, Any]:
    tm = edl.timemap()
    at = item.get("at")
    if at is None:
        return item
    hit = tm.to_source(min(at, max(0.0, tm.duration - 1e-6)))
    if hit:
        sp, t = hit
        item.setdefault("clip", sp.id)
        item["source"] = sp.source
        item["source_time"] = t
        if "from" in item and "to" in item:
            a = tm.to_source(item["from"])
            b = tm.to_source(max(item["from"], item["to"] - 1e-6))
            if a and b and a[0].id == b[0].id:
                item["source_from"], item["source_to"] = a[1], b[1]
    return item


def verify(ws: Workspace, output: Path, edl: EDL, *, settings: Any, transcripts: dict[str, Any],
           only: list[str] | None = None, skip: list[str] | None = None) -> dict[str, Any]:
    from videocli.media.probe import probe_path

    for n in [*(only or []), *(skip or [])]:
        if n not in ck.CHECKS:
            raise UsageError("BAD_ARGUMENT", f"Unknown check {n!r}.", hint=f"Checks: {', '.join(ck.CHECKS)}")
    names = [n for n in (only or list(ck.CHECKS)) if n not in (skip or [])]
    pr = probe_path(output)
    ctx = ck.Context(output=output, edl=edl, probe=pr, settings=settings, transcripts=transcripts, ws=ws)
    if any(n in ck.SCAN_NEEDED for n in names):
        with progress.step_ctx("scan"):
            ctx.scan = ck.scan_output(ctx)
    results = []
    for n in names:
        with progress.step_ctx(n):
            r = ck.CHECKS[n](ctx)
        for it in r.items:
            _map_item(edl, it)
            fx = _fix(n, edl, it, output)
            if fx:
                it["fix"] = fx
        results.append(r.as_dict())
    failed = [r["name"] for r in results if r["status"] == "fail"]
    warned = [r["name"] for r in results if r["status"] == "warn"]
    report = {
        "schema": SCHEMA,
        "created": now_iso(),
        "tool_version": __version__,
        "output": str(output),
        "edl": edl.name,
        "edl_revision": len(edl.history),
        "passed": not failed,
        "failed": failed,
        "warned": warned,
        "duration": ms(pr.duration),
        "checks": results,
    }
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = ws.dir("reports") / f"verify-{edl.name}-{stamp}.json"
    write_json(path, report)
    report["_path"] = path
    return report


def flagged_joins(edl: EDL, report: dict[str, Any], window: float = 0.6) -> list[tuple[str, str]]:
    """Joins near any failing or warning item, as (clip before, clip after)."""
    tm = edl.timemap()
    joins = [(sp.out_end, sp.id, nxt.id) for sp, nxt in zip(tm.spans, tm.spans[1:], strict=False)]
    hits: list[tuple[str, str]] = []
    for r in report["checks"]:
        if r["status"] not in ("fail", "warn"):
            continue
        for it in r["items"]:
            if "clips" in it and len(it["clips"]) == 2:
                hits.append((it["clips"][0], it["clips"][1]))
                continue
            at = it.get("at")
            if at is None:
                continue
            for t, a, b in joins:
                if abs(t - at) <= window:
                    hits.append((a, b))
    return list(dict.fromkeys(hits))
