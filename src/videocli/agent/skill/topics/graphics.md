# videocli topic: graphics and captions

Load with `videocli agent-guide --topic graphics`. Graphics are templates plus parameters,
baked into transparent clips and composited above overlays; captions burn last.

## Add a graphic

- List: `videocli gfx templates` (lower-third, title-card, end-card, chapter-marker, callout,
  counter, quote, progress-bar, logo-bug; plus project templates).
- On a clip: `videocli gfx add NAME -t lower-third --param title="Maya Shrestha" --param subtitle=Potter --on c0002 --offset 1.0`
- On a spoken phrase: `--at-word "three hundred years"` (appears on the first word) or
  `--at-word "…" --land end` (the reveal finishes as the phrase ends). The phrase must survive the
  edit; `PHRASE_NOT_FOUND` otherwise.
- Duration defaults to the template's, never shorter than the reading time.
- Change: `videocli gfx set NAME x0001 --param title=… --duration 4`; remove: `gfx remove`.
- Look: `videocli gfx preview NAME x0001 --still 0.8` (one frame over the real footage) or `--clip`.

## Rules

- Run `videocli gfx lint NAME` before rendering: text too long, outside the safe area, shorter
  than reading time, two reveals at once, past its clip or the end, over burned captions, low
  contrast. Fix every error.
- One idea per graphic. Do not stack two reveals within 0.3 s.
- Brand once: `videocli brand set --colors "#111111,#ffffff,#e63946" --font Brand.ttf --logo logo.png`.
  Re-rendering updates every graphic.

## Captions

- `videocli captions style NAME --preset clean|bold-words|karaoke` then
  `videocli render NAME --burn-captions -o out.mp4`. Karaoke highlights each word on its timestamp.
- Vertical output moves captions up into the safe area automatically.

## Heavy engines (optional groups)

- `--baker manim` templates (`videocli setup --with manim`): equations and diagrams; a scene reads
  its parameters from `VIDEOCLI_PARAMS`.
- `--baker html` templates (`videocli setup --with html`, Node.js 22): HyperFrames compositions.
- Scaffold: `videocli gfx new-template DIR --baker titles|manim|html|generic`, then move it under
  `.videocli/project/templates/`.
