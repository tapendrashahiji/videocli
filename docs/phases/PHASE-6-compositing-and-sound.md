# Phase 6: Compositing and sound

**Goal:** render every part of the EDL: overlays, picture-in-picture, transitions, split edits, reframing, stabilisation, music and a proper mix. Hand timelines to professional editors.

**Depends on:** phases 0 to 4. Reframing improves with phase 5 faces.
**Unlocks:** social clips, B-roll over interviews, and phases 7 and 8, which plug into the same graph.

**Design basis:** small ffmpeg graphs per chunk over pre-baked layers, with picture and sound rendered separately. See [RESEARCH-ARTISTIC.md](../RESEARCH-ARTISTIC.md) §3, including the prototypes.

---

## Scope

**In**

- Full render graph: chunking, `composite` and `bake` nodes
- Overlay track: cover and picture-in-picture, with transforms
- Transitions: cut, dissolve, fade, dip to colour
- Split edits (J and L cuts)
- Audio track: music, effects, room tone; ducking; clean-up; mix
- Reframing and stabilisation bakers
- Speed ramps and freeze frames
- Export to OpenTimelineIO, Final Cut XML and CMX 3600

**Out**

- Colour grading (phase 7), though the graph leaves the hook for it
- Motion-graphics bakers beyond a placeholder (phase 8)
- Deciding where B-roll or music should go (phase 9)

---

## Commands delivered

```
videocli edl overlay NAME --source FILE --from T --to T --on CLIP_ID [--offset S]
                          [--mode cover|pip] [--pos tl|tr|bl|br|X,Y] [--scale F] [--mute] [--opacity F]
videocli edl audio NAME --source FILE --role music|sfx|tone [--at OUT_T] [--from T] [--to T]
                        [--gain DB] [--duck DB] [--fade S] [--fit]
videocli edl transition NAME CLIP_ID --type dissolve|fade|dip [--duration S]
videocli edl split-edit NAME CLIP_ID [--lead S] [--lag S]
videocli edl speed NAME CLIP_ID --rate F | --ramp "0:1.0,0.6:2.5,1:1.0" | --freeze S
videocli edl reframe NAME [--aspect 9:16|1:1|4:5] [--mode center|subject] [--clip CLIP_ID]
videocli edl stabilize NAME CLIP_ID [--strength F]
videocli edl sound NAME [--denoise F] [--level] [--room-tone auto|FILE]
videocli render NAME ...                      # now renders all tracks
videocli export NAME --to otio|fcpxml|edl [-o PATH] [--relative-to DIR]
```

---

## Tasks

### Graph compiler (`render/graph.py`)

- [ ] **Chunking:** split the output timeline at every point where the set of active overlays, graphics or transitions changes
- [ ] Spine-only chunks keep the phase-2 fast path, unchanged
- [ ] Layered chunks compile to one `composite` node each, with its own small filter graph written to a script file
- [ ] `bake` nodes are scheduled before the composites that use them
- [ ] Chunk boundaries fall on frames and start on a keyframe, so chunks join by stream copy
- [ ] `render --dry-run` shows chunks, their layers and which are cached
- [ ] `render --explain ITEM_ID` shows which nodes an item affects

### `composite` node

- [ ] Base plus layers, in track order; graphics above overlays; captions never here (they are burned last, in `encode`)
- [ ] `cover`: scaled to fill, replaces the picture for its window
- [ ] `pip`: scale, position, optional border and rounded corners, opacity
- [ ] Each layer is time-shifted so its first frame lands on its window start
- [ ] Layers with transparency are composited with their alpha
- [ ] Transitions between two spine clips: `xfade` family for dissolve and fade, with the output shortened by the overlap; `dip` through a colour
- [ ] Transition handles: lint that both clips have enough spare source material beyond their cut points

### Bake framework (`render/bakers/`)

- [ ] `Baker` protocol and registry per SPEC §10.6
- [ ] Contract check on every result: duration, frame rate, resolution, transparency, determinism hash
- [ ] Intermediate formats with alpha: `qtrle` for short bakes, ProRes 4444 for long ones (decisions.md T3)
- [ ] `stabilize` baker: two-pass ffmpeg stabilisation, with the crop reported
- [ ] `reframe` baker: per-shot crop for a target aspect. `center` is fixed. `subject` uses face positions from phase 5, holds one crop per shot, and never moves mid-word
- [ ] `generic` baker: runs a command declared in the EDL inside the workspace, then applies the contract check
- [ ] Bake previews: a still or a short clip per bake for inspection

