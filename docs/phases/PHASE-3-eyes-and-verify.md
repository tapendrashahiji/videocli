# Phase 3: Eyes and verify

**Goal:** give the agent a cheap way to look at the video, and give the tool a way to check its own renders.

**Depends on:** phases 0 to 2.
**Unlocks:** a closed loop. The agent can plan, render, check, and fix without a human in the middle.

---

## Scope

**In**

- Shot detection
- Still frames and contact sheets
- Verify checks and report
- Cut-review sheet

**Out**

- Shot attributes, people and motion (phase 5)
- Craft metrics such as pace and coverage (`audit`, phase 9)
- Independent critique and human notes (phase 10)
- Any vision-model call; the agent looks at images itself

---

## Commands delivered

```
videocli scenes FILE [--detector adaptive|content] [--threshold N] [--min-scene S] [--limit N]
videocli frames FILE --at T1,T2,... [--width N] [--format jpg|png]
videocli frames FILE --every S [--from T] [--to T] [--limit N]
videocli sheet FILE (--every S | --scenes | --at T1,T2,... | --cuts NAME)
                    [--cols N] [--width N] [--from T] [--to T] [--max-tiles N]
videocli strip FILE --from T --to T [--width N]
videocli verify OUT --edl NAME [--checks LIST] [--skip LIST] [--sheet]
```

### Contact sheets

A contact sheet is one image holding a grid of stills, each labelled with its timestamp. It lets an agent inspect minutes of video in one image.

| Mode | Tiles | Use |
|---|---|---|
| `--every 30` | One still every 30 seconds | Overview of a long file |
| `--scenes` | The middle frame of each shot | Understand structure |
| `--at …` | Chosen times | Inspect specific moments |
| `--cuts NAME` | Last frame before and first frame after each join | Review an edit |

Defaults: 6 columns, at most 48 tiles per sheet, width 1600 px. More tiles produce several numbered sheets.

### Strips

`strip` draws a filmstrip of frames over the audio waveform for a short time window. It is the cheapest way to inspect one cut: the agent sees the picture change and any waveform spike at the same instant. Use it at decision points, not for scanning a whole file.

### `sheet` data

```json
{
  "sheets": [
    {
      "path": ".videocli/sources/a1b2c3d4e5f6/sheets/every30-001.jpg",
      "width": 1600, "height": 1210,
      "tiles": [ { "row": 0, "col": 0, "time": 0.0 }, { "row": 0, "col": 1, "time": 30.0 } ]
    }
  ]
}
```

The `tiles` list lets the agent map what it sees back to exact times.

---

## Tasks

### `analyze/scenes.py`

- [ ] Call PySceneDetect as a library on a downscaled stream
- [ ] Detectors `adaptive` (default) and `content`; parameters `threshold`, `min_scene`
- [ ] Write `scenes.json`; add `scene_count` to the index summary
- [ ] Add `scenes` to the default `analyze` set for sources that have video

### `media/frames.py`

- [ ] Accurate single-frame extraction at a time, with rotation applied
- [ ] Batch extraction that decodes once for many times
- [ ] Resize by width with aspect kept; JPEG quality setting
- [ ] Frame cache keyed by source, time and width

### Contact sheets

- [ ] Grid composer with OpenCV: tiles, margins, background
- [ ] Timestamp label drawn on each tile with OpenCV text (no font file needed)
- [ ] Pagination by `--max-tiles`
- [ ] `--cuts` mode: pairs of tiles per join, labelled with clip ids and source times
- [ ] Tile map in the JSON result
- [ ] `strip`: evenly spaced frames across a window, with the waveform drawn beneath on a shared time axis and tick labels

### `verify/checks.py`

One function per check, each returning a status and items. See SPEC §10.9.

- [ ] `duration`
- [ ] `streams`
- [ ] `av_sync`
- [ ] `black_frames`, ignoring ranges that are black in the source
- [ ] `frozen_frames`, ignoring ranges that are frozen in the source (slides are normal in lectures)
- [ ] `audio_clicks`: decode a short window around each join and measure the sample step
- [ ] `silence_gaps`
- [ ] `loudness`
- [ ] `clipped_words`
- [ ] Thresholds live in settings; each check can be skipped

### `verify/report.py`

- [ ] Build `videocli.verify/v1` per SPEC §8.9
- [ ] Map every output-time item back to its clip id and source time
- [ ] Attach a suggested `fix` command to items where one exists
- [ ] Save under `.videocli/reports/`; exit 5 when any check fails
- [ ] `--sheet` writes a cut-review sheet for the flagged joins only

---

## Tests

- Scene detection on a synthetic clip with hard cuts at known times
- Frame extraction returns the expected frame number on a frame-counter test source
- Sheet tile map matches the drawn grid; pagination boundaries are correct
- Strip time axis: a generated click at a known time appears at the right x position
- Each verify check has a fixture that passes and one built to fail:
  - an inserted black segment
  - a hard audio discontinuity at a join
  - a render with one clip missing (duration mismatch)
  - audio shorter than video
- Black and frozen ranges present in the source are not reported
- Report items map back to the right clip id

---

## Acceptance criteria

1. `videocli sheet lecture.mp4 --every 60` produces sheets on which a person can read every timestamp.
2. `videocli sheet lecture.mp4 --cuts cut1` shows before and after frames for each join.
3. `videocli verify out.mp4 --edl cut1` passes on a correct render and exits 0.
4. A deliberately damaged render fails with the right check named, exits 5, and points at the right clip.
5. An agent, using only the commands so far, can take a raw lecture to a verified cut without a human reading any JSON.

---

## Risks

| Risk | Mitigation |
|---|---|
| False alarms from normal lecture content (static slides, dark rooms) | Compare against the same range in the source before reporting |
| Click detection is noisy | Calibrate the threshold on real renders; report as `warn`, not `fail`, at first |
| Large sheets cost many tokens for the agent | Conservative default size and tile count; document the cost in the skill |
| Scene detection is slow at full resolution | Downscale before detection; cache the result |
