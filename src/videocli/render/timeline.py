"""Compile an EDL into render nodes (SPEC §10.5, phase 6).

The output timeline is split into chunks wherever the set of active layers or transitions
changes. A chunk holding only part of one spine clip is a ``segment`` (the phase-2 fast path,
with identical parameters for whole clips). A chunk with a transition or overlays is one
``composite`` with its own small filter graph. Picture chunks join by stream copy. Sound is
built separately: dialogue lanes (A/B), overlay sound, music and effects, room tone, then one
mix node. Stabilised clips read from a ``bake`` node instead of the source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from videocli.core.timeutil import Rate, ms
from videocli.edl.model import EDL, Clip
from videocli.render import presets
from videocli.render.graph import Node, topo
from videocli.render.nodes import media as nodes

SR = nodes.SAMPLE_RATE


@dataclass
class Unit:
    """A constant-speed run of frames inside a clip (a ramp piece, the clip itself, or a freeze)."""

    f0: int  # first frame, local to the clip
    frames: int
    src_start: float
    speed: float
    freeze: bool = False


@dataclass
class ClipPlan:
    clip: Clip
    units: list[Unit]
    frames: int
    start: int = 0  # global frame of the clip's first frame
    d_prev: int = 0
    d_next: int = 0
    lead: int = 0  # samples
    lag: int = 0


@dataclass
class Compiled:
    edl: EDL
    preset: presets.Preset
    size: tuple[int, int]
    rate: Rate
    chunks: list[Node]
    lanes: list[Node]
    picture: Node
    mix: Node
    frames: int
    samples: int
    bakes: list[Node] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    clip_frames: list[tuple[str, int, int]] = field(default_factory=list)
    pix_fmt: str = "yuv420p"

    @property
    def nodes(self) -> list[Node]:
        return topo([self.picture, self.mix])

    @property
    def duration(self) -> float:
        return self.frames / self.rate.fps

    # phase-2 compatibility
    @property
    def segments(self) -> list[Node]:
        return [n for n in self.chunks if n.kind == "segment"]


def _units(c: Clip, fps: Fraction) -> list[Unit]:
    out: list[Unit] = []
    f = 0
    for a, b, sp in c.pieces():
        n = max(1, round((b - a) / sp * float(fps)))
        out.append(Unit(f, n, a, sp))
        f += n
    if c.freeze:
        n = max(1, round(c.freeze * float(fps)))
        last = out[-1]
        hold_t = max(last.src_start, c.end - last.speed / float(fps))
        out.append(Unit(f, n, hold_t, 1.0, freeze=True))
    return out


def plan_clips(edl: EDL, fps: Fraction) -> tuple[list[ClipPlan], int]:
    plans = [ClipPlan(c, u := _units(c, fps), sum(x.frames for x in u)) for c in edl.tracks.main]
    trans = {t.after: t for t in edl.transitions if t.type != "cut"}
    for i, p in enumerate(plans):
        t = trans.get(p.clip.id)
        if t is not None and i + 1 < len(plans):
            d = round(t.duration * float(fps))
            d = max(1, min(d, p.frames - 1, plans[i + 1].frames - 1))
            p.d_next = d
            plans[i + 1].d_prev = d
    g = 0
    for p in plans:
        p.start = g
        g += p.frames - p.d_next
    total = plans[-1].start + plans[-1].frames if plans else 0
    return plans, total


def _src_at(p: ClipPlan, local: int, fps: Fraction) -> tuple[Unit, float]:
    for u in p.units:
        if u.f0 <= local < u.f0 + u.frames:
            if u.freeze:
                return u, u.src_start
            return u, ms(u.src_start + (local - u.f0) / float(fps) * u.speed)
    u = p.units[-1]
    return u, u.src_start


def _window(edl: EDL, plans: dict[str, ClipPlan], item: Any, frames: float, fps: Fraction, total: int) -> tuple[int, int] | None:
    p = plans.get(item.anchor)
    if p is None:
        return None
    f0 = p.start + round(item.offset * float(fps))
    f1 = f0 + max(1, round(frames))
    f0, f1 = max(0, f0), min(total, f1)
    return (f0, f1) if f1 > f0 else None


def _media(edl: EDL, clip: Clip, bakes: dict[str, Node]) -> tuple[dict[str, Any], list[Node], dict[str, Any]]:
    """Parameters, inputs and extra for reading a clip's picture (source or its stabilise bake)."""
    if clip.id in bakes:
        return {"baked": clip.stabilize, "offset": clip.start}, [bakes[clip.id]], {}
    return {}, [], {"path": edl.sources[clip.source].path}


