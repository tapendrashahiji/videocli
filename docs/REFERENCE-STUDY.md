# Reference study: what three CLIs teach us

Three repositories were cloned and read at their `HEAD` on 2026-11-14:

| Project | Why it was chosen | Language | Licence |
|---|---|---|---|
| [auto-editor](https://github.com/WyattBlue/auto-editor) | Closest product: analyze, decide cuts, render or export | Nim (was Python) | Unlicense |
| [PySceneDetect](https://github.com/Breakthrough/PySceneDetect) | Clean mid-sized Python CLI over a reusable library | Python ≥ 3.10 | BSD-3-Clause |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Mature Python CLI: packaging, error handling, ffmpeg chain | Python ≥ 3.11 | Unlicense |

Each section lists what the source actually does, then the decision videocli takes from it.

---

## 1. auto-editor

### What the source shows

- **It is no longer Python.** The project is built from `ae.nimble` and `src/*.nim` into one binary that links FFmpeg libraries directly. For reading Python, use an older tagged release.
- **Pipeline shape.** `src/analyze/` (audio, motion, blackdetect, subtitle) produces per-timebase levels. `src/edit.nim` turns levels into booleans with a small expression language (`or`, `and`, `xor`, `not`). `src/conductor.nim` builds a timeline. `src/render/` or `src/exports/` writes the result.
- **Labels, then actions.** Every moment gets an integer label. Label 0 is inactive, label 1 is active, and 2–255 are custom classes. Each label maps to an action (`cut`, `speed`, `zoom`, and so on). Detection and action are separate concepts.
- **Three timeline formats.** `src/timeline.nim` defines `v1` (a list of `[start, end, speed]` chunks for one source), `v2` (clips plus an effects table), and `v3` (multi-track `v` and `a` lists of clips with `src`, `start`, `dur`, `offset`, `stream`).
- **Planning without rendering.** `planEdit` in `conductor.nim` returns every cut as data and refuses timelines that are not a simple list of cuts. There is also a `--preview` flag.
- **Pacing controls.** `--margin` pads each kept section. `--smooth` sets a minimum cut length and a minimum clip length.
- **Cache.** `src/cache.nim` keys each analysis on a SHA-1 of path, mtime, size, timebase and arguments. Files live in a temp directory named with the tool version, carry a one-byte format header so a stale file fails to decode, and are pruned to the newest 255.
- **Many export targets.** `src/exports/` has Final Cut (fcp7, fcp11), Shotcut, Kdenlive, MLT, CMX 3600 EDL, OpenTimelineIO and JSON.
- **Agent skills ship in the repo.** `skills/` holds five `SKILL.md` files (core, effects, export, transcribe, transitions), split by topic.
- **Machine modes exist.** `--json` flags on info commands and a `machine` progress-bar type.
- **Hand-rolled argument parser** with "did you mean" suggestions for unknown options.

### What videocli takes

| Lesson | Decision |
|---|---|
| Detection and action are separate | Analyzers only emit facts. Planners turn facts into an EDL. Render only reads an EDL. |
| Start with the simplest timeline | The first release renders only the spine. The schema is multi-track from the start (spec v0.2), so nothing is replaced later. |
| Plan as data before rendering | `plan` writes an EDL file and never renders. `render --dry-run` prints the job list. |
| Margin and smoothing decide quality | `pad`, `min_cut` and `min_clip` are first-class planner parameters. |
| Version the cache and make it self-describing | Cache keys include analyzer version and parameters. Artifacts carry a `schema` field. |
| Ship skills with the tool | `SKILL.md` lives inside the package and is installed by `videocli skill install`. |
| Split skills by topic | Start with one skill file. Split when it passes roughly 300 lines. |

### What videocli does differently

- auto-editor expresses edits as flags and a mini-language on one command line. An agent edits better by reading and modifying a file, so the EDL is the interface.
- auto-editor's cache key uses path and mtime. videocli hashes content, so a moved project keeps its cache.

---

## 2. PySceneDetect

### What the source shows

- **Packaging.** `pyproject.toml` with setuptools. One console script: `scenedetect = "scenedetect.__main__:main"`. Core dependencies are `click`, `numpy`, `opencv-python`, `platformdirs` and `tqdm`. PyAV and MoviePy are extras.
- **The CLI is a private sub-package.** `scenedetect/_cli/` holds all Click code. The library (`scene_manager.py`, `detectors/`, `backends/`, `output/`) never imports it.
- **Two-phase execution.** The root command is `click.group(chain=True, invoke_without_command=True)`. Subcommands such as `detect-content` or `split-video` do not do work. They validate options and register themselves on a `CliContext` (`add_detector`, `add_command`). After parsing, `run_scenedetect(context)` in `controller.py` runs detection, post-processes the scene list, then calls each registered handler.
- **A small detector interface.** `SceneDetector` is an abstract class with `process_frame(timecode, frame_img) -> list[cuts]`, optional `post_process`, and `get_metrics`.
- **One table of defaults.** `_cli/config.py` has a `CONFIG_MAP` dictionary holding every option's default per command, plus `ValidatedValue` classes. Config file and CLI share it.
- **Optional backends by import guard.** `backends/__init__.py` wraps each optional import in `try/except ImportError`, sets the name to `None`, and exposes `AVAILABLE_BACKENDS`.
- **ffmpeg by subprocess.** `output/video.py` checks whether ffmpeg is available, then runs one ffmpeg call per scene and shows ffmpeg's own output only for the first.
- **Opt-in slow tests.** A `release` pytest marker is excluded by default.

### What videocli takes

| Lesson | Decision |
|---|---|
| CLI layer is separate from the library | `videocli/cli/` only parses, calls core, and formats. Nothing imports `cli`. |
| Validate everything before any work | Every command validates inputs and dependencies first, so a long job never fails late on a typo. |
| Small analyzer interface | One `Analyzer` protocol with `name`, `version`, `available()` and `run()`. |
| One defaults table | A single Pydantic settings model is the source for defaults, config file, env vars and `--help`. |
| Import guards for optional features | Heavy libraries are imported inside functions. Missing ones produce a structured error with the fix. |
| Slow tests are opt-in | A `slow` marker for tests that load real models. |

### What videocli does differently

- Chained subcommands in one invocation are convenient for humans but hard for agents to recover from when the middle step fails. videocli uses one command per step, with files between them.

---

## 3. yt-dlp

### What the source shows

- **Zero required dependencies.** `dependencies = []` in `pyproject.toml`. Everything else is in a `default` extra. `yt_dlp/dependencies/__init__.py` tries each import and sets missing ones to `None`.
- **Entry points.** `yt-dlp = "yt_dlp:main"` plus a `__main__.py` so `python -m yt_dlp` works. A PyInstaller hook entry point supports standalone binaries.
- **Exit codes at one place.** `main()` wraps `_real_main()` and maps exception classes to exit statuses: 1 for download-type failures, 2 for option-parsing errors, with explicit handling for `KeyboardInterrupt` and `BrokenPipeError`.
- **Simulation and JSON.** `--simulate` does no work. `-j/--dump-json` and `-J/--dump-single-json` print machine-readable results.
- **Post-processor chain.** Each `PostProcessor.run(info)` receives an info dictionary and returns it for the next step. A metaclass wraps `run` so every processor emits `started` and `finished` progress events without writing that code itself.
- **A disciplined ffmpeg wrapper.** `postprocessor/ffmpeg.py` locates executables and detects versions and features once. `real_run_ffmpeg` builds an argument list, captures stderr, and on a bad return code raises an error carrying the last stderr line. Filenames are passed with a `file:` prefix because a name containing `:` is read as a protocol and a leading `-` is read as an option.
- **Plugins** load from a `yt_dlp_plugins` namespace package.

### What videocli takes

| Lesson | Decision |
|---|---|
| One place maps errors to exit codes | A single `main()` catches `VideoCliError` subclasses and emits the error envelope and exit code. |
| Every side effect has a simulate mode | `--dry-run` on every command that writes or renders. |
| Progress as a wrapper, not per-step code | A decorator emits `started`, `progress`, `finished` events for every analyzer and render step. |
| One ffmpeg wrapper | All ffmpeg and ffprobe calls go through `media/ffmpeg.py`: argument lists only, `file:` prefix, captured stderr, last line in the error. |
| Detect ffmpeg features once | `doctor` reports version and required filters and encoders. |
| `python -m` works | `videocli/__main__.py` is provided. |

### What videocli does differently

- yt-dlp has a plugin system. videocli is a monolith: analyzers register in an in-code table, with no third-party plugin loading in v1.

---

## 4. Prior art for agent-driven video editing

A web search (not a source read) found existing projects in this space. Review them before coding to avoid rebuilding what exists and to find the gap videocli fills:

- [browser-use/video-use](https://github.com/browser-use/video-use): a Claude Code skill for editing raw takes
- [MakeMyClip/editor](https://github.com/MakeMyClip/editor): a Claude Code skill plus a `clip` CLI
- [mcp-video](https://pastorsimon1798.github.io/mcp-video): an MCP server, Python library and CLI
- ffmpeg-skill: FFmpeg scripts for agents with a probe, edit, verify workflow
- [ButterCut](https://github.com/barefootford/buttercut): timelines for Final Cut and Premiere from Claude Code

**The gap videocli targets:** a single installable binary-like tool with a versioned EDL file as the contract, a content-addressed analysis cache, and a built-in verify step, usable by any agent that has a shell.
