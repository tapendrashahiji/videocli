# videocli

A command-line video editor built for AI agents.

videocli turns footage into text and numbers an agent can read, takes every creative decision
as a file (an EDL), renders with ffmpeg, and checks its own output. Every command is
non-interactive, prints one JSON document on stdout, and returns a stable exit code.

> **Status:** pre-release, under construction. See [docs/SPEC.md](docs/SPEC.md) for the design and
> [docs/phases/](docs/phases/) for the build plan. The first release (v0.1) targets lectures,
> tutorials and talking heads.

## Install

```bash
curl -LsSf https://<project-domain>/install.sh | sh                      # asks which profile
curl -LsSf https://<project-domain>/install.sh | sh -s -- --profile lecture
```

Until the package is on PyPI, install from a checkout:

```bash
uv tool install git+https://github.com/tapendrashahiji/videocli
videocli setup
```

videocli needs ffmpeg 5.0 or newer. It uses the system copy when there is one.

## A first look

```bash
videocli doctor                 # what is installed, and the commands that fix the rest
videocli probe lecture.mp4      # streams, duration, colour metadata
```

Output is JSON when piped (or with `--json`):

```json
{"ok": true, "command": "probe", "version": "0.1.0", "data": {"duration": 3612.48, "...": "..."},
 "artifacts": [{"kind": "probe", "path": ".videocli/sources/a1b2c3d4e5f6/probe.json"}],
 "warnings": [], "next": ["videocli analyze lecture.mp4"]}
```

| Exit code | Meaning |
|---|---|
| 0 | Success |
| 1 | Runtime failure (`FFMPEG_FAILED`, …) |
| 2 | Usage error (`BAD_ARGUMENT`) |
| 3 | Missing dependency (`FFMPEG_NOT_FOUND`, `MODEL_NOT_FOUND`, `FEATURE_NOT_INSTALLED`) |
| 4 | Bad input (`FILE_NOT_FOUND`, `EDL_INVALID`, `SOURCE_CHANGED`) |
| 5 | Checks failed (`VERIFY_FAILED`, `LINT_FAILED`) |
| 6 | Waiting on an answer (`ORDERS_PENDING`) |
| 130 | Interrupted |

## Licence

MIT. ffmpeg runs as a separate process and is not redistributed.
