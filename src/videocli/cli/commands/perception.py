"""Perception commands: analyze, index, transcript, silences, find (phase 1)."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Optional

import typer

from videocli.cli.output import artifact, console, emit, formatter
from videocli.cli.registry import command
from videocli.cli.state import state

MEDIA_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi", ".mxf", ".mts", ".m2ts", ".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".opus", ".wmv", ".flv"}


def q(s: str | Path) -> str:
    return shlex.quote(str(s))


def _csv(value: Optional[str]) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def resolve_source(file: str):
    from videocli.core.source import lookup

    return lookup(state.workspace(), file)


def load_required(src, name: str, file: str) -> dict[str, Any]:
    from videocli.analyze.base import load_artifact
    from videocli.core.errors import InputError

    try:
        doc = load_artifact(state.workspace(), src, name)
    except InputError as exc:
        exc.hint = f"Run: videocli analyze {q(file)}" + ("" if name in ("silence", "transcript", "loudness") else f" --only {name}")
        raise
    assert doc is not None
    return doc


def window_opts(frm: Optional[str], to: Optional[str]) -> tuple[float | None, float | None]:
    from videocli.core.errors import UsageError
    from videocli.core.timeutil import parse_time

    a = parse_time(frm) if frm is not None else None
    b = parse_time(to) if to is not None else None
    if a is not None and b is not None and b <= a:
        raise UsageError("BAD_ARGUMENT", "--to must be after --from.")
    return a, b


def limit_list(items: list[Any], limit: Optional[int]) -> tuple[list[Any], int, bool]:
    n = limit if limit is not None else int(state.settings.list_limit)
    return items[:n], len(items), len(items) > n


def _visual(src, file: str, query: str, a, b, *, required: bool) -> list[dict[str, Any]]:
    """Rank shots by how well their logged description matches the query (descriptions come from `log`)."""
    from videocli.analyze.base import load_artifact
    from videocli.core.errors import DependencyError
    from videocli.core.fsutil import rel
    from videocli.index.search import tokens

    ws = state.workspace()
    doc = load_artifact(ws, src, "shots", required=False)
    described = [s for s in (doc or {}).get("shots", []) if s.get("description")]
    if not described:
        if not required:
            return []
        raise DependencyError("MODEL_NOT_FOUND", "Visual search needs shot descriptions (or a local text-image model).",
                              hint=f"Run: videocli analyze {q(file)} --profile artistic  then: videocli log {q(file)}")
    qt = set(tokens(query))
    out = []
    for s in described:
        if (a is not None and s["end"] <= a) or (b is not None and s["start"] >= b):
            continue
        dt = set(tokens(s["description"]))
        stem = lambda w: w[:5]  # noqa: E731 - crude stemming: "working"~"works"
        overlap = len({stem(w) for w in qt} & {stem(w) for w in dt})
        if not overlap:
            continue
        score = overlap / max(1, len(qt))
        sheet = ws.source_dir(src.id) / "sheets" / f"shot-{s['id']}.jpg"
        out.append({"kind": "shot", "shot": s["id"], "start": s["start"], "end": s["end"], "segment": -1, "text": s["description"],
                    "score": round(score, 3), "sheet": rel(sheet) if sheet.is_file() else None})
    out.sort(key=lambda h: -h["score"])
    return out


def register(app: typer.Typer) -> None:
    # ------------------------------------------------------------------ analyze
    @command(app, "analyze", summary="Run analyzers (probe, audio, silence, loudness, transcript, scenes) and build the index", writes=True, dry_run=True, estimate=True, exit_codes=(0, 1, 2, 3, 4))
    def analyze(
        path: str = typer.Argument(..., help="Media file, registered source id, or folder."),
        only: Optional[str] = typer.Option(None, "--only", metavar="LIST", help="Run only these analyzers (comma-separated)."),
        skip: Optional[str] = typer.Option(None, "--skip", metavar="LIST", help="Skip these analyzers."),
        model: Optional[str] = typer.Option(None, "--model", help="Speech model (default: transcript.model setting)."),
        language: Optional[str] = typer.Option(None, "--language", metavar="CODE", help="Language code; default auto."),
        audio_stream: int = typer.Option(0, "--audio-stream", metavar="N", min=0, help="Audio stream index."),
        prompt: Optional[str] = typer.Option(None, "--prompt", help="Initial prompt for the speech model (names, jargon)."),
        profile: Optional[str] = typer.Option(None, "--profile", help="lecture (default set), artistic or full (adds shots, motion, faces, prosody, music, …)."),
    ) -> None:
        """Analyze a source: facts the agent can read without rendering."""
        from videocli.analyze import base
        from videocli.core.source import lookup
        from videocli.index import build as index_build

        ws = state.workspace()
        p = Path(path)
        files = sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in MEDIA_EXT) if p.is_dir() else [path]
        if p.is_dir() and not files:
            from videocli.core.errors import InputError

            raise InputError("FILE_NOT_FOUND", f"No media files found in {path}.")
        if profile and profile not in base.PROFILE_SETS:
            from videocli.core.errors import UsageError

            raise UsageError("BAD_ARGUMENT", f"--profile must be one of {', '.join(base.PROFILE_SETS)}.")
        names = base.selection(_csv(only), _csv(skip), default=base.PROFILE_SETS[profile or "lecture"])
        if profile == "full":
            from videocli.setup.profiles import check_group

            if not check_group("diarize", deep=False).ok:
                names = [n for n in names if n != "diarize"]
        explicit = set(_csv(only))
        options = {"model": model, "language": language, "audio_stream": audio_stream, "initial_prompt": prompt}

        if state.estimate or state.dry_run:
            from videocli.media import probe as probemod

            plan = []
            for f in files:
                src = lookup(ws, str(f))
                pr = probemod.load(ws, src)
                order = base.resolve(names)
                est = {"source_id": src.id, "path": str(src.path), "analyzers": order, "duration": pr.duration}
                # Rough CPU figures: transcription ~0.3x realtime with the small model on 4 cores.
                secs = 0.02 * pr.duration * ("silence" in order) + 0.02 * pr.duration * ("loudness" in order)
                secs += (0.3 if (model or state.settings.transcript__model) in ("small", "small.en") else 0.15) * pr.duration * ("transcript" in order)
                secs += 0.05 * pr.duration * ("scenes" in order)
                est["seconds"] = round(secs, 1)
                est["disk_bytes"] = int(32000 * pr.duration) if pr.audio else 0
                plan.append(est)
            emit({"estimate" if state.estimate else "plan": plan, "seconds": round(sum(e["seconds"] for e in plan), 1), "disk_bytes": sum(e["disk_bytes"] for e in plan)})
            return

        results = []
        artifacts = []
        warnings = []
        for f in files:
            src = lookup(ws, str(f))
            ctx = base.RunContext(ws=ws, settings=state.settings, force=state.force, options=options)
            res = base.run_analyzers(src, names, ctx, explicit=explicit)
            idx_path = ws.source_dir(src.id) / "index.json"
            if res.ran or not idx_path.exists():
                idx_path = index_build.build(ws, src)
            idx = index_build.load(ws, src) or {}
            entry = {
                "source_id": src.id,
                "path": str(src.path),
                "ran": res.ran,
                "cached": res.cached,
                "skipped": res.skipped,
                "seconds": res.seconds,
                "summary": idx.get("summary", {}),
            }
            results.append(entry)
            for name, ap in res.artifacts:
                artifacts.append(artifact(name, ap))
            artifacts.append(artifact("index", idx_path))
            warnings += res.warnings
        first = files[0]
        nxt = [f"videocli transcript {q(first)} --format compact --limit 40", f"videocli silences {q(first)} --min 1.0"]
        nxt.append(f"videocli plan {q(first)} --cut-silence min=1.2,pad=0.15 -o cut1")
        data: dict[str, Any] = results[0] if len(results) == 1 else {"sources": results, "total": len(results)}
        emit(data, artifacts=artifacts, warnings=warnings, next=nxt)

    @formatter("analyze")
    def _fmt_analyze(d: dict[str, Any]) -> None:
        c = console()
        for e in d.get("sources", [d]):
            c.print(f"[bold]{e['path']}[/bold] [dim]{e['source_id']}[/dim]")
            c.print(f"  ran: {', '.join(e['ran']) or '-'}   cached: {', '.join(e['cached']) or '-'}")
            for s in e["skipped"]:
                c.print(f"  skipped {s['name']}: {s['reason']}")
            sm = e.get("summary", {})
            bits = [f"{k}={v}" for k, v in sm.items() if v is not None and k in ("duration", "speech_seconds", "silence_seconds", "word_count", "language", "integrated_lufs", "scene_count")]
            if bits:
                c.print("  " + "  ".join(bits))

    # ------------------------------------------------------------------ index
    @command(app, "index", summary="Summary and artifact pointers for a source (read this first)", exit_codes=(0, 4))
    def index(file: str = typer.Argument(..., help="Media file or source id.")) -> None:
        """Print the merged index for a source."""
        from videocli.index import build as index_build

        src = resolve_source(file)
        ws = state.workspace()
        doc = index_build.load(ws, src)
        if doc is None:
            from videocli.core.errors import InputError

            raise InputError("ARTIFACT_MISSING", f"{file} has not been analyzed.", hint=f"Run: videocli analyze {q(file)}")
        nxt = []
        if "transcript" in doc["artifacts"]:
            nxt.append(f"videocli transcript {q(file)} --format compact --limit 40")
        else:
            nxt.append(f"videocli analyze {q(file)}")
        emit(doc, artifacts=[artifact("index", ws.source_dir(src.id) / "index.json")], next=nxt)

    # ------------------------------------------------------------------ transcript
    @command(app, "transcript", summary="Transcript text in a window: compact, segments, words or srt", exit_codes=(0, 2, 4))
    def transcript(
        file: str = typer.Argument(..., help="Media file or source id."),
        frm: Optional[str] = typer.Option(None, "--from", metavar="T", help="Window start (source time)."),
        to: Optional[str] = typer.Option(None, "--to", metavar="T", help="Window end (source time)."),
        fmt: str = typer.Option("compact", "--format", help="compact, segments, words or srt."),
        limit: Optional[int] = typer.Option(None, "--limit", min=1, help="Maximum items (segments or words)."),
    ) -> None:
        """Read the transcript, one window at a time."""
        from videocli.core.errors import UsageError
        from videocli.index import views

        if fmt not in ("compact", "segments", "words", "srt"):
            raise UsageError("BAD_ARGUMENT", f"--format must be compact, segments, words or srt (got {fmt!r}).")
        src = resolve_source(file)
        tr = load_required(src, "transcript", file)
        a, b = window_opts(frm, to)
        segs = views.select_segments(tr, a, b)
        data: dict[str, Any] = {"source_id": src.id, "format": fmt, "from": a, "to": b, "language": tr.get("language")}
        nxt: list[str] = []
        if fmt == "words":
            words = views.words_list(segs, a, b)
            shown, total, trunc = limit_list(words, limit)
            data.update({"total": total, "truncated": trunc, "words": shown})
            last = shown[-1]["end"] if shown else None
        else:
            shown, total, trunc = limit_list(segs, limit)
            data.update({"total": total, "truncated": trunc})
            if fmt == "compact":
                data["text"] = "\n".join(views.compact_lines(shown)) + ("\n" if shown else "")
            elif fmt == "segments":
                data["segments"] = [{k: s[k] for k in ("id", "start", "end", "text")} for s in shown]
            else:
                data["text"] = views.srt(views.segment_cues(shown))
            last = shown[-1]["end"] if shown else None
        if trunc and last is not None:
            to_part = f" --to {b}" if b is not None else ""
            nxt.append(f"videocli transcript {q(file)} --from {last}{to_part} --format {fmt}" + (f" --limit {limit}" if limit else ""))
        nxt.append(f"videocli find {q(file)} \"PHRASE\"")
        emit(data, next=nxt)

    @formatter("transcript")
    def _fmt_tr(d: dict[str, Any]) -> None:
        import sys

        if "text" in d:
            sys.stdout.write(d["text"])
        elif "segments" in d:
            for s in d["segments"]:
                sys.stdout.write(f"{s['id']:>5} [{s['start']:.2f}-{s['end']:.2f}] {s['text']}\n")
        else:
            for w in d["words"]:
                sys.stdout.write(f"[{w['start']:.2f}-{w['end']:.2f}] {w['word']}  p={w['p']}\n")
        if d.get("truncated"):
            sys.stdout.write(f"… {d['total']} total\n")

    # ------------------------------------------------------------------ silences
    @command(app, "silences", summary="Detected silences in a window, longest-first option", exit_codes=(0, 2, 4))
    def silences(
        file: str = typer.Argument(..., help="Media file or source id."),
        min_: Optional[float] = typer.Option(None, "--min", metavar="SECONDS", min=0, help="Only silences at least this long."),
        frm: Optional[str] = typer.Option(None, "--from", metavar="T"),
        to: Optional[str] = typer.Option(None, "--to", metavar="T"),
        limit: Optional[int] = typer.Option(None, "--limit", min=1),
        longest: bool = typer.Option(False, "--longest", help="Sort longest first."),
    ) -> None:
        """List silences (source time)."""
        from videocli.index.views import in_window

        src = resolve_source(file)
        doc = load_required(src, "silence", file)
        a, b = window_opts(frm, to)
        items = [s for s in doc.get("silences", []) if in_window(s["start"], s["end"], a, b) and (min_ is None or s["duration"] >= min_)]
        if longest:
            items.sort(key=lambda s: -s["duration"])
        shown, total, trunc = limit_list(items, limit)
        emit(
            {
                "source_id": src.id, "threshold_db": doc["params"].get("threshold_db"), "min_duration": doc["params"].get("min_duration"),
                "total": total, "truncated": trunc, "total_seconds": round(sum(s["duration"] for s in items), 3), "silences": shown,
            },
            next=[f"videocli plan {q(file)} --cut-silence min={min_ or 1.2},pad=0.15 -o cut1"],
        )

    @formatter("silences")
    def _fmt_sil(d: dict[str, Any]) -> None:
        import sys

        for s in d["silences"]:
            sys.stdout.write(f"[{s['start']:.2f}-{s['end']:.2f}] {s['duration']:.2f}s\n")
        sys.stdout.write(f"{d['total']} silences, {d['total_seconds']:.1f}s{' (truncated)' if d['truncated'] else ''}\n")

    # ------------------------------------------------------------------ find
    @command(app, "find", summary="Find a phrase (or --regex) in the transcript with word-accurate times", exit_codes=(0, 2, 4))
    def find(
        file: str = typer.Argument(..., help="Media file or source id."),
        query: str = typer.Argument(..., help="Phrase (case-insensitive) or regular expression with --regex."),
        regex: bool = typer.Option(False, "--regex", help="Treat QUERY as a regular expression over segment text."),
        context: int = typer.Option(0, "--context", metavar="N", min=0, help="Include N neighbouring segments."),
        frm: Optional[str] = typer.Option(None, "--from", metavar="T"),
        to: Optional[str] = typer.Option(None, "--to", metavar="T"),
        limit: Optional[int] = typer.Option(None, "--limit", min=1),
        mode: str = typer.Option("text", "--mode", help="text (transcript), visual (logged shot descriptions) or both."),
    ) -> None:
        """Search the transcript."""
        from videocli.core.errors import UsageError, feature_not_available
        from videocli.index import search

        if mode not in ("text", "visual", "both"):
            raise UsageError("BAD_ARGUMENT", f"--mode must be text, visual or both (got {mode!r}).")
        src = resolve_source(file)
        a, b = window_opts(frm, to)
        hits: list[dict[str, Any]] = []
        if mode in ("text", "both"):
            from videocli.analyze.base import load_artifact

            tr = load_artifact(state.workspace(), src, "transcript", required=mode == "text")
            if tr:
                hits += [{**h, "kind": "speech"} for h in (search.find_regex if regex else search.find_phrase)(tr, query, context=context, frm=a, to=b)]
        if mode in ("visual", "both"):
            hits += _visual(src, file, query, a, b, required=mode == "visual")
        if mode == "both":
            hits.sort(key=lambda h: -h.get("score", 1.0))
        shown, total, trunc = limit_list(hits, limit)
        nxt = []
        if shown:
            h = shown[0]
            nxt.append(f"videocli transcript {q(file)} --from {max(0.0, h['start'] - 10):.2f} --to {h['end'] + 10:.2f}")
        _ = feature_not_available
        emit({"query": query, "regex": regex, "mode": mode, "total": total, "truncated": trunc, "matches": shown}, next=nxt)

    @formatter("find")
    def _fmt_find(d: dict[str, Any]) -> None:
        import sys

        for m in d["matches"]:
            sys.stdout.write(f"[{m['start']:.2f}-{m['end']:.2f}] seg {m['segment']}: {m['text']}\n")
        sys.stdout.write(f"{d['total']} matches\n")
