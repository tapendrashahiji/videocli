# Phase 5: Deep perception

**Goal:** describe footage in the language editors use (shots, people, movement, emphasis, music structure), so later phases can make craft decisions from data instead of from stills and a transcript.

**Depends on:** phases 0 to 4.
**Unlocks:** phase 9 (story and style) and phase 10 (director loop). Also improves reframing in phase 6 and skin-tone handling in phase 7.

**Why this phase exists:** Timeline-Bench found agents weak at rhythm and shot selection, and linked it to perceiving footage through stills and transcripts. See [RESEARCH-ARTISTIC.md](../RESEARCH-ARTISTIC.md) §1 and §5.

---

## Scope

**In**

- Model providers and the work-order protocol
- Shot records with cinematographic attributes
- Motion and camera movement over time
- Faces and people (local, unnamed)
- Speech prosody and emphasis; audio events
- Speaker diarization
- Music structure: beats, downbeats, sections
- Visual embeddings and visual search
- Slide detection
- Shot logging (descriptions) through work orders
- Retake, dead-shot and chapter suggestions

**Out**

- Any decision about what to keep (phase 9)
- Aesthetic judgement as a single score that drives cuts by itself

---

## Commands delivered

```
videocli analyze PATH --only shots,motion,faces,prosody,events,music,embed,slides,diarize
videocli analyze PATH --profile artistic
videocli shots FILE [--where "size=CU and motion=static"] [--sort FIELD] [--format compact] [--limit N]
videocli people PATH [--name p1="Maya"]
videocli speakers FILE
videocli music FILE
videocli slides FILE
videocli find PATH "QUERY" --mode text|visual|both
videocli log PATH [--provider agent|local|api] [--only-missing]
videocli orders list | show ID | submit ID ANSWERS.jsonl | cancel ID
videocli suggest FILE retakes|dead-shots|chapters
```

`PATH` may be a folder, so a whole shoot can be analysed and searched together.

### `shots --format compact`

```
s0042 [0512.20-0518.96] MCU single handheld int | p1 | motion .07 | "Woman at a workbench explains the mould."
s0043 [0518.96-0521.40] CU  insert static   int | —  | motion .01 | "Hands pressing clay into a wooden form."
```

---

## Providers and work orders

Built first, because several analyzers depend on it. See SPEC §6.5 and §10.2.

- [ ] `Provider` protocol and registry: `agent`, `local`, `api`
- [ ] Order model: id, kind, items with images or audio clips, question, answer schema
- [ ] `agent` provider: writes the order, returns pending; the command exits 6 with `ORDERS_PENDING` and a `next` hint
- [ ] `orders submit` validates each answer against its schema; partial submissions are allowed
- [ ] Answers stored with provider name, model name where known, and time
- [ ] Batching: items grouped so one sheet image can serve several questions
- [ ] Cost guard: `--estimate` reports item and image counts before an order is created
- [ ] `local` provider interface with one reference model behind the `perception` group
- [ ] `api` provider: disabled unless configured; prints what would be sent under `--dry-run`

---

## Analyzers

### `shots` (base for metrics; `perception` for attributes)

- [ ] One record per shot from phase-3 scenes, per SPEC §8.3
- [ ] Metrics from sampled frames: motion, blur, brightness, black ratio
- [ ] Attributes (size, angle, type, location, subject) by zero-shot classification with an image-text model, each with a confidence
- [ ] Low-confidence attributes are left empty and can be filled by a work order
- [ ] `shots` command with a small filter language and compact output

### `motion` (base)

- [ ] Optical flow on downscaled frames; global motion estimated separately from subject motion
- [ ] Camera movement class per shot: static, pan, tilt, zoom, handheld, tracking
- [ ] Motion energy curve over time and **action peaks**, used later for cutting on action
- [ ] Stability score, used later to suggest stabilisation

### `faces` (`perception`)

- [ ] Face detection per sampled frame; position and size
- [ ] Local clustering into people `p1`, `p2`, … across a whole project
- [ ] Eyes-closed and blink detection around candidate cut points
- [ ] `people --name` attaches a user-supplied name; nothing is identified automatically
- [ ] Face boxes feed reframing (phase 6) and skin-tone checks (phase 7)

