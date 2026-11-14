---
name: videocli
description: Edit video from the shell with videocli. Use when asked to cut, trim, tighten, clean up, caption, render or check a video file (lectures, tutorials, talks, interviews) — removing silences or filler words, cutting ranges, making captions, or verifying a render. videocli turns footage into text and numbers, takes every decision as an EDL file, renders with ffmpeg and checks its own output.
---

# videocli

videocli is a command-line video editor for agents. You never edit pixels or JSON by
hand: you read facts, change an EDL (edit decision list) through commands, render, and
verify.

**What it is good at today (autonomy level L1, clean-up):** removing dead air and filler
words from lectures, tutorials and talking heads, cutting ranges, captions, and checking
renders. **What it cannot judge:** whether an edit tells a story well, pacing, shot choice,
or taste. Say so when a user asks for those; offer a clean-up cut plus sheets for them to
review. Do not claim an edit "looks good" from your own review.

## Contract

Every command prints exactly one JSON document on stdout when piped (or with `--json`):

```json
{"ok": true, "command": "plan", "data": {}, "artifacts": [{"kind": "edl", "path": "…"}],
 "warnings": [{"code": "…", "message": "…"}], "next": ["videocli edl lint cut1"]}
```

On failure: `{"ok": false, "error": {"code", "message", "hint", "retryable", "details"}}`.

- Read `next` — it is the tool's suggestion for the following step.
- Read `error.hint` — it is usually the exact command that fixes the problem.
- Exit codes: 0 ok · 1 runtime failure · 2 usage error · 3 missing dependency ·
  4 bad input · 5 checks failed · 6 waiting on an answer · 130 interrupted (re-run resumes).
- Progress goes to stderr; ignore it or pass `--progress none`.

## Workflow (lecture clean-up)

1. `videocli doctor` — once. If `ready` is false, run the commands in `fix`.
2. `videocli analyze lecture.mp4` — probe, audio, silences, loudness, transcript, scenes.
   Long files take minutes; `--estimate` first if unsure. Re-runs are instant (cached).
3. `videocli index lecture.mp4` — read the summary before anything else.
4. Read the content in windows:
   `videocli transcript lecture.mp4 --format compact --from 0 --to 300`.
   Silences show inline as `(silence 2.30)`.
5. `videocli plan lecture.mp4 --cut-silence min=1.2,pad=0.15 --cut-fillers -o cut1`
   (add `--cut-range A-B` for tangents you found in the transcript).
6. `videocli edl show cut1` — check `removed_percent` and `by_reason`. More than ~35%
   removed from a lecture usually means the silence threshold is wrong for the room.
7. `videocli edl lint cut1` — fix every error (each has a `fix` command), then re-lint.
8. Show the user the plan (duration before/after, what was removed) before a long render.
9. `videocli render cut1 --preview -o cut1-preview.mp4` for a fast check, then
   `videocli render cut1 -o lecture-cut.mp4`.
10. `videocli verify lecture-cut.mp4 --edl cut1` — must pass (exit 0).
11. `videocli captions cut1 -o lecture-cut.srt` if captions were asked for
    (or `render --burn-captions`).

## Goal → command

