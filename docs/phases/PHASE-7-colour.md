# Phase 7: Colour

**Goal:** correct, match and grade footage with editable numbers, and bring log and HDR sources into a consistent working space.

**Depends on:** phases 0 to 4 and the render graph from phase 6. Skin-tone handling improves with phase 5 faces.
**Unlocks:** finished-looking output, and the colour part of style profiles in phase 9.

**Design basis:** a grade is a stack of numeric operations that compiles to one 3D LUT. See [RESEARCH-ARTISTIC.md](../RESEARCH-ARTISTIC.md) §2, including the LUT prototype.

---

## Scope

**In**

- Colour metadata handling and input transforms
- Scopes as numbers
- Grade operations and the LUT compiler
- Automatic balance and shot matching
- Looks, including look transfer from a reference
- Spatial operations: windows, vignette, denoise, sharpen
- Colour checks in `verify`
- Export of `.cube` and ASC CDL

**Out**

- Generative relighting or repainting
- HDR delivery mastering (HDR sources are supported; HDR output is a later decision)
- Deciding the creative look without a brief or reference (phase 9 supplies those)

---

## Commands delivered

```
videocli scopes FILE|NAME [--shots] [--at T] [--from T] [--to T] [--sheet]
videocli grade input NAME --source ID --transform auto|NAME|LUT.cube
videocli grade set NAME (--clip ID | --group KEY | --look) --op 'cdl slope=1.05,1,0.95 sat=0.9'
videocli grade auto NAME [--balance] [--match] [--reference CLIP_ID|IMAGE] [--strength F]
videocli grade match NAME --clip ID --to CLIP_ID|IMAGE [--strength F]
videocli grade look NAME --from REF.mp4|REF.jpg|LOOK.cube [--strength F]
videocli grade show NAME [--clip ID]
videocli grade preview NAME [--clip ID] [--split] [--candidates N]
videocli grade reset NAME [--clip ID]
videocli lut build NAME [--clip ID | --look] -o OUT.cube [--size 33|65]
videocli export NAME --to cube|cdl
```

### `scopes --shots` data

```json
{
  "shots": [
    {
      "clip": "c0001",
      "luma": { "p1": 0.04, "p50": 0.41, "p99": 0.93, "clipped_high": 0.002, "crushed_low": 0.000 },
      "rgb_mean": [ 0.44, 0.40, 0.37 ],
      "saturation": 0.31,
      "white_balance": { "kelvin_shift": 320, "tint": -0.02 },
      "skin": { "present": true, "hue_offset_deg": 6.5 },
      "delta_to_previous": 9.8
    }
  ]
}
```

Numbers come first. `--sheet` adds a before and after sheet for a secondary visual check.

---

## Pipeline

```
source ─► input transform ─► clip grade ─► look ─► output transform ─► encode
                             └──────── one compiled .cube ───────┘
                                       applied with lut3d
spatial ops (window, vignette, denoise, sharpen) run as separate filters or masked nodes
```

- Working space: Rec.709. Intermediates are 10-bit whenever a clip has a grade or an input transform.
- Grade keys in the EDL (`grades`) let many clips share one grade, so a scene changes in one place.

---

## Tasks

### Input transforms (`color/input.py`)

- [ ] `auto`: choose from probe colour metadata. SDR Rec.709 passes through; other tagged spaces convert through ffmpeg colour filters; HLG and PQ are tone-mapped to SDR
- [ ] Camera log: apply a user or vendor `.cube`; a small table maps detected log profiles to the transform the user must supply
- [ ] `ocio` group: OpenColorIO transforms with its built-in ACES configs, baked to a LUT so render stays on ffmpeg
- [ ] Untagged footage: assume Rec.709 and warn
- [ ] The input transform is stored per source in the EDL

### Scopes (`analyze/color.py`)

- [ ] Per shot, on sampled frames after the input transform: luma percentiles, clipped and crushed share, channel means, saturation, hue histogram
- [ ] White-balance estimate from near-neutral pixels
- [ ] Skin-tone hue offset when phase-5 faces exist
- [ ] Perceptual difference between neighbouring shots on the timeline
- [ ] Results cached per source range and input transform

### Grade operations (`color/ops.py`)

- [ ] Operations per SPEC §8.6, each a pure function on RGB arrays
- [ ] `cdl`: `out = (in × slope + offset) ^ power` per channel, then saturation with Rec.709 luma weights
- [ ] `exposure`, `temperature` with tint, `contrast` with pivot, `curves`, `rolloff`
- [ ] `qualifier`: a soft mask from hue, saturation and luma ranges, applied to a nested `cdl`
- [ ] `lut`: an external `.cube` composed into the stack
- [ ] A text form for `--op`, and validation of ranges

