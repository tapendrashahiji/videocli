# Phase 2: EDL and render

**Goal:** turn facts into an edit decision list, let the agent adjust it, and render it accurately.

**Depends on:** phases 0 and 1.
**Unlocks:** the first real edited video. After this phase, silence and filler removal on a lecture works end to end.

**Changed in spec v0.2:** the EDL is multi-track from this first version, and render is built as a graph whose cut-and-join route is the fast path. Only the spine is renderable in this phase, but nothing here needs replacing later.

---

## Scope

**In**

- Full `videocli.edl/v1` schema (SPEC §8.5), load, save, validation
- Rule planners: ranges, silence, fillers, snap, smooth
- EDL operations and lint, for the spine
- Render graph core with these nodes: `segment`, `picture`, `lane`, `mix`, `encode`
- Previews
- Captions re-timed to the edit

**Out**

- Rendering `overlay`, `graphics`, `audio` tracks and `transitions` (phase 6)
- Grades (phase 7) and graphics (phase 8)
- Automated output checks (phase 3)

An EDL that uses a part not yet renderable validates, and `render` returns `FEATURE_NOT_AVAILABLE` naming the phase-6-or-later part.

---

## Commands delivered

```
videocli plan FILE [--cut-silence min=S,pad=S] [--cut-fillers [words=a,b]]
                   [--cut-range A-B]... [--keep-only A-B]...
                   [--min-clip S] [--min-cut S] [--no-snap] -o NAME

videocli edl show NAME [--clips] [--limit N]
videocli edl cut NAME --from T --to T [--reason TEXT]
videocli edl keep NAME --from T --to T [--reason TEXT]
videocli edl trim NAME CLIP_ID [--start T] [--end T]
videocli edl split NAME CLIP_ID --at T
videocli edl move NAME CLIP_ID --before CLIP_ID
videocli edl lock NAME ITEM_ID
videocli edl restore NAME --from T --to T
videocli edl lint NAME
videocli edl diff A B
videocli edl copy A B

videocli render NAME -o OUT [--preset NAME] [--preview] [--around-cuts S]
                     [--burn-captions] [--no-normalize] [--jobs N] [--estimate]
videocli captions NAME -o OUT.srt|OUT.vtt [--max-chars N] [--max-lines N]
```

Times in `edl cut`, `keep` and `restore` are **source times**, because those are what the agent reads in the transcript.

### `edl show` data

```json
{
  "name": "cut1",
  "tracks": { "main": 186, "overlay": 0, "graphics": 0, "audio": 0 },
  "source_duration": 3612.48,
  "output_duration": 2901.30,
  "removed_seconds": 711.18,
  "removed_percent": 19.7,
  "shortest_clip": 0.42,
  "longest_cut": { "from": 1810.2, "to": 1954.0, "reason": "off-topic tangent" },
  "by_reason": { "silence": 588.4, "filler": 31.2, "manual": 91.6 }
}
```

### `edl lint` data

```json
{
  "passed": false,
  "issues": [
    { "rule": "clipped_word", "severity": "error", "clip": "c0042", "at": 512.33, "word": "algorithm", "fix": "videocli edl trim cut1 c0042 --end 512.71" },
    { "rule": "tiny_clip", "severity": "warn", "clip": "c0077", "duration": 0.18 }
  ]
}
```

Each issue carries a ready-to-run `fix` command where one exists.

---

## Tasks

### `edl/model.py` and `edl/io.py`

- [ ] Pydantic models for the whole of EDL v1: sources, all four tracks, transitions, grades, captions, mix, markers, output, history
- [ ] `rate` and `resolution` on the timeline; picture times snap to timeline frames on save
- [ ] Validators: `start < end`, inside source duration, known source id, unique item ids, anchors point at existing spine clips
- [ ] Atomic save; stable key order and formatting, so diffs stay readable
- [ ] `history` entry appended by every operation
- [ ] `plan` infers `rate` and `resolution` from the first source

### `edl/planners/`

- [ ] Shared keep-list type built on the range helpers from phase 0
- [ ] `ranges.py`: `--cut-range`, `--keep-only`
- [ ] `silence.py`: remove silences ≥ `min`, shrink each cut by `pad` on both sides
- [ ] `fillers.py`: per-language default word lists, user override, word-timestamp cuts with a small pad
- [ ] `snap.py`: move a boundary that falls inside a word to the nearest word edge or silence midpoint
- [ ] `smooth.py`: merge cuts shorter than `min_cut`, drop clips shorter than `min_clip`
- [ ] Fixed rule order per SPEC §10.4; locked clips pass through
- [ ] A `reason` on every clip and a derived reason on every cut

### `edl/ops.py`

- [ ] `cut`, `keep`, `restore` by source-time range, splitting clips as needed
- [ ] `trim`, `split`, `move`, `lock` by clip id
- [ ] Clip ids stay stable where possible; new ids for split results
- [ ] **Anchor maintenance:** when a spine clip is split, trimmed, moved or removed, items anchored to it are re-anchored or reported. The logic lands now, so phase 6 only adds the commands that create such items
- [ ] Each op returns a before and after summary

