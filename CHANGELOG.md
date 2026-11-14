# Changelog

All notable changes to videocli. The agent contract (envelope, exit codes, error codes,
schemas and command interface) follows [semantic versioning](https://semver.org/): before
1.0, minor versions may change it and say so here.

## 0.9.0 — unreleased

Every phase of the plan is implemented (0–10). v1.0 waits on the published measurements the
spec requires: the phase-9 style-profile study, critic calibration, the preference win-or-tie
rate and a Timeline-Bench run (see docs/evaluation.md).

- Deep perception (phase 5): work orders with agent/local/api providers; shots, motion, faces
  (local, unnamed), prosody, events, music structure, embeddings, slides, diarization (group);
  `shots`, `people`, `speakers`, `music`, `slides`, `log`, `orders`, `suggest`, visual `find`.
- Story and style (phase 9): `brief`, `style learn|show|compare`, `story`, `selects`,
  `stringout`, `assemble` (radio, picture, music), music fitting at downbeats, `refine` passes
  (coverage, split-edits, action, beats, pacing, match, punch), `audit`.
- Director loop (phase 10): `critique` (independent pack and rubric), `review build` (static
  page, blind variants), `notes ingest|list|apply` (scoped, questions for ambiguity),
  `variants`, `tournament`, `direct`; `python -m eval preference …` and `timeline-bench …`.
- opencv-python-headless pinned below 5 (5.x dropped the Haar face detector).

## 0.5.0 — unreleased

Finishing: compositing, sound, colour and motion graphics (phases 6–8).

- Render graph chunks the timeline wherever layers change: spine-only chunks keep the
  phase-2 fast path; layered chunks are one small composite graph each; bakes are cached.
- `edl overlay|audio|transition|split-edit|speed|reframe|stabilize|sound`; music ducked under
  speech, room tone, dialogue clean-up, stems; `render --explain`; `export --to otio|fcpxml|edl`.
- Colour: grade ops compiled to one 3D LUT per clip (10-bit intermediates), input transforms
  for HLG/PQ/tagged/log sources, `scopes`, `grade auto|match|look|set|preview|reset`, `lut build`,
  `export --to cube|cdl`, verify checks `legal_range` and `added_clipping`.
- Graphics: templates with typed parameters and a brand kit, the `titles` baker (bundled Noto
  fonts, Devanagari shaping), Manim and HyperFrames bakers (optional), animated ASS captions
  (clean, bold-words, karaoke), word-anchored timing, `gfx lint`, previews.
- Agent skill topics: compositing, colour, graphics.
- Fix: `edl split --at` crashed when writing history.

## 0.1.0 — unreleased

First release: lectures, tutorials and talking heads (autonomy level L1).

- Foundation: one JSON envelope per command, stable exit codes and error registry, global
  options anywhere on the command line, settings with five-level precedence, workspace with
  content-hash source ids and a content-addressed cache, `doctor`, `setup` with profiles,
  one-line installers.
- Perception: `probe` (with colour metadata), `analyze` (audio, silence, loudness,
  transcript, scenes), `index`, `transcript`, `silences`, `find`.
- Planning: multi-track EDL schema (`videocli.edl/v1`), rule planners (ranges, silence,
  fillers, snap, smooth), `plan`, `edl show|cut|keep|restore|trim|split|move|lock|lint|diff|copy`.
- Output: cached, resumable render graph with frame-exact picture and sample-exact sound,
  presets, `--preview`, `--around-cuts`, captions (SRT/VTT, burn-in).
- Eyes and judgement: `frames`, `sheet`, `strip`, `scenes`, `verify`.
- Agents: packaged skill, `agent-guide`, `skill install`, `schema`, `commands`.
- Evaluation harness: `python -m eval run DIR`.