### LUT compiler (`color/lut.py`)

- [ ] Evaluate the whole stack (clip grade, then look) on a 33³ or 65³ lattice with NumPy
- [ ] Write `.cube`; key it by the stack's content and store it in the cache
- [ ] `segment` nodes apply it with `lut3d`, with trilinear or tetrahedral interpolation
- [ ] Monotonicity and range checks, so a broken stack cannot ship
- [ ] Round-trip test against direct evaluation of the ops

### Automatic correction (`color/match.py`)

- [ ] `--balance`: neutralise the white-balance estimate and set exposure from luma percentiles, expressed as `temperature`, `exposure` and `cdl` ops
- [ ] `--match`: fit a `cdl` that moves a clip's statistics toward a reference clip or image, in a perceptual space, limited by `--strength`
- [ ] Group clips by source and scene, so one shoot gets one correction
- [ ] **Constraints:** skin hue stays within a tolerance of the reference line; protected hue ranges (skin, sky, foliage) move less; no added clipping
- [ ] Every automatic result is ordinary ops in the EDL, so a person or agent can adjust them
- [ ] Candidate mode: produce N variants, measure each, and return them ranked with their numbers

### Looks

- [ ] `grade look --from LOOK.cube`: attach a creative LUT as the timeline look
- [ ] `--from REF`: measure contrast, saturation, warmth and split-tone from a reference frame or video and fit look ops to them
- [ ] `--strength` blends the look toward neutral
- [ ] A small built-in set of neutral starting looks, documented as starting points

### Spatial operations

- [ ] `vignette`, `denoise`, `sharpen`, `deband` as filters after the LUT
- [ ] `window`: a shape (ellipse, rectangle, gradient) or a baked matte limits nested ops; rendered as a masked merge of graded and ungraded picture
- [ ] Windows that follow a face use phase-5 positions, held and smoothed per shot

### Agent loop support

- [ ] `grade preview --split` renders stills with before and after side by side
- [ ] `grade preview --candidates N` renders N variants on one sheet, with each variant's measurements in the JSON
- [ ] Feedback rule in the skill: change only the parameters the note mentions; leave the rest locked
- [ ] Colour topic added to the agent skill

### Verify and export

- [ ] New checks: legal range, added clipping, skin-tone offset, shot-to-shot difference within a scene, banding risk on gradients
- [ ] `export --to cube`: per-clip and look LUTs
- [ ] `export --to cdl`: ASC CDL values for clips whose grade is a pure `cdl`, with a list of clips that are not
- [ ] OTIO export carries grade references as metadata

---

## Tests

- Each op against hand-computed values
- LUT compiler matches direct op evaluation within a small tolerance on random colours
- A generated grey ramp stays monotonic through every built-in look
- `--balance` neutralises a clip with a known colour cast
- `--match` reduces the measured difference between two synthetic shots, and respects `--strength`
- Skin constraint: a match that would push skin hue past tolerance is clamped
- HLG-tagged test clip converts to SDR without clipping
- 10-bit intermediates are used when a grade is present and not otherwise
- Changing a shared grade re-renders exactly the clips that use it
- Exported `.cube` reproduces the render when applied in ffmpeg directly

---

## Acceptance criteria

1. `videocli grade auto NAME --balance --match` on a two-camera interview brings the cameras visibly together, and the shot-to-shot difference in `scopes` drops.
2. Log or HLG footage renders with correct contrast after `grade input`.
3. `videocli grade look NAME --from ref.jpg` moves the measured contrast, saturation and warmth toward the reference.
4. The exported `.cube` gives the same result in DaVinci Resolve.
5. In a small blind test, viewers prefer the automatically balanced version over the ungraded one more often than not. The result is published either way.

---

## Risks

| Risk | Mitigation |
|---|---|
| The agent judges colour from compressed stills and gets it wrong | Numbers first; candidates ranked by measurement; sheets second |
| Automatic grades harm skin tones | Skin constraint, strength limit, and a verify check |
| A LUT cannot express local changes | Windows and spatial ops are separate nodes |
| Wrong assumptions about untagged or log footage | Warn loudly; require an explicit transform for detected log; never guess silently |
| Banding from 8-bit sources under strong grades | 10-bit intermediates, a deband op, and a banding check |
| Colourists still beat automatic grading in preference | Position it as correction and matching by default; creative looks come from references and human notes |
