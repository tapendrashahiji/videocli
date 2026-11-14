# Phase 0: Foundation

**Goal:** an installable `videocli` that proves the agent contract end to end with the simplest real command, `probe`.

**Depends on:** nothing.
**Unlocks:** every later phase. Get the contract right here, because changing it later breaks agents.

---

## Scope

**In**

- Repository, packaging, CI skeleton
- CLI root, global options, JSON envelope, error tree, exit codes
- ffmpeg wrapper, `doctor`, `setup`
- Workspace, source identity, cache (analysis keys and render-node keys)
- `probe` including colour metadata, `config`, `cache`, `jobs`
- Setup profiles and dependency groups

**Out**

- Any analysis beyond ffprobe
- Model downloads other than the plumbing for them
- The agent skill file (phase 4)

---

## Commands delivered

```
videocli --version
videocli doctor
videocli setup [--profile lecture|finishing|artistic|full] [--with LIST] [--offline-check]
videocli probe FILE
videocli config show | set KEY VALUE
videocli cache status | prune [--older-than 30d] | clear
videocli jobs
```

### `doctor` data

```json
{
  "python": "3.12.4",
  "platform": "linux-x86_64",
  "ffmpeg": { "path": "/usr/bin/ffmpeg", "version": "7.1", "ok": true, "missing_features": [] },
  "ffprobe": { "path": "/usr/bin/ffprobe", "version": "7.1", "ok": true },
  "models": { "whisper-small": false },
  "groups": { "base": true, "perception": false, "diarize": false, "music": false, "stems": false, "ocio": false, "manim": false, "html": false, "matte": false, "otio": false },
  "licences": [ { "name": "faster-whisper", "licence": "MIT" } ],
  "gpu": { "cuda": false },
  "ready": false,
  "fix": [ "videocli setup" ]
}
```

`ready` is true when the base workflow can run. `fix` lists the exact commands that would make it true.

---

## Tasks

### Repository and packaging

- [ ] Name is `videocli` (decisions.md D1): reserve it on PyPI with a placeholder release; check the GitHub organisation and repository names
- [ ] `pyproject.toml` with hatchling, `requires-python = ">=3.10"`, script `videocli = "videocli.cli.app:main"`
- [ ] `src/` layout and `__main__.py` so `python -m videocli` works
- [ ] ruff, pyright and pytest configuration
- [ ] `LICENSE`, `README.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`
- [ ] CI workflow: lint, type-check, tests on Linux, macOS, Windows × Python 3.10 and 3.13

### `core/errors.py`

- [ ] `VideoCliError(code, message, hint=None, retryable=False, details=None)` base class
- [ ] Subclasses mapped to exit codes: `UsageError` 2, `DependencyError` 3, `InputError` 4, `CheckFailed` 5, `Pending` 6, `RuntimeFailure` 1
- [ ] One registry of error codes with a one-line description each, exported by `videocli commands`

### `cli/app.py` and `cli/output.py`

- [ ] Typer root app with the global options from SPEC §6.6
- [ ] `--estimate` hook: a command may return expected time and disk use without running
- [ ] `main()` is the only place that catches exceptions and sets the exit status
- [ ] Unknown exceptions become `INTERNAL_ERROR`, exit 1, with a log path; no traceback on stdout
- [ ] `KeyboardInterrupt` exits 130; `BrokenPipeError` exits quietly
- [ ] Output mode selection: `--json`, `VIDEOCLI_OUTPUT`, or not-a-terminal
- [ ] `emit(data, artifacts, warnings, next)` builds the success envelope
- [ ] Human renderer: a registered Rich formatter per command, with a generic fallback
- [ ] Usage errors from Typer are re-wrapped into the error envelope in JSON mode

### `core/config.py`

- [ ] One Pydantic `Settings` model with every key, default and description
- [ ] Loader with precedence: flag → env → project file → user file → default
- [ ] `config show` prints effective values and where each came from

### `media/ffmpeg.py`

- [ ] `locate()` following the order in SPEC §4.3
- [ ] `capabilities()` parses `-version`, `-filters`, `-encoders`, `-demuxers`, cached per process
- [ ] `run(args, *, progress_total=None, log_name)` with list arguments only, `-nostdin`, `-hide_banner`
- [ ] `file:` prefix helper for local paths
- [ ] stderr to a log file; on failure raise `FFMPEG_FAILED` with the last stderr line
- [ ] Progress parser for `-progress pipe:1`
- [ ] `run_probe(args) -> dict` for ffprobe JSON

