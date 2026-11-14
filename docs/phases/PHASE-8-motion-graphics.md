# Phase 8: Motion graphics (v0.5: finishing)

**Goal:** add titles, lower thirds, animated captions, diagrams and data graphics as templates driven by parameters, timed to the spoken word.

**Depends on:** phase 6 (bake framework and compositing).
**Unlocks:** the finishing milestone. With phases 6 and 7, an agent can deliver a graded, captioned, titled video.

**Design basis:** graphics are bakers behind one contract, and no single engine is required. See [RESEARCH-ARTISTIC.md](../RESEARCH-ARTISTIC.md) §4.

---

## Scope

**In**

- Template system and brand kit
- `titles` baker in the base install
- `manim` and `html` bakers as optional groups
- Animated caption styles
- Word-anchored timing
- Graphics lint and previews

**Out**

- A visual template designer
- Bundling source-available engines
- 3D graphics

---

## Commands delivered

```
videocli gfx templates [--baker NAME]
videocli gfx add NAME --template T --param k=v ... (--on CLIP_ID [--offset S] | --at-word "phrase" [--land end])
                      [--duration S] [--layer N]
videocli gfx list NAME
videocli gfx set NAME ITEM_ID --param k=v ...
videocli gfx remove NAME ITEM_ID
videocli gfx preview NAME ITEM_ID [--still T | --clip]
videocli gfx lint NAME
videocli gfx new-template DIR --baker titles|manim|html
videocli brand set [--font FILE] [--colors "#111,#fff,#e63"] [--logo FILE] [--safe-margin F]
videocli captions style NAME --preset clean|bold-words|karaoke|custom [--param k=v ...]
```

---

## Template model

A template is a folder with a manifest and the baker's source.

```json
{
  "schema": "videocli.template/v1",
  "name": "lower-third",
  "baker": "titles",
  "params": {
    "title": { "type": "string", "required": true, "max_chars": 32 },
    "subtitle": { "type": "string", "default": "" },
    "accent": { "type": "color", "default": "brand.accent" }
  },
  "duration": { "default": 4.0, "min": 2.0, "in": 0.4, "out": 0.3 },
  "placement": { "anchor": "bottom-left", "safe_area": true },
  "aspects": [ "16:9", "9:16", "1:1" ]
}
```

- Parameters are typed and validated before any render.
- `brand.*` values come from the project's brand kit, so every graphic shares fonts and colours.
- Templates are searched in the project, then the user directory, then the built-in set.

### Built-in templates (base tier)

`lower-third`, `title-card`, `end-card`, `chapter-marker`, `callout`, `counter`, `quote`, `progress-bar`, `logo-bug`.

---

## Tasks

### Template system

- [ ] Manifest model, parameter validation, brand kit resolution
- [ ] Template discovery and `gfx templates` listing with parameters and a thumbnail path
- [ ] `gfx new-template` scaffolds a working example per baker
- [ ] Each graphics item in the EDL stores template, parameters, anchor, offset, duration and layer

### `titles` baker (base)

- [ ] Text layout with Pillow: wrapping, alignment, safe margins, bundled open-licence fonts
- [ ] Animation set: fade, slide, wipe, scale, typewriter, with easing curves (never linear by default)
- [ ] Renders a transparent clip at the timeline rate and resolution
- [ ] Shapes: boxes, lines, rounded rectangles, simple icons from SVG paths
- [ ] Right-to-left and complex scripts, including Devanagari, through proper text shaping

### Animated captions (base)

- [ ] Styles written as ASS subtitles and burned with libass in `encode`, last in the chain
- [ ] `clean`: sentence cues. `bold-words`: two or three words per cue, upper case. `karaoke`: the current word highlighted using word timestamps
- [ ] Cue rules: break on punctuation and pauses; never show a cue for less than a minimum time; safe area per aspect ratio
- [ ] Emphasised words from phase 5 can take an accent colour or scale

