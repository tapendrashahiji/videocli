# videocli topic: compositing and sound

Load with `videocli agent-guide --topic compositing`.

## Layers

- `videocli edl overlay NAME --source broll.mp4 --from 40 --to 46.5 --on c0007 --offset 2.0`
  B-roll that **covers** the picture from 2.0 s into clip c0007 (output time). The interview's
  sound continues; add `--sound` to hear the B-roll instead of muting it.
- `--mode pip --pos tr --scale 0.3 [--opacity 0.8]` for picture-in-picture.
- Overlays are **anchored** to their clip: cutting, trimming or moving the spine moves them too.
  `edl lint` reports `orphan_anchor` if the clip disappears and `overlapping_overlays` if two
  collide.

## Transitions and timing

- `videocli edl transition NAME c0003 --type dissolve|fade|dip --duration 0.5` — after c0003.
  It **shortens the edit by the overlap**. `--type cut` removes it. Use them sparingly: hard cuts
  are the default grammar; dissolves imply time passing.
- `videocli edl split-edit NAME c0004 --lag 0.6` (L-cut: the speaker keeps talking over the next
  shot) or `--lead 0.4` (J-cut: the next sound starts early).
- `videocli edl speed NAME c0005 --rate 1.5`, `--ramp 0:1,0.5:3,1:1`, or `--freeze 1.0`.
  Ramped clips are muted.

## Sound

- `videocli edl audio NAME --source music.wav --gain -14 --duck -10 --fade 1.5 --fit`
  Music under the whole edit, lowered 10 dB while people speak (needs `analyze` on the spine
  sources for speech ranges; otherwise the dialogue level is used).
- `--role sfx --at 12.4` for an effect at an output time; `--role tone` for ambience.
- `videocli edl sound NAME --room-tone auto --highpass 80 [--denoise 0.4] [--level]`
- `videocli render NAME -o out.mp4 --stems` also writes dialogue, music and effects stems.

## Reframe, stabilise, export

- `videocli edl reframe NAME --aspect 9:16` (vertical), `1:1`, `4:5`; `--mode subject` uses face
  positions when phase-5 face data exists, else a centre crop with a warning.
  `--clip c0002 --x 0.3` moves one shot's crop.
- `videocli edl stabilize NAME c0006 --strength 0.5` (baked once, cached).
- `videocli export NAME --to otio|fcpxml|edl [-o PATH] [--relative-to DIR]` hands the timeline
  to Resolve, Premiere or Final Cut; `round_check` must be `pass`. CMX 3600 carries the spine only.

## Checking

`render --dry-run` lists chunks (`segment` = fast path, `composite` = layered), and
`render NAME --explain ITEM_ID` shows which chunks an item touches. Re-rendering after one
overlay change only redoes those chunks. Always `verify` afterwards.