### `prosody` and `events` (base for prosody; `perception` for events)

- [ ] Per word: pitch, energy and speaking-rate deviation from the speaker's norm
- [ ] **Emphasis** score per word and per phrase
- [ ] Breath and pause map between phrases
- [ ] Audio events: laughter, applause, music present, noise bursts
- [ ] Compact transcript gains optional emphasis and event marks

### `diarize` (`diarize`)

- [ ] Speaker turns; a speaker assigned to each transcript word by overlap
- [ ] `speakers`: talk time and turn count
- [ ] Warning when overlapping speech is frequent
- [ ] Optional link between speakers and face clusters when one person is on screen while speaking

### `music` (`music`)

- [ ] Tempo, beats, downbeats, and labelled sections (intro, verse, chorus, bridge, outro)
- [ ] Energy curve per bar
- [ ] Works on a music file or on a source's audio; warns on speech-only audio
- [ ] Evaluate at least two libraries for accuracy and install weight before choosing the default

### `embed` and visual search (`perception`)

- [ ] One embedding per shot, more for long shots; vectors in a NumPy file with a JSON map
- [ ] `find --mode visual` ranks shots for a text query; `--mode both` merges with transcript hits
- [ ] Results include a sheet path so the agent can confirm
- [ ] Shot-to-shot similarity, used later for match cuts and jump-cut detection

### `slides` (base)

- [ ] Slide changes by structural difference with a hold time; optional crop region
- [ ] One record per slide with the transcript spoken over it

---

## Shot logging

`log` gives every shot a one-sentence description and a usability flag.

- [ ] Builds one small sheet per shot (first, middle, last frame) and one order per batch
- [ ] The default `agent` provider means the calling agent writes the descriptions; with many shots it should fan the order out to sub-agents
- [ ] `--only-missing` skips shots already logged
- [ ] Descriptions join the search index, so `find` can match them
- [ ] A measured comparison between `agent` and `local` answers on a sample decides the default (decisions.md X3)

---

## Suggestions

`suggest` returns candidates with evidence and never changes an EDL.

- [ ] `retakes`: adjacent transcript segments with high text similarity; recommends the last take
- [ ] `dead-shots`: black, frozen, or silent with no speech
- [ ] `chapters`: boundaries from long pauses, slide changes and scene changes, with surrounding text

---

## Tests

- Work orders: create, partial submit, invalid answer rejected with the item id, resume of the original command
- Shot metrics on synthetic clips: static, moving, blurred, dark
- Camera movement class on generated pans, zooms and shakes
- Face clustering keeps two synthetic identities apart; names never appear unless supplied
- Emphasis rises on a louder, higher, slower word in a synthetic sample
- Music analysis on a generated click track with known tempo and bar length
- Visual search mocked in unit tests; one `slow` test ranks an obvious match first
- Every heavy analyzer returns `FEATURE_NOT_INSTALLED` cleanly when its group is absent
- Provenance is stored for every attribute and description

---

## Acceptance criteria

1. `videocli analyze shoot/ --profile artistic` then `videocli shots` gives, for a real shoot, records a person agrees with on a spot-check of fifty shots.
2. `videocli log shoot/` with the `agent` provider completes through work orders with no human involved.
3. `videocli find shoot/ "hands working clay" --mode both` ranks correct shots near the top, confirmed on the returned sheet.
4. `videocli music score.wav` puts downbeats where a person taps them.
5. The base install still works with no optional group present.

---

## Risks

| Risk | Mitigation |
|---|---|
| Zero-shot attributes are wrong often enough to mislead planners | Confidence thresholds, empty-when-unsure, and work orders to fill gaps |
| Logging a large shoot costs many agent tokens | Estimates before ordering, small sheets, batching, `--only-missing` |
| Face grouping raises privacy concerns | Local only, unnamed by default, removable with one command |
| Music libraries are heavy to install | Optional group; lighter fallback that gives beats without sections |
| Different providers give different answers | Provenance on every answer; results are not mixed silently |
| Model licences vary | Licence shown by `doctor` and before download |
