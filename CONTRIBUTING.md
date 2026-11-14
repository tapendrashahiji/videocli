# Contributing to videocli

Thanks for helping. videocli is a single Python package; the design lives in
[docs/SPEC.md](docs/SPEC.md) and the build plan in [docs/phases/](docs/phases/).

## Development setup

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest              # fast tests (needs ffmpeg ≥ 5.0 on PATH)
pytest -m slow      # tests that load real models
ruff check src tests && pyright
```

## Rules that keep the agent contract stable

- **Layering.** `cli/` parses, calls domain code, and formats. Nothing imports `cli`.
- **One ffmpeg door.** Every ffmpeg/ffprobe call goes through `videocli.media.ffmpeg.run`.
  List arguments only; wrap local paths with `fpath()`.
- **Errors.** Raise a `VideoCliError` subclass with a registered code
  (`videocli.core.errors.ERROR_CODES`) and a `hint` that names the fix. Never print from
  domain code; `cli.app.main` is the only place that catches exceptions.
- **Lazy imports.** Command modules import only typer and light helpers. Heavy libraries
  load inside the function that needs them. `videocli --help` must start in under 300 ms.
- **Facts, intent, decisions.** Analyzers write facts, planners write the EDL, render only
  reads the EDL.
- **Content-addressed.** Artifacts and render nodes are keyed by their inputs; never
  redo unchanged work.
- **Tests.** Generate media with ffmpeg `lavfi` in tests; don't commit binary fixtures.

## Adding things

- **An analyzer:** add a module under `analyze/` implementing the `Analyzer` protocol, register
  it in `analyze/base.py`, and add a parser test with captured ffmpeg output.
- **A planner rule:** add it under `edl/planners/`, keep it pure (ranges in, ranges out), and
  add it to the fixed rule order in `edl/planners/__init__.py`.
- **A verify check:** add one function in `judge/checks.py` returning a status and items, with
  one fixture that passes and one built to fail.

## Commits and pull requests

Small, focused commits with a message that says why. CI must be green on Linux, macOS and
Windows.
