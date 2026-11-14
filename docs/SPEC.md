# videocli: implementation specification

**Status:** draft v0.2 · **Date:** 2026-11-14 · **Working name:** `videocli`

An open-source, single-package command-line tool that lets an AI agent (Claude Code or any agent with a shell) edit video. It turns footage into text and numbers the agent can reason over, takes every creative decision as a file, renders with ffmpeg, and judges the result.

Related documents:

- [REFERENCE-STUDY.md](REFERENCE-STUDY.md): lessons from auto-editor, PySceneDetect and yt-dlp
- [RESEARCH-ARTISTIC.md](RESEARCH-ARTISTIC.md): research behind colour, compositing, motion graphics and artistic editing
- [decisions.md](decisions.md): every decision made so far, and what is still deferred
- [phases/](phases/): one build document per phase (0 to 10)

### What changed in v0.2

| Area | v0.1 | v0.2 |
|---|---|---|
| Colour grading, compositing, motion graphics | Non-goals | Goals (phases 6 to 8) |
| Artistic editing | Non-goal | Goal, measured by blind preference (phases 9 and 10) |
| EDL | Linear list; tracks added later as v2 | Multi-track from the first version |
| Render | Cut and join | Render graph with bake nodes; cut and join is its fast path |
| Models | None built in | Model-provider interface: local, calling agent, or opt-in API |
| Judgement | `verify` only | `verify`, `audit`, `critique` and human `notes` |
| Phases | 0 to 6 | 0 to 10 |

---

## 1. Goals and non-goals

### Goals

1. **Agent-first.** Every command is non-interactive, prints one JSON document on stdout, and returns a stable exit code.
2. **Monolith.** One repository, one Python package, one executable. No server, daemon or database.
3. **One-command install.** A single line installs the tool and everything the chosen profile needs.
4. **Decisions are files.** Analysis produces facts. Every creative decision (cut, grade, layer, graphic, mix) is a number in a file that a person or agent can read and change.
5. **Verifiable.** The tool checks its own output and reports problems as data.
6. **Local and private.** No network calls after setup unless the user enables an API provider. No telemetry.
7. **Finishing.** Colour grading, compositing and motion graphics are first-class, deterministic and parameter-driven.
8. **Artistic editing, measured.** Produce edits of documentary, narrative and music-driven material, and measure them against human editors by blind preference. The measured rate is published and must improve release over release.

### On goal 8

No design can guarantee taste. Current evidence sets the baseline: on Timeline-Bench, the best agent resolved 26.8% of tasks, and professional editors preferred the human reference in 83.5% of blind judgments (see [RESEARCH-ARTISTIC.md](RESEARCH-ARTISTIC.md)). The goal is therefore a direction with a number attached, tracked on an autonomy ladder:

| Level | Name | What the tool does | Human role |
|---|---|---|---|
| L0 | Assist | Logs, searches, suggests | Edits |
| L1 | Clean-up | Removes dead air and fillers | Spot-checks |
| L2 | Rough cut | Assembles a first cut from a brief | Reviews everything |
| L3 | Fine cut with notes | Revises from human notes, round by round | Gives notes, approves |
| L4 | Unsupervised final | Delivers without review | None |

Targets: L1 for lectures at v0.1. L3 for artistic work at v1.0. L4 on artistic work is measured and reported, never promised.

### Non-goals

- A GUI or interactive timeline editor (a static review page is in scope)
- Generating or repainting pixels with generative models
- 3D, particle simulation and heavy visual effects
- Real-time playback
- Importing timelines edited in other tools (export only, for now)
- A hosted rendering service

### Target material, in order

1. Lectures, tutorials, talking heads (v0.1)
2. Interviews, podcasts, social clips (v0.5)
3. Documentary, narrative scenes, music-driven edits (v1.0)

---

## 2. The core idea

The agent works through files. Five layers produce and consume them:

```
 sources ──► PERCEPTION ──► index, shots, colour, music …      (facts)
                 │
 brief, refs ──► EDITORIAL MODEL ──► style profile, story        (intent)
                 │
                 ▼
             PLANNING ──► EDL: tracks, grades, graphics, mix     (decisions)
                 │
                 ▼
             RENDER GRAPH ──► picture + sound ──► output         (execution)
                 │
                 ▼
             JUDGEMENT ──► verify, audit, critique, notes        (feedback)
                 └────────────── back to planning ──────────────┘
```

When the agent needs eyes, it asks for frames or a contact sheet and looks at the image itself. When a judgement needs a model, the tool issues a **work order** that the agent, a local model or an API can answer.

---

## 3. Architecture

### 3.1 Principles

