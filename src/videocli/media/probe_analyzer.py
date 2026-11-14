"""``probe`` as an analyzer, so ``analyze`` lists it under ran or cached."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from videocli.analyze.base import Availability, RunContext, register
from videocli.core.source import Source
from videocli.media import probe as probemod


class ProbeAnalyzer:
    name = "probe"
    version = probemod.VERSION
    requires: tuple[str, ...] = ()
    schema = probemod.SCHEMA
    needs_audio = False
    needs_video = False

    def params(self, ctx: RunContext) -> dict[str, Any]:
        return {}

    def available(self, ctx: RunContext) -> Availability:
        return Availability.yes()

    def run(self, src: Source, params: dict[str, Any], ctx: RunContext) -> Path:
        result, path, _ = probemod.probe(ctx.ws, src, force=True)
        ctx.probe = result
        return path


register(ProbeAnalyzer())
