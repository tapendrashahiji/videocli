# videocli topic: colour

Load with `videocli agent-guide --topic colour`. A grade is a stack of numbers that compiles to
one `.cube` per clip; you never judge colour from compressed stills alone — read `scopes` first.

## Order of work

1. Input: `videocli grade input NAME --source ID --transform auto`. HLG/PQ are tone-mapped;
   detected camera log **requires** the vendor LUT (`--transform camera.cube`); untagged footage is
   assumed Rec.709 with a warning.
2. Measure: `videocli scopes NAME --shots` (per clip: luma p1/p50/p99, clipping, rgb means,
   saturation, white balance, `delta_to_previous`, and `shot_difference` overall).
3. Correct: `videocli grade auto NAME --balance --match [--reference c0001] [--strength 0.8]`.
   One correction per source (one shoot). Skin hue and highlights are protected; results are
   ordinary ops you can edit. Check `shot_difference.after < before`.
4. Look: `videocli grade look NAME --from ref.jpg|ref.mp4|look.cube [--strength 0.6]`.
5. Fine-tune: `videocli grade set NAME --clip c0004 --op 'exposure stops=0.2'`
   or a shared group: `--clip c0004 --group interview` then `--group interview --op '…'`.
6. Check: `videocli grade preview NAME --split` (before/after stills with numbers) or
   `--candidates 4` (ranked variants on one sheet). Then render and `verify`
   (adds `legal_range` and `added_clipping`).

## Ops (text form for `--op`)

`cdl slope=R,G,B offset=R,G,B power=R,G,B sat=S` · `exposure stops=S` ·
`temperature kelvin_shift=K tint=T` · `contrast amount=A pivot=P` · `saturation amount=A` ·
`rolloff threshold=T` · `lut path=FILE.cube strength=S` ·
spatial: `vignette amount=A` · `denoise amount=A` · `sharpen amount=A` · `deband` ·
`window shape=ellipse|rect x= y= w= h= feather=` (with nested ops in the EDL).

## Rules

- Change only the parameters a note mentions; leave the rest of the stack alone.
- Prefer small moves: exposure ±0.5, kelvin ±1000, sat 0.8–1.2.
- Export for a colourist: `videocli export NAME --to cube` (one LUT per clip + look) or
  `--to cdl` (pure-CDL clips; others are listed).
