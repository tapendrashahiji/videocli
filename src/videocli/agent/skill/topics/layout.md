# videocli topic: layout (what is where on screen)

Load with `videocli agent-guide --topic layout`. Place graphics, captions and crops from data,
then prove the result, without looking at a screenshot for every decision.

## Data

- `videocli analyze FILE --only layout` (needs `videocli setup --with perception`): per stable
  window (the picture holds still; a talking webcam bubble does not split windows) it records
  text lines and blocks (OCR), faces, objects (Florence-2 backend only) and free rectangles.
  Boxes are `[x, y, w, h]` fractions of the source frame, times are source seconds.
- Read it: `videocli layout FILE --from 80 --to 110` (blocks; add `--lines` for every line),
  `--at 92` (one window), `--find "Remove from Chrome"` (where and when a text is on screen).
- Script: `config set layout.ocr_lang devanagari` for Hindi/Nepali screen text (default `en`).
- Objects and UI without text: `videocli setup --with objects`, then
  `config set layout.backend florence` and `analyze FILE --only layout --force`.

## Placement uses it automatically

- `gfx add … --place auto` (the default) moves a title graphic off on-screen text, faces and
  objects during its time; the output's `placement` says what it avoided. `--place keep` keeps
  the template position; `gfx set NAME ID --place auto` re-places later. Manual: `--param box_x=0.6 --param box_y=0.1`.
- Captions with `position=auto` (default) go to the top while the bottom band would cover
  screen text or a face. On screens full of text (settings pages, file browsers) neither band
  is clear: `captions-style NAME --param box=1` puts a semi-transparent bar behind the caption so
  it stays readable; `verify` then accepts boxed captions over text. Small buttons ("Go to Top")
  never count as covered.
- `edl reframe NAME --aspect 9:16 --mode stack --face auto` takes the face panel from the data.

## Validate

1. Before rendering: `videocli gfx lint NAME` adds `covers_screen_text`, `covers_face`,
   `covers_object`, `crop_cuts_text` (with a `--clip … --x` fix) and `face_panel_misses_face`.
   Run each issue's `fix`.
2. After rendering: `videocli verify OUT --edl NAME` includes the `layout` check: it reads the
   render with OCR. **fail** = a graphic's text is not readable in the output; **warn** = a
   graphic or caption sits on screen text or a face. Each item has a `fix`.
3. One `videocli sheet --cuts NAME` (or `gfx preview`) at the end for taste; the data cannot
   judge beauty, and OCR can miss stylised text (a miss is reported, never passed silently).