def crop_spec(crop: Any, cx: float | None) -> list:
    """Crop parameters for a picture input: [aspect, x] or, stacked, [aspect, x, {face, split}]."""
    if crop.mode == "stack" and crop.face:
        return [crop.aspect, cx, {"face": [round(v, 4) for v in crop.face], "split": round(crop.split, 4)}]
    return [crop.aspect, cx]


def compile_timeline(
    edl: EDL, preset: presets.Preset, *, crf: int, x264_preset: str, normalize: bool = True,
    labels: dict[str, str] | None = None, speech: list[tuple[float, float]] | None = None,
    room_tone: dict[str, Any] | None = None, stems: bool = False, grades_dir: Any = None, ws: Any = None,
) -> Compiled:
    rate = Rate.parse(edl.rate)
    fps = rate.value
    size = presets.dims(preset, edl.resolution)
    warnings: list[dict[str, str]] = []
    plans, total = plan_clips(edl, fps)
    by_id = {p.clip.id: p for p in plans}
    from videocli.color import render as cr

    graded = cr.timeline_graded(edl)
    pix = "yuv420p10le" if graded else "yuv420p"

    def grade(source: str, key: str | None, uid: str) -> tuple[list[str], list[str], dict[str, Any]]:
        """Filters for one picture input; labels inside window graphs are unique per input."""
        if not graded:
            return [], [], {}
        return cr.filters(edl, source, key, grades_dir, "g" + uid)

    crop = None
    if edl.reframe is not None:
        crop = edl.reframe
        if crop.mode == "subject" and not crop.crops:
            warnings.append({"code": "REFRAME_CENTER_FALLBACK", "message": "No face positions (phase 5); reframing uses a centre crop."})

    # ---------------------------------------------------------------- bakes (stabilise)
    from videocli.render.bakers import stabilize_node

    bakes: dict[str, Node] = {}
    for p in plans:
        c = p.clip
        if c.stabilize:
            bakes[c.id] = stabilize_node(edl.sources[c.source].path, c.source, c.start, c.end, c.stabilize, str(rate))

    # ---------------------------------------------------------------- picture chunks
    edges = {0, total}
    for p in plans:
        edges |= {p.start, p.start + p.d_prev, p.start + p.frames - p.d_next, p.start + p.frames}
        for u in p.units:
            edges.add(p.start + u.f0)
    layers: list[tuple[int, int, Any]] = []
    for ov in edl.tracks.overlay:
        w = _window(edl, by_id, ov, (ov.end - ov.start) * float(fps), fps, total)
        if w is None:
            warnings.append({"code": "OVERLAY_SKIPPED", "message": f"Overlay {ov.id} is anchored to a missing clip or lies outside the timeline."})
            continue
        layers.append((w[0], w[1], ov))
        edges |= {w[0], w[1]}
    gfx_layers: list[tuple[int, int, Any, Node]] = []
    if edl.tracks.graphics:
        from videocli.graphics.templates import get as get_template
        from videocli.graphics.templates import load_brand, resolve_params
        from videocli.render.bakers.graphics import graphic_node

        brand = load_brand(ws)
        for g in sorted(edl.tracks.graphics, key=lambda x: x.layer):
            nfr = max(1, round(g.duration * float(fps)))
            wnd = _window(edl, by_id, g, nfr, fps, total)
            if wnd is None:
                warnings.append({"code": "GRAPHIC_SKIPPED", "message": f"Graphic {g.id} is anchored to a missing clip or lies outside the timeline."})
                continue
            t = get_template(ws, g.template)
            params = resolve_params(t, g.params, brand)
            node = graphic_node(t, params, brand, size, str(rate), wnd[1] - wnd[0] if wnd[1] - wnd[0] < nfr else nfr, g.id,
                                command=edl.commands.get(t.entry or "") if t.baker == "generic" else None)
            gfx_layers.append((wnd[0], wnd[1], g, node))
            edges |= {wnd[0], wnd[1]}
    edges = sorted(e for e in edges if 0 <= e <= total)

    def base_at(f: int) -> tuple[str, Any]:
        for i, p in enumerate(plans):
            t0 = p.start + p.frames - p.d_next
            if p.d_next and t0 <= f < p.start + p.frames:
                return "trans", i
            if p.start + p.d_prev <= f < t0:
                return "clip", i
        return "clip", len(plans) - 1

    def crop_x(clip: Clip) -> float | None:
        if crop is None:
            return None
        return round(crop.crops.get(clip.id, 0.5), 4)

    chunks: list[Node] = []
    w, h = size
    for f0, f1 in zip(edges, edges[1:], strict=False):
        if f1 <= f0:
            continue
        n = f1 - f0
        kind, i = base_at(f0)
        active = [ov for a, b, ov in layers if a <= f0 and f1 <= b] + [g for a, b, g, _n in gfx_layers if a <= f0 and f1 <= b]
        p = plans[i]
        if kind == "clip" and not active:
            u, t = _src_at(p, f0 - p.start, fps)
            src = edl.sources[p.clip.source]
            mp, mins, extra = _media(edl, p.clip, bakes)
            params = {
                "source": p.clip.source, "start": t, "speed": u.speed, "rate": str(rate), "size": [w, h], "frames": n,
                "pix_fmt": pix, "crf": crf, "x264_preset": x264_preset, "has_video": src.has_video,
            }
            pre, post, gk = grade(p.clip.source, p.clip.grade, f"{len(chunks)}")
            if gk:
                params["grade"] = gk
                extra = {**extra, "pre": pre, "post": post}
            if u.freeze:
                params["hold"] = True
            cx = crop_x(p.clip)
            if cx is not None:
                params["crop"] = crop_spec(crop, cx)
            params.update(mp)
            if labels and p.clip.id in labels:
                params["label"] = labels[p.clip.id]
            chunks.append(Node("segment", params, inputs=mins, label=f"{p.clip.id}@{f0}", runner=nodes.run_segment, extra=extra))
            continue
        # composite chunk: a base (one clip or a transition pair) plus layers
        bases = []
        ins: list[Node] = []
        extra: dict[str, Any] = {"paths": []}

        def add_base(pp: ClipPlan, local: int) -> None:
            u, t = _src_at(pp, local, fps)
            src = edl.sources[pp.clip.source]
            mp, mins, ex = _media(edl, pp.clip, bakes)
            b = {"source": pp.clip.source, "start": t, "speed": u.speed, "has_video": src.has_video, "hold": u.freeze,
                 "crop": (crop_spec(crop, crop_x(pp.clip)) if crop is not None else None), **mp}
            pre, post, gk = grade(pp.clip.source, pp.clip.grade, f"{len(chunks)}b{len(bases)}")
            if gk:
                b["grade"] = gk
                extra.setdefault("grades", {})[str(len(bases))] = {"pre": pre, "post": post}
            if mins:
                b["input"] = len(ins)
                ins.extend(mins)
            else:
                b["path_index"] = len(extra["paths"])
                extra["paths"].append(ex["path"])
            bases.append(b)

        trans = None
        if kind == "trans":
            add_base(p, f0 - p.start)
            add_base(plans[i + 1], f0 - plans[i + 1].start)
            t = next(t for t in edl.transitions if t.after == p.clip.id)
            trans = {"type": t.type, "frames": p.d_next, "offset": f0 - (p.start + p.frames - p.d_next), "color": t.color}
        else:
            add_base(p, f0 - p.start)
        lays = []
        for a, _b, ov in [(a, b, ov) for a, b, ov in layers if a <= f0 and f1 <= b]:
            t = ms(ov.start + (f0 - a) / float(fps))
            lay = {"source": ov.source, "start": t, "mode": ov.mode, "pos": ov.pos, "scale": ov.scale,
                   "opacity": ov.opacity, "id": ov.id, "path_index": len(extra["paths"])}
            _pre, post, gk = grade(ov.source, ov.grade, f"{len(chunks)}l{len(lays)}")
            if gk:
                lay["grade"] = gk
                extra.setdefault("layer_grades", {})[str(len(lays))] = post
            lays.append(lay)
            extra["paths"].append(edl.sources[ov.source].path)
        for a, _b, g, gnode in [x for x in gfx_layers if x[0] <= f0 and f1 <= x[1]]:
            lays.append({"kind": "bake", "id": g.id, "input": len(ins), "start": round((f0 - a) / float(fps), 6)})
            ins.append(gnode)
        params = {"rate": str(rate), "size": [w, h], "frames": n, "pix_fmt": pix, "crf": crf, "x264_preset": x264_preset,
                  "bases": bases, "layers": lays, "transition": trans}
        from videocli.render.nodes.composite import run_composite

        chunks.append(Node("composite", params, inputs=ins, label=f"{p.clip.id}@{f0}", runner=run_composite, extra=extra))

    picture = Node("picture", {"rate": str(rate), "count": len(chunks)}, inputs=chunks, label="picture", runner=nodes.run_picture)

    # ---------------------------------------------------------------- sound
    total_samples = round(total * SR / fps)
    fade = edl.mix.fade_ms
    lanes: list[Node] = []
    placements: list[tuple[int, float]] = []
    for i, p in enumerate(plans):
        c = p.clip
        src = edl.sources[c.source]
        s_clip0 = round(p.start * SR / fps)
        if c.ramp or not src.has_audio:
            # Ramps take no natural sound (mix rule: mute); sources without audio give silence.
            s1 = round((p.start + p.frames) * SR / fps)
            lanes.append(Node("lane", {"source": c.source, "start": c.start, "end": c.end, "speed": 1.0, "stream": c.audio.stream,
                                       "gain_db": 0.0, "samples": s1 - s_clip0, "fade_ms": fade, "has_audio": False, "lane": i % 2},
                              label=c.id, runner=nodes.run_lane, extra={"path": src.path}))
            placements.append((s_clip0, 1.0))
            continue
        lead = c.audio.lead
        lag = c.audio.lag
        prev_lag = plans[i - 1].clip.audio.lag if i > 0 else 0.0
        next_lead = plans[i + 1].clip.audio.lead if i + 1 < len(plans) else 0.0
        lead = min(lead, c.start / c.speed)
        dur_src = edl.sources[c.source].duration or c.end
        lag = min(lag, max(0.0, (dur_src - c.end) / c.speed))
        body_frames = sum(u.frames for u in p.units if not u.freeze)
        s0 = s_clip0 - round(lead * SR)
        s_body_end = round((p.start + body_frames) * SR / fps)
        s1 = s_body_end + round(lag * SR)
        s0 = max(0, s0)
        fade_in = max(fade / 1000.0, p.d_prev / float(fps), lead, prev_lag)
        fade_out = max(fade / 1000.0, p.d_next / float(fps), lag, next_lead)
        params = {
            "source": c.source, "start": ms(c.start - lead * c.speed), "end": ms(c.end + lag * c.speed), "speed": c.speed,
            "stream": c.audio.stream, "gain_db": c.audio.gain_db, "samples": s1 - s0, "fade_ms": fade, "has_audio": True, "lane": i % 2,
        }
        if fade_in > fade / 1000.0 + 1e-9 or fade_out > fade / 1000.0 + 1e-9:
            params["fade_in"] = round(fade_in, 4)
            params["fade_out"] = round(fade_out, 4)
        lanes.append(Node("lane", params, label=c.id, runner=nodes.run_lane, extra={"path": src.path}))
        placements.append((s0, 1.0))

    others: list[dict[str, Any]] = []
    other_nodes: list[Node] = []
    for a, b, ov in layers:
        if ov.mute or not edl.sources[ov.source].has_audio:
            continue
        s0 = round(a * SR / fps)
        n = round(b * SR / fps) - s0
        other_nodes.append(Node("lane", {"source": ov.source, "start": ov.start, "end": ov.start + n / SR, "speed": 1.0, "stream": 0,
                                         "gain_db": 0.0, "samples": n, "fade_ms": max(fade, 30), "has_audio": True, "lane": 2},
                                label=ov.id, runner=nodes.run_lane, extra={"path": edl.sources[ov.source].path}))
        others.append({"offset": s0, "gain_db": 0.0, "role": "dialogue", "id": ov.id})
    for item in edl.tracks.audio:
        src = edl.sources[item.source]
        if item.at is not None:
            start_t = item.at
        else:
            p = by_id.get(item.anchor or "")
            if p is None:
                warnings.append({"code": "AUDIO_SKIPPED", "message": f"Audio item {item.id} is anchored to a missing clip."})
                continue
            start_t = p.start / float(fps) + item.offset
        s0 = round(start_t * SR)
        if s0 >= total_samples:
            warnings.append({"code": "AUDIO_SKIPPED", "message": f"Audio item {item.id} starts after the end of the edit."})
            continue
        natural = ((item.end if item.end is not None else src.duration) - item.start)
        want = (total_samples - s0) / SR if item.fit else natural
        n = max(1, min(round(want * SR), total_samples - s0))
        loop = item.fit and want > natural + 1e-3
        other_nodes.append(Node("music", {"source": item.source, "start": item.start, "end": item.end, "samples": n, "loop": loop,
                                          "fade": item.fade}, label=item.id, runner=nodes.run_music, extra={"path": src.path}))
        others.append({"offset": s0, "gain_db": item.gain_db, "duck_db": item.duck_db, "role": item.role, "id": item.id})
    if room_tone:
        other_nodes.append(Node("music", {"source": room_tone["source"], "start": room_tone["start"], "end": room_tone["end"],
                                          "samples": total_samples, "loop": True, "fade": 0.5, "pingpong": True},
                                label="room-tone", runner=nodes.run_music, extra={"path": room_tone["path"]}))
        others.append({"offset": 0, "gain_db": room_tone.get("gain_db", 0.0), "role": "tone", "id": "room-tone"})

    mix_params: dict[str, Any] = {"samples": total_samples, "placements": placements, "normalize": normalize and edl.mix.normalize,
                                  "target_lufs": edl.mix.target_lufs, "true_peak_db": edl.mix.true_peak_db}
    if others:
        mix_params["others"] = others
        if speech is not None:
            mix_params["speech"] = [[round(a * SR), round(b * SR)] for a, b in speech]
    snd = edl.sound
    cleanup = {k: v for k, v in {"denoise": snd.denoise, "level": snd.level, "compress": snd.compress, "highpass": snd.highpass}.items() if v}
    if cleanup:
        mix_params["cleanup"] = cleanup
    if stems:
        mix_params["stems"] = True
    mix = Node("mix", mix_params, inputs=lanes + other_nodes, label="mix", runner=nodes.run_mix)
    comp = Compiled(edl, preset, size, rate, chunks, lanes, picture, mix, total, total_samples, list(bakes.values()), warnings)
    comp.pix_fmt = pix
    comp.clip_frames = [(p.clip.id, p.start, p.frames) for p in plans]
    return comp