### Speed (`segment` node)

- [ ] Constant rate (existing), ramps as piecewise curves, and freeze frames
- [ ] Sound follows constant-rate changes with pitch preserved; ramps and freezes take sound from the mix rules (mute, or keep natural sound under)

### Sound (`sound/`)

- [ ] **Split edits:** `lead` and `lag` extend a clip's sound before or after its picture. Lane placement handles the overlap; a short cross-fade is applied where two clips overlap on different lanes
- [ ] Audio track items: `music`, `sfx`, `tone`, placed at output time or anchored to a clip
- [ ] **Ducking:** music lowered under speech using speech ranges from the index, with attack and release times; a sidechain compressor is the fallback when ranges are missing
- [ ] `--fit`: trim or loop music to the edit length at beat or section boundaries when phase-5 music data exists, otherwise fade
- [ ] **Room tone:** find a quiet stretch of the source, loop it under gaps and cuts so the background never drops to digital silence
- [ ] Clean-up presets: noise reduction, levelling across speakers, gentle compression, high-pass
- [ ] Stems: the mix can also write dialogue, music and effects as separate files
- [ ] Final loudness to target with a true-peak ceiling; per-role levels reported

### EDL operations and lint

- [ ] The commands above, each validating and appending to `history`
- [ ] Anchors: overlays and graphics follow their spine clip (logic from phase 2)
- [ ] Lint: overlay longer than its clip allows, overlapping overlays on one layer, transition without handles, music shorter than the edit, split edit running past the neighbour clip, reframe crop below a minimum resolution

### Export (`export/`)

- [ ] `otio`: tracks, clips, transitions and markers through the OpenTimelineIO library; grades and graphics carried as metadata
- [ ] `fcpxml`: spine, connected clips for overlays, audio roles
- [ ] `edl`: CMX 3600 for the spine, with a warning listing what was left out
- [ ] Baked layers exported as media files the editor can relink
- [ ] Round-check: re-read each written file and compare clip count and total duration

---

## Tests

- Chunking: a timeline with two overlays and one transition yields the expected chunk list
- Changing one overlay re-renders only the chunks it covers
- Frame-counter sources: every layer shows the expected frame at its window start
- Transparency: a semi-transparent baked layer blends to the expected pixel values
- Dissolve shortens output by exactly the overlap; lint catches a transition without handles
- Split edit: sound from clip A is audible for `lag` seconds after the picture cuts to B
- Ducking: music level under speech is lower by the configured amount, measured on the mix
- Room tone: no digital silence across a cut
- Picture and sound masters stay equal in length with every feature combined
- Reframe: output dimensions are right and the face stays inside the frame on a test clip
- Bake contract: wrong duration or missing alpha is rejected with `BAKE_FAILED` and a reason
- Exported OTIO re-loads with matching clip counts and durations
- `verify` passes on a multi-track render

---

## Acceptance criteria

1. An agent can build a 45-second vertical clip from an interview with B-roll over one passage, a music bed ducked under speech, one dissolve and burned captions, and `verify` passes.
2. An L-cut is audible and correct: the previous speaker's last words continue over the next shot.
3. Editing one overlay and re-rendering takes a small fraction of the first render's time.
4. `videocli export NAME --to otio` opens in DaVinci Resolve with every cut, layer and transition in place.
5. A spine-only lecture EDL from phase 2 renders byte-for-byte as before.

---

## Risks

| Risk | Mitigation |
|---|---|
| Chunk joins show a visible or audible seam | Keyframe-aligned chunks, stream copy, sound built on one continuous timeline; a seam test on every feature |
| Composite graphs grow fragile | Each chunk's graph is small by construction; operations are a fixed, tested set; anything else is a bake |
| Alpha intermediates are large | Short-lived cache with pruning; codec chosen by bake length |
| Speed ramps and sound do not mix well | Explicit rules for what sound does under a ramp; lint warns |
| Editor import differs between tools | Prefer OTIO; document tested versions; keep a manual import checklist |
| ffmpeg-only compositing hits a ceiling | The bake contract is the escape hatch; revisit a GPU compositor only when measured need appears |