### `setup/`

- [ ] `doctor.py` builds the report above
- [ ] `provision.py`: download ffmpeg static build to the user cache with a checksum check
- [ ] Model download plumbing (the model itself is used in phase 1)
- [ ] Dependency groups and profiles per SPEC §4.2, defined in one table
- [ ] `setup --profile` and `setup --with` install groups into the tool's own environment
- [ ] Feature checks per group, so `doctor` reports which groups are usable
- [ ] Licence listing for every installed model and heavy library
- [ ] `install.sh` and `install.ps1`: ensure uv, `uv tool install videocli`, `videocli setup --profile NAME`
- [ ] Profile prompt (decisions.md D6): with a terminal attached, show Lecture, Finishing and Full with rough sizes and ask; read the answer from the terminal device, since standard input is the piped script
- [ ] `--profile NAME` skips the prompt; with no terminal, install `lecture` without asking and print the command to add more

### `core/workspace.py`, `source.py`, `cache.py`

- [ ] Create and open `.videocli/` with `workspace.json` and a schema version
- [ ] `source_id` hashing per SPEC §5.2
- [ ] Register a source; detect a changed file at a known path (`SOURCE_CHANGED`)
- [ ] Artifact key, read, write (atomic), header fields
- [ ] Generic content-addressed store used later by render nodes, LUTs and bakes: `key → directory`, with least-recently-used pruning
- [ ] Job state files, so an interrupted long command can resume; `jobs` lists unfinished ones
- [ ] Workspace lock file for writers
- [ ] `cache status|prune|clear`

### `media/probe.py`

- [ ] ffprobe call with `-show_format -show_streams -of json`
- [ ] Normalise into `ProbeResult`: duration, container, video, audio list, subtitles
- [ ] Detect rotation metadata and variable frame rate
- [ ] Colour metadata: primaries, transfer, matrix, range, bit depth; classify SDR, HLG or PQ; guess a camera log profile from tags where present
- [ ] Warnings `HDR_SOURCE` and `LOG_SOURCE`, so later phases know an input transform is needed
- [ ] Warnings: no audio stream, VFR, odd dimensions, unusual pixel format, very long file

### `core/timeutil.py`

- [ ] Parse `SS.mmm`, `MM:SS`, `HH:MM:SS.mmm`
- [ ] Rational frame-rate type; seconds ↔ frame index
- [ ] Snap to frame; half-open range helpers: merge, subtract, intersect, complement
- [ ] Source time ↔ output time mapping helpers (used by every later phase)

### `core/progress.py`

- [ ] Event emitter with `bar`, `json`, `none` sinks, writing to stderr
- [ ] `@step("name")` decorator emitting `started`, `progress`, `finished`

---

## Tests

- Envelope shape for success and each error class, in JSON mode
- Exit code for each error class
- JSON mode is selected automatically when stdout is piped
- Config precedence across all five levels
- `source_id` is stable across rename and changes when content changes
- Range helpers: property-style tests for merge, subtract and complement
- `probe` on synthetic clips generated with `lavfi`: video and audio, audio-only, video-only, rotated, 10-bit, HLG-tagged
- A profile resolves to the expected groups; a missing group yields `FEATURE_NOT_INSTALLED` with the right hint
- ffmpeg failure produces `FFMPEG_FAILED` with the last stderr line and a log file
- A path containing spaces, a colon, and a leading dash is handled

---

## Acceptance criteria

1. On a clean machine, the one-line installer asks for a profile, finishes, and `videocli doctor` reports `ready: true`. Run unattended, it installs `lecture` without asking.
2. `videocli probe sample.mp4 | python -m json.tool` succeeds, and the output validates against the probe schema.
3. `videocli probe missing.mp4` prints an error envelope with `FILE_NOT_FOUND` and exits 4.
4. `videocli --help` starts in under 300 ms with a warm disk cache.
5. CI is green on all three operating systems.

---

## Risks

| Risk | Mitigation |
|---|---|
| Static ffmpeg builds differ by platform | Pin per platform; feature-check in `doctor`; allow a system ffmpeg |
| Typer startup time grows with imports | Lazy imports; a CI test that fails above the startup budget |
| Windows path and console encoding problems | Windows in CI from the first commit; force UTF-8 output |
| Contract churn after release | Freeze envelope and exit codes at the end of this phase |
| Later phases need things the foundation lacks | The store, job state, colour metadata and time mapping are built now, because phases 5 to 10 all depend on them |