| Principle | Rule |
|---|---|
| Layering | `cli` → domain packages. Nothing imports `cli`. |
| Facts, intent, decisions | Analyzers emit facts. Profiles and stories hold intent. Planners write decisions. Render only reads decisions. |
| Parameters, not pixels | Every effect is editable numbers. A grade is a LUT built from numbers; a graphic is a template plus parameters. |
| Bake, then composite | Complex effects render first into clips with transparency. Final compositing stays simple. |
| Content-addressed | Every analysis artifact and render node has a key from its inputs. Unchanged work is never redone. |
| Validate first | Check inputs, dependencies and schemas before any long work. |
| One ffmpeg door | Every ffmpeg and ffprobe call goes through `media/ffmpeg.py`. |
| Lazy imports | Heavy libraries load inside the function that needs them. `videocli --help` starts in under 300 ms. |
| Independent judgement | Whatever made an edit does not get the final say on it. |

### 3.2 Package layout

```
videocli/
├── pyproject.toml
├── install.sh / install.ps1
├── src/videocli/
│   ├── cli/                   # Typer only: parse → call → format
│   │   ├── app.py, output.py
│   │   └── commands/
│   ├── core/                  # errors, config, workspace, source ids,
│   │                          # cache, timeutil, progress, jobs
│   ├── media/                 # ffmpeg wrapper, probe, audio, frames, sheets
│   ├── analyze/               # one module per analyzer + registry
│   │   ├── base.py
│   │   ├── transcript.py, silence.py, loudness.py, scenes.py      # phases 1-3
│   │   ├── shots.py, motion.py, faces.py, prosody.py, events.py   # phase 5
│   │   ├── music.py, embed.py, slides.py, diarize.py              # phase 5
│   │   └── color.py                                               # phase 7
│   ├── providers/             # model providers: local, agent, api
│   │   └── orders.py          # work-order protocol
│   ├── index/                 # build, search
│   ├── edl/                   # model, io, ops, lint, planners/
│   ├── editorial/             # brief, style, story, idioms, solver   (phase 9)
│   ├── render/
│   │   ├── graph.py           # compile EDL → nodes; keys; scheduler
│   │   ├── nodes/             # segment, composite, concat, audio, encode
│   │   ├── bakers/            # titles, manim, html, matte, stabilize, generic
│   │   ├── captions.py, presets.py
│   ├── color/                 # ops, lut compiler, match, input transforms
│   ├── sound/                 # lanes, mix, duck, music fit, cleanup
│   ├── judge/                 # verify, audit, critique, notes, review page
│   ├── export/                # otio, fcpxml, cmx3600, cube, cdl
│   ├── setup/                 # doctor, provision, profiles
│   └── agent/                 # SKILL.md files, guide, schema export
├── eval/                      # accuracy and preference harnesses
├── tests/
└── docs/
```

### 3.3 Technology choices

| Concern | Choice | Reason |
|---|---|---|
| Language | Python ≥ 3.10 | ML ecosystem |
| CLI | Typer | Typed commands, generated help |
| Schemas | Pydantic v2 | Validation and JSON Schema export |
| Media engine | ffmpeg and ffprobe by subprocess | No binding to maintain; licence stays separate |
| Transcription | faster-whisper | Word timestamps without PyTorch |
| Shot detection | PySceneDetect | Mature, small interface |
| Frame maths | OpenCV headless, NumPy | Stills, sheets, visual metrics, LUT building |
| Grade model | ASC CDL plus a small op set, compiled to a 3D LUT | Portable, editable, deterministic |
| Colour management | ffmpeg `zscale`/`colorspace`, camera LUTs, OpenColorIO (optional) | Covers tagged, log and HDR sources |
| Compositing | ffmpeg graphs per chunk over baked layers | Robust and cacheable |
| Motion graphics | libass and Pillow (base); Manim and HyperFrames (optional) | Open licences; one contract |
| Interchange | OpenTimelineIO, FCPXML, CMX 3600, `.cube`, `.cdl` | Handoff to professional tools |
| Build and dev | hatchling, uv, ruff, pytest, pyright | Standard |

---

## 4. Installation and distribution

### 4.1 The one command

```bash
curl -LsSf https://<project-domain>/install.sh | sh                      # asks which profile
curl -LsSf https://<project-domain>/install.sh | sh -s -- --profile artistic
```

The script installs `uv` if missing, runs `uv tool install videocli`, then `videocli setup --profile NAME`.

Choosing the profile:

- With a terminal attached, the installer shows the profiles with their rough size and asks. It reads the answer from the terminal device, because under `curl … | sh` standard input is the script.
- `--profile NAME` skips the question.
- With no terminal (an agent, CI, a container), it installs `lecture` and prints the command to add more.

### 4.2 Profiles and tiers

All code ships in one distribution. Third-party libraries and models are grouped, and a profile selects groups.