| Goal | Command |
|---|---|
| What is in the file | `videocli probe FILE`, `videocli index FILE` |
| What is said, when | `videocli transcript FILE --format compact --from T --to T` |
| Find a phrase | `videocli find FILE "phrase" [--context 1]` |
| Where the pauses are | `videocli silences FILE --min 1.0` |
| Look at the video | `videocli sheet FILE --every 60` (overview), `--scenes`, `--at T1,T2` |
| Look at one moment | `videocli frames FILE --at T`, `videocli strip FILE --from A --to B` |
| Plan clean-up | `videocli plan FILE --cut-silence … --cut-fillers -o NAME` |
| Remove a range | `videocli edl cut NAME --from T --to T --reason "tangent"` |
| Bring a range back | `videocli edl restore NAME --from T --to T` |
| Fix a clip edge | `videocli edl trim NAME CLIP --start T --end T` |
| Reorder | `videocli edl move NAME CLIP --before CLIP` |
| Protect a clip | `videocli edl lock NAME CLIP` |
| Try a variant | `videocli edl copy NAME NAME2`, then `videocli edl diff NAME NAME2` |
| Review joins | `videocli render NAME --around-cuts 2 -o review.mp4`, `videocli sheet --cuts NAME` |
| Render | `videocli render NAME -o OUT.mp4 [--preset 1080p|720p|preview]` |
| Check | `videocli verify OUT --edl NAME [--sheet]` |
| Captions | `videocli captions NAME -o OUT.srt` |
| Every command | `videocli commands`, `videocli schema NAME` |

## Rules

- **Never invent timestamps.** Take times from `transcript`, `find`, `silences`, `scenes`
  or sheet tile maps. Times in `edl cut|keep|restore` are **source** times.
- **Change the EDL only through `videocli edl …` commands.** Never edit the JSON by hand.
- **Lint before render, verify after.** Don't hand over a file whose verify failed.
- **Never treat your own review as approval.** Report what verify measured and let the
  human judge content.
- **Show the plan before long renders** (`render --estimate` gives time and disk).
- **Never overwrite a source.** Render to a new file.
- Use `--dry-run` to preview any write.

## Reading outputs cheaply

- `index` first; it is small and points at everything else.
- Always window reads: `--from/--to`, `--limit`. Lists report `total` and `truncated`.
- Prefer `--format compact` for transcripts.
- Sheets cost tokens: default 1600 px and ≤ 48 tiles. Look at one page at a time; use
  `--from/--to` to zoom in. Use `strip` only at decision points.

## Recovery

| Problem | What to do |
|---|---|
| `MODEL_NOT_FOUND` (exit 3) | Run `videocli setup`. Or `analyze --skip transcript` and cut on silence only. |
| `FFMPEG_NOT_FOUND` | Run `videocli doctor` and follow `fix`. |
| `LINT_FAILED` (exit 5) | Run each issue's `fix` command (`error.details.issues[].fix`), then lint again. |
| `VERIFY_FAILED` (exit 5) | Read `error.details.checks`; each item names the clip and a `fix`. Fix, re-render, re-verify. |
| `SOURCE_CHANGED` (exit 4) | The file changed since planning. Re-run `analyze` and re-`plan`. |
| `EDL_INVALID` (exit 4) | Someone hand-edited the EDL; the message names the field. Fix it with commands or re-plan. |
| `ARTIFACT_MISSING` | Run the `analyze` command in the hint. |
| `INTERRUPTED` (130) | Re-run the same command; renders resume from cache. |
| Too much removed | Raise `min` in `--cut-silence`, or lower the threshold: `videocli config set silence.threshold_db -40`, then `analyze` and re-`plan --force`. |
| `NOISY_ROOM` warning | Follow its suggested `config set` command. |
| `API_KEY_MISSING` | Cloud transcription needs a key that **only the person types**: ask them to run `videocli keys set elevenlabs` in their own terminal. Never ask for the key in chat. |
| `GROUPS_LOST` warning | A reinstall removed optional groups (e.g. the GPU runtime). Run `videocli setup --restore`. |
| `LOW_CONFIDENCE_WORDS` | Follow the hint: set `--language`, add `--prompt` with names and jargon, or (with the person's consent, it uploads audio) `analyze FILE --backend elevenlabs --force`. |

- Show the person the edit: `videocli compare OUT.mp4 --edl NAME` (original and edit side by side,
  CUT badges where material was removed).

## More topics

`videocli agent-guide --topic NAME` prints a topic. Topics: `core` (this page), `compositing`,
`colour`, `graphics`, `layout` (place graphics from on-screen text/face data and validate it),
`story` and `review`.