### `manim` baker (`manim` group)

- [ ] A template is a Manim scene class that takes parameters; the baker renders it with transparency at the requested size, rate and duration
- [ ] Parameter injection without string-building code
- [ ] Render in an isolated working directory; capture logs; map failures to `BAKE_FAILED` with the scene's error
- [ ] Example templates: equation reveal, graph plot, labelled diagram, bar chart race
- [ ] Optional word sync: pass word timestamps so animation steps land on spoken words

### `html` baker (`html` group)

- [ ] A template is an HTML composition; the baker renders it to a transparent clip with HyperFrames
- [ ] Requires Node.js 22 or newer; `doctor` reports its presence and version
- [ ] Parameters passed as JSON; fonts and assets resolved from the template folder and brand kit
- [ ] Deterministic frame capture; lint for off-screen and overflowing text where the engine reports it
- [ ] Example templates: kinetic typography, UI mock-up motion, data card, animated map pin

### `generic` baker

- [ ] Documented recipe for attaching any other engine through a command that meets the bake contract

### Timing (`gfx add --at-word`)

- [ ] Find the phrase in the transcript and map it to output time through the EDL
- [ ] `--land end` back-times the item so its reveal finishes on the spoken word
- [ ] Minimum on-screen time from text length and a reading-speed setting
- [ ] Hold the final state for a minimum time before the item leaves
- [ ] Items re-time automatically when the spine changes, through their anchors

### Lint (`gfx lint`)

- [ ] Text too long for its box; text outside the safe area
- [ ] On-screen time shorter than reading time
- [ ] Two new items revealed at the same moment
- [ ] Graphic over a face, using phase-5 face boxes when present
- [ ] Graphic overlapping burned captions
- [ ] Item extends past its anchor clip or the end of the timeline
- [ ] Contrast between text and the footage beneath it, sampled from frames

### Preview and agent support

- [ ] `gfx preview --still` composites one frame over the real footage
- [ ] `gfx preview --clip` renders only that item's chunk
- [ ] Graphics topic added to the agent skill: template list, timing rules, the lint rules as guidance

---

## Tests

- Template manifests validate; a missing required parameter is rejected before baking
- `titles` output has the requested duration, size, rate and real transparency
- Easing: positions at sampled frames match the curve
- Devanagari and right-to-left text shape and wrap correctly on a reference image
- Karaoke captions highlight each word within one frame of its timestamp
- `--at-word --land end`: the reveal finishes within one frame of the word's end
- An anchored graphic moves when its clip moves, and lint flags it when the clip is removed
- Lint rules each have a failing and a passing fixture
- `manim` and `html` bakers return `FEATURE_NOT_INSTALLED` cleanly when absent
- Bake cache: changing one parameter re-bakes one item

---

## Acceptance criteria

1. With only the base install, an agent adds a lower third, a title card and karaoke captions to an interview, and `gfx lint` and `verify` pass.
2. With the `manim` group, a diagram animation steps in time with the narration.
3. With the `html` group, a kinetic-typography title renders with transparency and composites cleanly.
4. Changing the brand kit and re-rendering updates every graphic.
5. **v0.5 release:** phases 6, 7 and 8 together deliver a graded, mixed, captioned, titled video from one EDL, and it exports to an editor.

---

## Risks

| Risk | Mitigation |
|---|---|
| Three engines triple the maintenance | One contract; base baker covers the common cases; heavy bakers are optional and thin |
| Agent-written graphics look generic | Brand kit, a small set of well-designed templates, and lint on timing and readability |
| Node.js and Manim complicate install | Optional groups; `doctor` checks; base install needs neither |
| Text rendering in non-Latin scripts | Proper shaping from the start; reference-image tests |
| Graphics hide faces or captions | Lint with face boxes and caption regions |
| Engine licences change | Only open-licence engines are bundled; licence recorded by `doctor` |