| Group | Contents | Needed for |
|---|---|---|
| `base` | typer, rich, pydantic, platformdirs, numpy, faster-whisper, scenedetect, opencv-python-headless, pillow | Phases 0 to 4; basic grade, composite and titles |
| `perception` | onnxruntime and vision models | Shot attributes, faces, visual search |
| `diarize` | speaker diarization library and model | Speaker labels |
| `music` | beat, downbeat and section analysis | Music-driven cuts |
| `stems` | source separation | Dialogue and music separation |
| `ocio` | opencolorio | Log, HDR and ACES colour |
| `manim` | Manim | Diagram and equation graphics |
| `html` | Node.js ≥ 22 and HyperFrames | HTML, CSS and typographic graphics |
| `matte` | video matting or segmentation model | Background removal, windows |
| `otio` | opentimelineio | Editor export |

| Profile | Groups |
|---|---|
| `lecture` (unattended default) | `base` |
| `finishing` | `base`, `ocio`, `html`, `otio` |
| `artistic` | `finishing` plus `perception`, `music`, `diarize` |
| `full` | everything |

A missing group returns `FEATURE_NOT_INSTALLED` with the exact `videocli setup --with …` command as the hint.

### 4.3 ffmpeg provisioning

Resolution order: `VIDEOCLI_FFMPEG` and `VIDEOCLI_FFPROBE` → `PATH` (version ≥ 5.0 with required features) → a pinned, checksum-verified static build in the user cache.

`doctor` checks features per group. Base: `silencedetect`, `ebur128`, `loudnorm`, `afade`, `atempo`, `scale`, `subtitles`, `blackdetect`, `freezedetect`, `libx264`, `aac`, concat demuxer. Finishing adds: `lut3d`, `zscale`, `overlay`, `xfade`, `acrossfade`, `sidechaincompress`, `signalstats`, `maskedmerge`, `vidstabdetect`, a 10-bit encoder, and an encoder with alpha (`qtrle`, `prores_ks` or `libvpx-vp9`).

**Open decision:** which static build to pin per platform, confirmed to include every feature above.

### 4.4 Models and licences

Models download during `setup` or on first use. Every model and heavy library is listed by `doctor` with its licence. Several matting and segmentation models carry research or gated licences, so each is recorded and the user is told before download.

Proposed project licence: **MIT**. ffmpeg runs as a separate process and is not redistributed in the wheel.

---

## 5. Workspace and cache

### 5.1 Layout

```
.videocli/
├── workspace.json
├── config.toml
├── sources/<source_id>/        # probe, audio.wav, transcript, silence, loudness,
│                               # scenes, shots, color, music, index, frames/, sheets/
├── project/
│   ├── brief.json              # what the edit is for
│   ├── story.json              # beats and selects
│   └── styles/<name>.style.json
├── edits/<name>.edl.json
├── grades/<key>.cube           # compiled LUTs
├── bakes/<key>/                # baked layers with transparency
├── graph/<key>/                # cached render nodes
├── orders/<id>/                # work orders and answers
├── reports/                    # verify, audit, critique, notes
├── review/                     # static review pages
└── logs/
```

### 5.2 Identity and keys

- `source_id`: first 12 hex characters of SHA-256 over file size plus three 1 MiB blocks (start, middle, end). Survives rename and move.
- Analysis key: `sha256(source_id, analyzer, analyzer_version, params)`.
- Render node key: `sha256(node_kind, params, input_keys, tool_versions)`.
- Every artifact starts with `schema`, `key`, `created`, `tool_version` and `params`.
- `videocli cache status|prune|clear` manages disk use. Bakes and graph nodes are pruned least-recently-used first.

---

## 6. Agent contract

This section is the public interface and follows semantic versioning.

### 6.1 Output

- JSON mode when `--json` is given, `VIDEOCLI_OUTPUT=json` is set, or stdout is not a terminal.
- stdout carries exactly one JSON document. Logs and progress go to stderr.

```json
{
  "ok": true,
  "command": "analyze",
  "version": "0.2.0",
  "data": { },
  "artifacts": [ { "kind": "transcript", "path": ".videocli/sources/a1b2c3d4e5f6/transcript.json" } ],
  "warnings": [ { "code": "LOW_CONFIDENCE_WORDS", "message": "…" } ],
  "next": [ "videocli transcript lecture.mp4 --format compact" ]
}
```

```json
{
  "ok": false,
  "command": "render",
  "version": "0.2.0",
  "error": {
    "code": "FFMPEG_FAILED",
    "message": "ffmpeg exited with status 1: Invalid data found when processing input",
    "hint": "Run: videocli probe lecture.mp4",
    "retryable": false,
    "details": { "log": ".videocli/logs/render-20261114-101500.log" }
  }
}
```

### 6.2 Exit codes

| Code | Meaning | Examples |
|---|---|---|
| 0 | Success | |
| 1 | Runtime failure | `FFMPEG_FAILED`, `ANALYZER_FAILED`, `BAKE_FAILED` |
| 2 | Usage error | `BAD_ARGUMENT` |
| 3 | Missing dependency | `FFMPEG_NOT_FOUND`, `MODEL_NOT_FOUND`, `FEATURE_NOT_INSTALLED` |
| 4 | Bad input | `FILE_NOT_FOUND`, `EDL_INVALID`, `SOURCE_CHANGED` |
| 5 | Checks failed | `VERIFY_FAILED`, `LINT_FAILED`, `AUDIT_FAILED` |
| 6 | Waiting on an answer | `ORDERS_PENDING`, `REVIEW_PENDING` |
| 130 | Interrupted | |

