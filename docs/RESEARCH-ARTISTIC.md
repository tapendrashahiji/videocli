# Research: finishing and artistic editing

**Date:** 2026-11-14 · Supports [SPEC.md](SPEC.md) v0.2

This document records what was researched before colour grading, compositing, motion graphics and artistic editing moved from non-goals to goals, and the design decision each finding led to.

Method: web search and paper reading, two more repositories cloned and read (`browser-use/video-use`, `heygen-com/hyperframes`), and five small ffmpeg prototypes run locally.

---

## 1. Where agents stand on artistic editing today

### Timeline-Bench (September 2026)

[Timeline-Bench](https://arxiv.org/abs/2609.35143) is a benchmark of 56 real editing assignments, from raw production material and a brief to a finished video. Sixteen agents were tested, pairing frontier models with coding-agent harnesses including Claude Code.

| Finding | Number |
|---|---|
| Best agent's resolved tasks | 15 of 56 (26.8%) |
| Average agent | 14.0% |
| Blind judgments where professional editors preferred the human reference | 83.5% |
| Preferred the agent edit | 11.1% |
| Unresolved runs that failed only the quality test | 562 of 771 |
| How often agent edits changed shot, relative to the reference | 0.72× |
| Longest static shot in agent edits, relative to the reference | 1.5× |

Other findings that shape this design:

- Agents do well at what a brief can state: delivering the right format, meeting explicit requirements, keeping the timeline clean.
- Agents are weak at rhythm, shot selection and finishing, and they cannot reliably judge their own edits. The authors link this to agents perceiving footage through stills and transcripts.
- Editors penalised a slower pace but not a faster one.
- Agents' own post-render fixes were mostly about rendering and sync. Human editors' notes were mostly about pacing, story and shot choice.
- Adding curated editorial guidance to the agent produced no measurable gain in human preference.
- The benchmark's automated quality test tracks human preference closely at the agent level, and the tasks and verifier are public.

### Other systems

- [VideoAgent](https://arxiv.org/abs/2606.23327) reports human ratings close to human-made videos on its own benchmark of short-form categories such as beat-synced edits.
- [Auto-Director](https://arxiv.org/abs/2610.28346) uses multi-agent collaboration and scores narrative logic, factual accuracy, semantic alignment, compositional quality and stylistic cohesion.
- `video-use` (read from source) has the agent self-review every cut boundary on the rendered output, measure loudness, cap self-review at three passes, and spawn a separate critic for anything that will be published.

### Decisions

| Finding | Decision |
|---|---|
| Technical correctness is not the bottleneck; craft is | Keep `verify`, and add a separate **audit** layer that measures pacing, shot variety and coverage |
| Agents under-cut and hold static shots too long | Pace metrics are first-class, compared against a reference style, with long static holds flagged |
| Agents cannot judge their own edits | Judgement is split in three: measured metrics, an independent critic, and human notes. Self-review alone never passes a gate |
| Prose guidance did not help | Taste comes from **reference edits turned into numbers** (style profiles) and from human notes, not from longer skill text |
| Human notes are about pacing, story and shots | The notes format and critic rubric are organised around those three |
| A public benchmark exists | Adopt Timeline-Bench as the external measure of the artistic goal, beside the project's own blind tests |
| The honest baseline is low | The goal is stated as a measured win-or-tie rate that must improve, not as a promise |

---

## 2. Colour grading

### Findings

- [LumiVideo](https://arxiv.org/abs/2604.02409) treats grading as choosing parameters instead of generating pixels. The agent searches over ASC CDL-style values, which compile into a 33×33×33 3D LUT exported as a `.cube` file. A physical stream measures luminance percentiles, a semantic stream describes the scene and names protected hue ranges such as skin and sky, and a critic scores candidates from rendered previews. One LUT per clip gives temporal consistency by construction. Its stated limit is that a global LUT cannot make local adjustments. In its human study the expert colourist still won more first-place votes (43.6% against 38.2%).
- `video-use` uses the same mental model: ASC CDL per channel, `out = (in × slope + offset) ^ power`, then saturation, applied per segment during extraction.
- [OpenColorIO](https://opencolorio.readthedocs.io/en/latest/releases/ocio_2_5.html) installs from PyPI as `opencolorio`, with wheels for Linux, macOS and Windows. Version 2.5 ships built-in ACES 2.0 configs, reachable without external files.
- The local ffmpeg build has `lut3d`, `haldclut`, `zscale`, `tonemap`, `colorspace`, `curves`, `colorbalance`, `colortemperature`, `selectivecolor`, `signalstats`, `histogram`, `waveform`, `vectorscope` and `libplacebo`.

### Prototype

A NumPy script built a 33³ `.cube` from CDL values (slope, offset, power, saturation 0.85). Applying it with ffmpeg `lut3d` changed measured average saturation from 57.0 to 50.1 and shifted hue and luma as expected. A 10-bit 4:2:2 intermediate (`yuv422p10le`) encoded and probed correctly.

### Decisions

| Finding | Decision |
|---|---|
| Parameters beat pixels for consistency and editability | A grade is a stack of numeric operations. videocli never generates or repaints pixels |
| Any colour-only stack fits in a 3D LUT | Every grade stack **compiles to one `.cube`**, applied by `lut3d`. Deterministic, cacheable, exportable to Resolve |
| ASC CDL is the industry's portable grade | CDL is the core operation and an export format |
| A global LUT cannot do local work | Spatial operations (windows, vignette, denoise) are separate render nodes that use masks |
| Agents judge colour poorly from compressed stills | Scope data as numbers comes first; sheets are a secondary check |
| Protected hues prevent ugly skin | Skin and sky ranges are constraints on every automatic grade |
| Log and HDR footage need real colour management | Input transforms through ffmpeg for tagged footage, camera LUTs for log, and OpenColorIO as an optional tier |
| 8-bit intermediates band under grading | Intermediates switch to 10-bit whenever a grade is present |

---

## 3. Compositing and render engine

### Options considered

| Option | Strength | Weakness | Verdict |
|---|---|---|---|
| One large ffmpeg filter graph | No new dependency | Fragile and slow with many clips; awkward keyframes | Rejected as the only path |
| ffmpeg graphs per small chunk | Robust, parallel, cacheable | Needs a compiler from timeline to chunks | **Chosen** for compositing |
| MLT (`melt`), the engine behind Kdenlive and Shotcut | A real multi-track engine | Not pip-installable across platforms; hard to fit one-command install | Export target only |
| Python frame compositor piping to ffmpeg | Fully flexible | Slow in NumPy; GPU adds heavy dependencies | Used only inside bakers |
| Browser rendering (HTML to video) | Excellent for graphics | Slow for long footage | A motion-graphics baker |
| Blender headless | Full compositor | Very large; version-pinned | Rejected |

`video-use` confirms the base pattern in production use: extract per segment, join without re-encoding, 30 ms audio fades at every boundary, overlays time-shifted to their window, subtitles last.

### Prototype

On ffmpeg 6.1.1:

- A clip with transparency (`qtrle`, `argb`) was overlaid on base footage at a time offset with `setpts` and `overlay`.
- `xfade` and `acrossfade` joined two 4 s clips with a 0.5 s dissolve, giving the expected 7.5 s.
- `sidechaincompress` ducked one audio signal under another.

### Decisions

| Finding | Decision |
|---|---|
| Small graphs are robust; big ones are not | The timeline compiles to a **render graph**, executed in chunks split wherever the set of active layers changes |
| Exotic effects do not fit ffmpeg expressions | Anything complex is **baked** first into a clip with transparency, then composited simply |
| Bakes are expensive | Every node is content-addressed and cached, so one change re-renders one node |
| Picture and sound have different needs | Picture and sound render as separate passes and are joined at the end, as in post-production |
| Split edits overlap audio | Dialogue is laid on alternating A and B lanes and mixed |

---

## 4. Motion graphics

### Findings

- **Remotion** is source-available, not open source. Its free licence covers individuals, non-profits and for-profit organisations of up to three employees; larger companies need a paid licence ([Remotion's own comparison page](https://www.remotion.dev/docs/compare/motion-canvas), [licence summary](https://capitalandcompute.net/blog/best-open-source-ai-video-tools/)).
- **HyperFrames** (read from source) is Apache 2.0, renders HTML, CSS and seekable animations to video, needs Node.js 22 or newer, and ships agent skills.
- **Motion Canvas** is MIT-licensed.
- **Manim** renders with transparency and suits diagrams, equations and graph morphs. `video-use` vendors a Manim skill for exactly this.
- `video-use` picks an engine per animation slot: HyperFrames, Remotion, Manim, or PIL with a PNG sequence.
- The local ffmpeg has `ass` and `subtitles` (libass), which can draw animated captions without any extra engine.

### Decisions

| Finding | Decision |
|---|---|
| No single engine fits every graphic | Motion graphics are **bakers** behind one contract: parameters in, a transparent clip out |
| An open-source project should not depend on a source-available engine | HyperFrames and Manim are the supported heavy bakers. Remotion is not bundled; users can attach it through the generic baker |
| Captions and simple cards are the common case | A base-tier `titles` baker uses libass and Pillow, with no extra install |
| Agents should be free to invent | A `generic` baker accepts any command that meets the contract |
| Graphics must land on spoken words | Graphics items anchor to transcript words, and the planner back-times the reveal |

---

## 5. Perception for taste

### Findings

- [The Anatomy of Video Editing](https://arxiv.org/abs/2207.09812) (AVE) labels about 196,000 shots from 5,591 movie scenes with cinematographic attributes, including shot size, angle, type, motion, location and subject. Its tasks include shot-attribute classification, shot ordering and next-shot selection.
- [MovieCuts](https://arxiv.org/abs/2109.05569) studies cut-type recognition, treating the transition between shots as part of film grammar.
- [allin1](https://pypi.org/project/allin1/) predicts tempo, beats, downbeats and labelled sections (intro, verse, chorus, bridge). It needs PyTorch, NATTEN and madmom, so its install is heavy.
- [MatAnyone 2](https://arxiv.org/abs/2512.11782) (CVPR 2026) is a strong human video-matting model. Reports describe its code licence as NTU S-Lab License 1.0, and SAM 3 weights need access approval. Licences of matting models need checking one by one.
- `video-use` argues the opposite extreme: only a packed transcript is worth precomputing, and everything else is derived by the model at decision time. That works for talking-head material and matches Timeline-Bench's observation about where such agents fall short.

### Decisions

| Finding | Decision |
|---|---|
| Film grammar has a shared vocabulary | Shot records use AVE-style attributes, so planners and style profiles speak one language |
| Stills and transcripts miss rhythm and motion | Motion, camera movement, prosody and music structure are computed as numbers over time |
| Model choice changes fast and licences vary | A **model provider** interface: local model, the calling agent, or an opt-in API. No model is hard-wired |
| The calling agent can already see | Visual logging can be issued as **work orders** the agent answers, at zero extra dependency |
| Heavy music and matting models | They live in optional tiers, each with its licence recorded |

---

## 6. What this research does not settle

- Whether style profiles built from reference edits raise human preference. This is the central bet of phase 9 and must be tested early.
- Which local vision model is good enough for shot logging. To be measured in phase 5 against work-order answers.
- How far ffmpeg-only compositing goes before a GPU compositor is needed.
- Whether Timeline-Bench's public verifier can run unchanged against videocli output.
