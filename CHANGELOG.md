# Changelog

All notable changes to videocli. The agent contract (envelope, exit codes, error codes,
schemas and command interface) follows [semantic versioning](https://semver.org/): before
1.0, minor versions may change it and say so here.

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