### 6.3 Token discipline

- Lists default to `--limit 50` with `total` and `truncated`.
- Large results go to files; the envelope returns the path and a summary.
- `--from` and `--to` on every read command.
- Compact text formats for transcripts and shot lists.
- Contact sheets default to 1600 px wide or less.

### 6.4 Progress, estimates and long jobs

- `--progress json` writes newline-delimited events to stderr.
- `--estimate` returns expected time and disk use without doing the work.
- Renders are **resumable**. State lives in the graph cache, so re-running the same command continues where it stopped. There is no background service. `videocli jobs` lists unfinished renders from their state files.

### 6.5 Work orders

A work order asks someone with eyes or ears to answer structured questions. It is how the tool gets model judgement without hard-wiring a model.

```json
{
  "schema": "videocli.order/v1",
  "id": "ord_20261114_0007",
  "kind": "shot_log",
  "items": [
    {
      "id": "s0042",
      "images": [ ".videocli/sources/a1b2c3d4e5f6/sheets/shot-0042.jpg" ],
      "question": "Describe this shot for an editor.",
      "answer_schema": { "description": "string", "shot_size": "ECU|CU|MCU|MS|WS|EWS", "usable": "boolean" }
    }
  ]
}
```

- A command that needs answers creates the order and exits with code 6 and `ORDERS_PENDING`.
- The configured **provider** answers: `agent` (the calling agent reads the images and submits answers), `local` (a local model), or `api` (opt-in, user's key).
- `videocli orders submit ID answers.jsonl` validates each answer against its schema and stores who answered.
- Re-running the original command then proceeds.

### 6.6 Global options

`--json` · `--workspace PATH` · `--progress MODE` · `--dry-run` · `--estimate` · `--force` · `--quiet` · `--verbose` · `--jobs N` · `--provider NAME` · `--version`

### 6.7 Self-description

`videocli agent-guide [--topic]` · `videocli skill install` · `videocli schema [NAME]` · `videocli commands`

The skill is split by topic as the tool grows: core, colour, compositing, graphics, story, review.

---

## 7. Time model

- Public times are **seconds with millisecond precision**.
- Each EDL declares a `rate` as a rational string such as `"30000/1001"`. On save, every picture time snaps to a timeline frame.
- Sound is sample-accurate and may differ from picture times (split edits).
- Ranges are half-open.
- Source times address source media. Output times address the finished timeline. Every command says which it takes.
- Variable-frame-rate sources are flagged by `probe` and conformed to the timeline rate.

---

## 8. Data schemas

Shapes are abbreviated. Pydantic models in code are authoritative; `videocli schema` exports them.

### 8.1 Probe: `videocli.probe/v1`

```json
{
  "schema": "videocli.probe/v1",
  "source_id": "a1b2c3d4e5f6",
  "path": "interview.mov",
  "duration": 3612.48,
  "container": "mov",
  "video": {
    "codec": "prores", "width": 3840, "height": 2160, "fps": "25/1", "vfr": false, "rotation": 0,
    "pix_fmt": "yuv422p10le", "bit_depth": 10,
    "color": { "primaries": "bt2020", "transfer": "arib-std-b67", "matrix": "bt2020nc", "range": "tv", "hdr": "hlg", "log_guess": null }
  },
  "audio": [ { "index": 0, "codec": "pcm_s24le", "sample_rate": 48000, "channels": 2 } ],
  "warnings": [ { "code": "HDR_SOURCE", "message": "Source is HLG; an input transform is required." } ]
}
```

### 8.2 Transcript, silence, loudness, scenes

Unchanged from v0.1: `videocli.transcript/v1` (segments, words, probabilities, optional speaker), `videocli.silence/v1`, `videocli.loudness/v1`, `videocli.scenes/v1`.

### 8.3 Shot record: `videocli.shots/v1` (phase 5)

One record per shot, using AVE-style attributes.

```json
{
  "id": "s0042", "start": 512.20, "end": 518.96,
  "size": "MCU", "angle": "eye", "type": "single", "motion": "handheld", "location": "interior", "subject": "human",
  "faces": [ { "person": "p1", "area": 0.11, "x": 0.38, "y": 0.31 } ],
  "metrics": { "motion": 0.07, "blur": 412.0, "brightness": 0.44, "action_peak": 515.6 },
  "description": "Woman at a workbench explains the mould, looking off camera left.",
  "provenance": { "attributes": "local:clip-zero-shot", "description": "agent" }
}
```

### 8.4 Index: `videocli.index/v1`

A summary plus pointers to every artifact available for a source.

### 8.5 EDL: `videocli.edl/v1`

One schema from the first release. Phase 2 implements `tracks.main`; other parts are validated from the start and become renderable in their phase.

```json
{
  "schema": "videocli.edl/v1",
  "name": "doc-rough-03",
  "rate": "25/1",
  "resolution": [ 1920, 1080 ],
  "color": { "working": "rec709", "output": "rec709" },
  "sources": {
    "a1b2c3d4e5f6": { "path": "interview.mov", "duration": 3612.48, "fps": "25/1", "input_transform": "hlg_to_rec709" },
    "0f9e8d7c6b5a": { "path": "broll_workshop.mp4", "duration": 240.0, "fps": "25/1" },
    "77aa88bb99cc": { "path": "score.wav", "duration": 190.0 }
  },
  "tracks": {
    "main": [
      { "id": "c0001", "source": "a1b2c3d4e5f6", "start": 512.20, "end": 531.04, "speed": 1.0,
        "audio": { "gain_db": 0.0, "lead": 0.0, "lag": 0.6 },
        "grade": "g_interview", "reason": "beat:hook", "locked": false }
    ],
    "overlay": [
      { "id": "o0001", "source": "0f9e8d7c6b5a", "start": 40.0, "end": 46.5, "anchor": "c0001", "offset": 6.0,
        "mode": "cover", "mute": true, "grade": "g_broll" }
    ],
    "graphics": [
      { "id": "x0001", "baker": "titles", "template": "lower-third", "params": { "title": "Maya Shrestha", "subtitle": "Potter" },
        "anchor": "c0001", "offset": 1.0, "duration": 4.0 }
    ],
    "audio": [
      { "id": "m0001", "source": "77aa88bb99cc", "role": "music", "start": 0.0, "at": 0.0, "gain_db": -18.0, "duck_db": -10.0, "fade": 1.5 }
    ]
  },
  "transitions": [ { "after": "c0001", "type": "dissolve", "duration": 0.4 } ],
  "grades": {
    "look": { "ops": [ { "op": "cdl", "slope": [1.04, 1.0, 0.96], "offset": [0.0, 0.0, 0.01], "power": [1.0, 1.0, 1.0], "sat": 0.92 } ] },
    "g_interview": { "ops": [ { "op": "exposure", "stops": 0.3 }, { "op": "temperature", "kelvin_shift": -250 } ] },
    "g_broll": { "ops": [ { "op": "match", "to": "g_interview", "strength": 0.8 } ] }
  },
  "captions": { "mode": "none", "style": "default" },
  "mix": { "target_lufs": -14.0, "true_peak_db": -1.0, "fade_ms": 15 },
  "markers": [ { "at": 12.4, "kind": "note", "text": "Hold longer on the hands." } ],
  "output": { "preset": "1080p", "container": "mp4", "vcodec": "h264", "crf": 18, "acodec": "aac", "audio_bitrate": "192k" },
  "history": [ { "op": "assemble", "at": "2026-11-14T10:15:00Z" } ]
}
```

Rules:

- `main` is the spine and defines output time.
- Items on other tracks **anchor** to a spine clip with an `offset`, so they follow that clip when the spine changes. Music uses absolute output time (`at`).
- `audio.lead` and `audio.lag` make split edits: sound starts before or runs past the picture cut.
- A clip names a grade by key. Order of application: input transform → clip grade → timeline `look` → output transform.
- `locked: true` protects an item from automatic planners.
- `history` records every operation.

### 8.6 Grade operations (phase 7)

| Op | Parameters | Compiles into the LUT |
|---|---|---|
| `cdl` | slope, offset, power (RGB), sat | Yes |
| `exposure` | stops | Yes |
| `temperature` | kelvin_shift, tint | Yes |
| `contrast` | amount, pivot | Yes |
| `curves` | control points per channel | Yes |
| `qualifier` | hue, sat and luma ranges, then a nested `cdl` | Yes |
| `rolloff` | highlight threshold | Yes |
| `lut` | path to a `.cube` | Yes (composed) |
| `match` | reference grade or frame, strength | Resolved to `cdl` at plan time |
| `window` | mask or shape, feather, nested ops | No: a masked render node |
| `vignette`, `denoise`, `sharpen`, `deband` | amounts | No: spatial filters |

### 8.7 Style profile: `videocli.style/v1` (phase 9)

Numbers measured from reference edits.

```json
{
  "schema": "videocli.style/v1",
  "name": "short-doc-ref",
  "from": [ "ref1.mp4", "ref2.mp4" ],
  "pace": { "asl": 3.8, "median_shot": 2.9, "p90_shot": 8.4, "max_static_hold": 11.0, "cuts_per_min_curve": [ 14, 16, 19, 22, 15 ] },
  "grammar": { "size_transitions": { "WS>MCU": 0.21, "MCU>CU": 0.18 }, "split_edit_rate": 0.34, "dissolve_rate": 0.03, "broll_coverage": 0.46 },
  "music": { "presence": 0.72, "cut_on_beat_rate": 0.41, "duck_db": -11.0 },
  "speech": { "wpm": 162, "kept_pause_p50": 0.32 },
  "color": { "contrast": 0.61, "saturation": 0.47, "warmth": 0.08 },
  "sound": { "integrated_lufs": -14.2 }
}
```

### 8.8 Story: `videocli.story/v1` (phase 9)

```json
{
  "schema": "videocli.story/v1",
  "logline": "A potter keeps a 300-year-old kiln alive.",
  "target_duration": 180,
  "beats": [
    { "id": "hook", "intent": "Show the fire before explaining it.", "target_seconds": 12,
      "selects": [ { "source": "a1b2c3d4e5f6", "start": 512.2, "end": 531.0, "score": 0.86, "why": "Strong first line, clean delivery." } ],
      "chosen": [ 0 ] }
  ]
}
```

### 8.9 Reports

| Schema | Written by | Holds |
|---|---|---|
| `videocli.verify/v1` | `verify` | Technical checks: duration, sync, black, clicks, loudness, legal colour |
| `videocli.audit/v1` | `audit` | Craft metrics against a style profile: pace, static holds, jump cuts, coverage, beat alignment, shot consistency |
| `videocli.critique/v1` | `critique` | Rubric scores and timecoded problems from an independent judge |
| `videocli.notes/v1` | `notes ingest` | Human notes, each tied to a timecode and a category |

---

## 9. Commands

One command does one step. Full flags are in the phase documents.

| Layer | Commands | Phase |
|---|---|---|
| Environment | `doctor`, `setup`, `config`, `cache`, `jobs` | 0 |
| Perception | `probe`, `analyze`, `index`, `transcript`, `silences`, `find` | 0, 1 |
| | `scenes`, `frames`, `sheet`, `strip` | 3 |
| | `shots`, `people`, `speakers`, `music`, `slides`, `log`, `orders`, `suggest` | 5 |
| | `scopes` | 7 |
| Planning | `plan`, `edl show\|cut\|keep\|trim\|split\|move\|lock\|restore\|lint\|diff\|copy` | 2 |
| | `edl overlay\|audio\|transition\|split-edit\|speed\|reframe\|stabilize\|sound` | 6 |
| | `grade input\|set\|auto\|match\|look\|show\|preview\|reset`, `lut build` | 7 |
| | `gfx templates\|add\|list\|set\|remove\|preview\|lint`, `brand`, `captions style` | 8 |
| | `brief`, `style learn\|show\|compare`, `story`, `selects`, `stringout`, `assemble`, `refine` | 9 |
| Output | `render`, `captions` | 2 |
| | `export --to otio\|fcpxml\|edl\|cube\|cdl` | 6, 7 |
| Judgement | `verify` | 3 |
| | `audit` | 9 |
| | `critique`, `review build`, `notes ingest\|list\|apply`, `variants`, `tournament`, `direct` | 10 |
| Agent | `agent-guide`, `skill install`, `schema`, `commands` | 4 |

---

## 10. Key subsystems

### 10.1 Analyzers

```python
class Analyzer(Protocol):
    name: str
    version: int
    requires: tuple[str, ...]
    schema: str
    def params_model(self) -> type[BaseModel]: ...
    def available(self) -> Availability: ...
    def run(self, src: Source, params: BaseModel, ctx: RunContext) -> Path: ...
```

Analyzers register in an in-code table. `analyze` resolves dependencies, skips cached artifacts and rebuilds the index.

### 10.2 Model providers

```python
class Provider(Protocol):
    name: str                                        # "agent", "local", "api"
    def can_answer(self, kind: str) -> bool: ...
    def answer(self, order: Order) -> Answers | Pending: ...
```

- `agent` writes the order and returns `Pending`. This is the default and adds no dependency.
- `local` runs an installed model.
- `api` is off unless the user configures a key.
- Every stored answer records its provider, so results from different providers are never silently mixed.

### 10.3 ffmpeg wrapper

List arguments only, `file:` path prefix, `-nostdin`, stderr to a log, last stderr line in the error, progress from `-progress pipe:1`, features detected once per process.

### 10.4 Planners

Two families write into the EDL:

- **Rule planners** (phase 2): ranges, silence, fillers, snap, smooth. Deterministic clean-up.
- **Editorial planners** (phase 9): selects, assembly, B-roll coverage, split edits, cut-on-action, cut-to-beat, music fitting. Each is an idiom with a cost function, combined by a solver, with weights taken from a style profile.

Locked items always pass through.

### 10.5 Render graph

`render` compiles the EDL into a graph of nodes and runs it.

| Node | Does | Output |
|---|---|---|
| `segment` | Trim, speed, input transform, grade LUT, reframe for one spine clip | Intermediate video (10-bit when graded) |
| `bake` | Runs a baker: graphic, matte, stabilised clip, title | Clip with transparency |
| `composite` | Stacks layers and transitions for one chunk | Intermediate video |
| `picture` | Joins chunks without re-encoding | Picture master |
| `lane` | Extracts and fades one audio clip onto dialogue lane A or B | PCM |
| `mix` | Lanes, music and effects; ducking; loudness | Sound master |
| `encode` | Joins picture and sound, burns captions last, encodes delivery | Output file |

Execution rules:

1. **Chunking.** The output timeline is split wherever the set of active layers or transitions changes. A chunk with only a spine clip takes the fast path (one `segment`). A chunk with layers gets one small `composite` graph.
2. **Keys.** Each node's key comes from its parameters and its inputs' keys. Changing one overlay re-renders one chunk.
3. **Picture and sound are separate.** Picture chunks join by stream copy. Sound is built from PCM lanes and mixed once. They meet only in `encode`.
4. **Dialogue lanes.** Neighbouring spine clips alternate between lanes A and B, so split edits and cross-fades can overlap without special cases.
5. **Order inside a composite.** Base → overlays in track order → graphics → captions last.
6. **Parallel and resumable.** Independent nodes run in parallel up to `--jobs`. Finished nodes are skipped on re-run.
7. **Previews** use the same graph at low resolution. `--around-cuts S` renders only the chunks near joins.

### 10.6 Bakers

A baker turns parameters into a clip with transparency.

```python
class Baker(Protocol):
    name: str
    version: int
    def available(self) -> Availability: ...
    def bake(self, item: BakeItem, ctx: RunContext) -> BakeResult: ...   # path, duration, size, fps
```

Contract: exact duration, frame rate and resolution as requested; transparency present; deterministic for the same parameters. `gfx lint` checks each result.

| Baker | Tier | Use |
|---|---|---|
| `titles` | base | Lower thirds, cards, animated captions (libass, Pillow) |
| `stabilize` | base | Two-pass stabilisation |
| `reframe` | base, better with `perception` | Crop path for vertical and square output |
| `manim` | `manim` | Diagrams, equations, graph animations |
| `html` | `html` | Typography, UI motion, data graphics (HyperFrames) |
| `matte` | `matte` | Subject mattes for background replacement and colour windows |
| `generic` | base | Any command that meets the contract |

### 10.7 Colour pipeline

```
source ─► input transform ─► clip grade ─► look ─► output transform ─► encode
          (tags, camera LUT,   └──── compiled into one .cube ────┘
           or OpenColorIO)                 applied by lut3d
```

- `scopes` measures each shot as numbers: luma percentiles, clipping share, channel means, saturation, white-balance estimate, skin-tone deviation where faces exist, and difference from neighbouring shots.
- `grade auto` balances and matches shots within constraints: protected skin and sky hues, no added clipping.
- A grade is always numbers. `lut build` writes the compiled `.cube`, which also opens in other tools.
- Working space is Rec.709 with 10-bit intermediates. An ACES working space through OpenColorIO is optional.

### 10.8 Sound

- Dialogue on A/B lanes with short fades at every edge.
- Room tone can fill gaps left by cuts.
- Music is fitted to length at section and downbeat boundaries, then ducked under speech using speech ranges from the index.
- Clean-up presets: noise reduction, levelling, compression.
- Final loudness and true peak are measured and enforced.

### 10.9 Judgement

| Step | Question | Judge | Blocks delivery |
|---|---|---|---|
| `edl lint` | Is the plan well-formed? | Rules | Yes |
| `verify` | Is the render technically correct? | Measurements | Yes |
| `audit` | Does the craft match the reference style? | Metrics against a style profile | Configurable |
| `critique` | Would an editor accept it? | An independent provider with a rubric | Configurable |
| `notes` | What does the human want changed? | A person, through the review page | Yes, at L3 and below |

The critic never shares context with whatever built the edit. Its rubric weights pacing, story and shot choice above technical defects, because that is where human editors' notes concentrate.

---

## 11. Agent workflows

### 11.1 Lecture (L1)

```bash
videocli analyze lecture.mp4
videocli plan lecture.mp4 --cut-silence min=1.2,pad=0.15 --cut-fillers -o cut1
videocli edl lint cut1
videocli render cut1 -o lecture-cut.mp4
videocli verify lecture-cut.mp4 --edl cut1
```

### 11.2 Artistic (L2 to L3)

```bash
videocli analyze footage/ --profile artistic          # facts, shots, music
videocli log footage/                                 # work orders → shot descriptions
videocli style learn refs/*.mp4 -o house              # taste as numbers
videocli brief set --logline "…" --duration 180 --style house
videocli selects                                      # candidates per story beat
videocli assemble -o rough                            # radio edit, then picture
videocli refine rough --passes coverage,split-edits,beats
videocli grade auto rough && videocli gfx add rough …
videocli render rough --preview -o rough.mp4
videocli verify rough.mp4 --edl rough
videocli audit rough --style house                    # craft metrics
videocli critique rough                               # independent judge
videocli review build rough                           # page for the human
videocli notes ingest notes.json && videocli notes apply rough -o fine
```

Skill rules: never invent timestamps; change the EDL through commands; lint before render and verify after; never treat your own review as approval; show the plan before long renders; never overwrite a source.

---

## 12. Quality, testing and evaluation

| Layer | Approach |
|---|---|
| Unit | Pure functions: time maths, EDL ops, planners, LUT compiler, solver, metrics |
| Integration | Synthetic media from ffmpeg `lavfi`; frame-counter sources for exact-frame checks |
| Golden | Stable EDL, LUT and report bytes for fixed inputs |
| Contract | Every command's JSON validates against its schema |
| Slow | Real models on short clips, behind a marker |
| Platforms | Linux, macOS, Windows on Python 3.10 to 3.13 |

Evaluation, by goal:

| Goal | Measure |
|---|---|
| Clean-up (L1) | Cut-boundary precision and recall against human EDLs |
| Colour | Shot-to-shot difference after matching; blind preference against an ungraded and a colourist version |
| Graphics and compositing | Contract lint pass rate; timing error against anchor words |
| Artistic (L2 to L4) | Blind win-or-tie rate against human edits on the project's own set, and resolved rate on Timeline-Bench |

Performance targets remain goals to measure, not results. See each phase.

---

## 13. Security, safety and privacy

- No `shell=True`; no string-built commands. The `generic` baker runs only commands the user or agent wrote into the EDL, inside the workspace, and `render` lists them under `--dry-run`.
- Sources are read-only; outputs never overwrite a source; writes are atomic.
- Downloads only during `setup`, pinned and checksummed.
- No telemetry. The `api` provider is off by default and names what it would send.
- Face grouping stays local. People are stored as `p1`, `p2`; names are added only by the user.
- EDL, style, story and order files are validated before use.

---

## 14. Phases and milestones

| Phase | Name | Result |
|---|---|---|
| [0](phases/PHASE-0-foundation.md) | Foundation | Installable CLI, contract, `doctor`, `setup`, `probe`, workspace, cache |
| [1](phases/PHASE-1-audio-perception.md) | Audio perception | Transcript, silence, loudness, index, search |
| [2](phases/PHASE-2-edl-and-render.md) | EDL and render | Multi-track schema, rule planners, render graph fast path, captions |
| [3](phases/PHASE-3-eyes-and-verify.md) | Eyes and verify | Scenes, frames, sheets, verify |
| [4](phases/PHASE-4-agent-and-release.md) | Agent and release | Skill, schemas, installer, evaluation. **v0.1: lectures** |
| [5](phases/PHASE-5-deep-perception.md) | Deep perception | Shots, people, motion, prosody, music, providers and work orders |
| [6](phases/PHASE-6-compositing-and-sound.md) | Compositing and sound | Full render graph, overlays, transitions, split edits, mix, reframe, export |
| [7](phases/PHASE-7-colour.md) | Colour | Scopes, grade ops, LUT compiler, matching, looks, colour management |
| [8](phases/PHASE-8-motion-graphics.md) | Motion graphics | Bakers, templates, animated captions. **v0.5: finishing** |
| [9](phases/PHASE-9-story-and-style.md) | Story and style | Brief, style profiles, selects, assembly, editorial planners, audit |
| [10](phases/PHASE-10-director-loop.md) | Director loop | Critique, review and notes, variants, preference evaluation. **v1.0: artistic** |

Phases 5 to 8 are largely independent of each other. Phase 9 needs 5 and 6. Phase 10 needs 9.

**Chosen build order:** 0 → 1 → 2 → 3 → 4 (v0.1) → 6 → 7 → 8 (v0.5) → 5 → 9 → 10 (v1.0). Phase 5 may start in parallel with 6 to 8 if capacity allows; its work-order protocol is the part to pull forward.

---

## 15. Decisions

All decisions are recorded in [decisions.md](decisions.md). In summary:

| Topic | Decision |
|---|---|
| Name | `videocli` |
| Licence | MIT |
| ffmpeg | System copy first, otherwise a pinned download |
| Languages | English first; others best-effort; default speech model `small` |
| Model judgement | The calling agent, through work orders |
| Install | The installer asks for a profile; unattended installs get `lecture` |
| Colour | Rec.709 working space; ACES optional |
| Motion graphics | Manim and HyperFrames, both optional |
| Artistic goal at v1.0 | Fine cut with human notes (L3) |
| Style profiles | Tested with a small blind study before phase 9 is built |
| Build order after v0.1 | Phase 6, then 7 and 8, then 5, 9, 10 |
| Packaging | Command line only until after v0.1 |

Still deferred until measured: the ffmpeg build to pin, planner defaults, the local vision model, the music library, critic gating, artistic release gates, and whether to add an MCP server or standalone binary.