### `edl/lint.py`

- [ ] `clipped_word`, `tiny_clip`, `tiny_cut`, `overlap`, `out_of_bounds`, `source_changed`
- [ ] `orphan_anchor`: an item whose anchor clip no longer exists
- [ ] `lint` exits 5 when any issue has severity `error`

### `render/graph.py`

- [ ] Node base type: `kind`, `params`, `inputs`, `key`, `output`
- [ ] Compiler from EDL to nodes for spine-only timelines: one `segment` and one `lane` per clip, then `picture`, `mix`, `encode`
- [ ] Key = hash of kind, parameters, input keys and tool versions; outputs stored in the phase-0 store
- [ ] Scheduler: topological order, parallel up to `--jobs`, skips finished nodes, resumes after interruption
- [ ] `--dry-run` prints the node list; `--estimate` returns expected time and disk use
- [ ] Refuse with `FEATURE_NOT_AVAILABLE` when the EDL needs a node kind this phase lacks

### `render/nodes/segment.py`

- [ ] One ffmpeg call per clip: `-ss` before `-i`, `-t`, video encode, no audio
- [ ] Conform to the timeline rate and resolution; constant frame rate; keyframe at the start
- [ ] Speed changes with `setpts`
- [ ] 8-bit intermediate by default; the pixel format is a parameter, so phase 7 can switch to 10-bit

### `render/nodes/lane.py` and `mix.py`

- [ ] `lane`: extract each clip's audio as PCM with fade in and out of `fade_ms`; speed with chained `atempo`
- [ ] Alternate neighbouring clips between lanes A and B
- [ ] Sources without audio produce silence of the right length
- [ ] `mix`: place lane clips at their output times, sum, then two-pass loudness normalization to `target_lufs` with a true-peak ceiling
- [ ] Output: one PCM sound master the exact length of the picture

### `render/nodes/picture.py` and `encode.py`

- [ ] `picture`: concat demuxer over segments with stream copy
- [ ] `encode`: join picture and sound; encode audio once; burn captions last if requested; `-movflags +faststart` for MP4
- [ ] When no burn-in or delivery re-encode is needed, picture is stream-copied into the output
- [ ] Temporary output, renamed on success; refuse to overwrite a source

### Presets and previews

- [ ] `source`, `1080p`, `720p`, `preview` (480p, fast preset)
- [ ] `--preview`: the same graph at the preview preset
- [ ] `--around-cuts S`: only S seconds on each side of each join, as one reel, with a small burned-in clip label

### `render/captions.py`

- [ ] Map each transcript word through the EDL to output time; drop words in cut ranges
- [ ] Cue grouping by characters, lines, duration and sentence punctuation
- [ ] SRT and VTT writers
- [ ] `--burn-captions` through the ffmpeg `subtitles` filter in `encode`

---

## Tests

- Planner unit tests on hand-built silence and word lists, including pad larger than half a silence
- Snap never moves a boundary into a locked clip
- Each EDL op: before and after clip lists, including splits at clip edges
- Anchor maintenance: an anchored item follows its clip through split, trim and move, and becomes an `orphan_anchor` lint issue on removal
- A full multi-track EDL (as in SPEC §8.5) validates and round-trips byte-identically
- Lint rules each have a failing and a passing fixture
- Render of a frame-counter source: output frames at each join match the expected source frames
- Picture and sound masters have equal duration within one frame
- Output duration equals the EDL total within one frame per clip
- Changing one clip re-runs exactly one `segment` and one `lane`
- An interrupted render resumes and produces a file identical to an uninterrupted one
- Captions: words from cut ranges never appear; cue times stay inside the output duration
- 300-clip EDL renders without drift between picture and sound

---

## Acceptance criteria

1. `videocli plan lecture.mp4 --cut-silence min=1.2,pad=0.15 --cut-fillers -o cut1` writes a valid EDL in under one second.
2. `videocli edl lint cut1` passes on the default plan for a clean recording.
3. `videocli render cut1 -o out.mp4` produces a file whose duration matches `edl show` within tolerance.
4. Listening to twenty random joins finds no audible clicks and no clipped words.
5. `videocli render cut1 --around-cuts 2 -o review.mp4` produces a reel short enough to review quickly.
6. `videocli captions cut1 -o out.srt` lines up with speech in the rendered file.
7. Killing a render halfway and re-running the same command finishes without redoing completed nodes.

---

## Risks

| Risk | Mitigation |
|---|---|
| Picture and sound drift across many joins | Sound is built on one continuous timeline from PCM; picture is constant frame rate; a 300-clip drift test |
| Many small ffmpeg processes are slow | Parallel workers and the node cache; measure before optimising |
| The graph is over-built for a lecture cut | The spine-only compiler is small, and it removes a rewrite in phase 6 |
| Filler removal sounds choppy | Conservative defaults, pad around fillers, opt-in |
| Agent edits JSON by hand and breaks it | Full validation on load with a precise `EDL_INVALID` message and location |
