# videocli

**A command-line video editor built for AI agents.**

videocli turns footage into text and numbers an agent can read, takes every creative decision
as a file (an EDL), renders with ffmpeg, and checks its own output. Every command is
non-interactive, prints one JSON document on stdout, and returns a stable exit code. It works
with Claude Code or any agent that has a shell, and it is pleasant for people too.

> **Status: v0.1 (lectures).** Clean-up of lectures, tutorials and talking heads: silence and
> filler removal, range edits, captions, verified renders. Compositing, colour, motion
> graphics and artistic editing are designed ([docs/SPEC.md](docs/SPEC.md)) and arrive in later
> releases ([docs/phases/](docs/phases/)).

## Install

```bash
curl -LsSf https://<project-domain>/install.sh | sh                      # asks which profile
curl -LsSf https://<project-domain>/install.sh | sh -s -- --profile lecture
```

```powershell
irm https://<project-domain>/install.ps1 | iex                          # Windows
```

The installer sets up [uv](https://docs.astral.sh/uv/), installs videocli as a tool, and runs
`videocli setup`. Run unattended (agents, CI) it installs the `lecture` profile without asking.
Until the package is on PyPI, install from a checkout:

```bash
uv tool install git+https://github.com/tapendrashahiji/videocli
videocli setup
```

Requirements: Python ≥ 3.10 (uv provides one) and ffmpeg ≥ 5.0 (the system copy is used).
`videocli doctor` reports what is missing and the exact command that fixes it.

## Sixty seconds

```bash
videocli analyze lecture.mp4                       # transcript, silences, loudness, scenes
videocli transcript lecture.mp4 --format compact --from 0 --to 120
videocli plan lecture.mp4 --cut-silence min=1.2,pad=0.15 --cut-fillers -o cut1
videocli edl show cut1                             # 61:12 → 48:40, what was removed and why
videocli edl lint cut1                             # clipped words? each issue has a fix command
videocli render cut1 -o lecture-cut.mp4            # cached, parallel, resumable
videocli verify lecture-cut.mp4 --edl cut1         # duration, sync, black, clicks, loudness…
videocli captions cut1 -o lecture-cut.srt
```

When piped, each command prints an envelope like:

```json
{"ok": true, "command": "plan", "version": "0.1.0",
 "data": {"output_duration": 2920.4, "removed_percent": 20.4, "by_reason": {"silence": 712.0, "filler": 31.2}},
 "artifacts": [{"kind": "edl", "path": ".videocli/edits/cut1.edl.json"}],
 "warnings": [], "next": ["videocli edl lint cut1"]}
```

## For agents

```bash
videocli skill install            # .claude/skills/videocli/SKILL.md (or --target generic for AGENTS.md)
videocli commands                 # the whole interface as JSON, generated from the CLI itself
videocli schema edl               # JSON Schema for any file or command output
```

See [docs/agents.md](docs/agents.md) for the contract (envelope, exit codes, time model).

## Documentation

- [Quick start](docs/quickstart.md) · [Agent contract](docs/agents.md) · [Command reference](docs/commands.md)
- [What it does well and cannot judge](docs/limits.md) · [Evaluation](docs/evaluation.md)
- Design: [SPEC](docs/SPEC.md), [decisions](docs/decisions.md), [research](docs/RESEARCH-ARTISTIC.md), [phases](docs/phases/)

## Uninstall

```bash
uv tool uninstall videocli
rm -rf ~/.cache/videocli ~/.local/share/videocli     # cache and downloaded models (Linux paths)
```

Workspaces are the `.videocli/` folders next to your footage; delete them to remove analysis
and render caches (`videocli cache clear` keeps your EDLs).

## Licence

MIT. ffmpeg runs as a separate process and is not redistributed. `videocli doctor` lists the
licence of every installed model and heavy library.
