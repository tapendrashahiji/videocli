# Command reference

Generated from `videocli commands` by `scripts/gen_docs.py`. Do not edit by hand.

## Global options

| Option | Type | Help |
|---|---|---|
| `--json` | flag | Print one JSON envelope on stdout (default when stdout is not a terminal). |
| `--workspace` | text | Workspace folder (default: nearest .videocli). |
| `--progress` | text | Progress on stderr: auto, bar, json or none. |
| `--dry-run` | flag | Show what would happen without writing or rendering. |
| `--estimate` | flag | Return expected time and disk use without doing the work. |
| `--force` | flag | Redo cached work and allow overwriting outputs. |
| `--quiet` | flag | No human output on success. |
| `--verbose` | flag | More detail on stderr. |
| `--jobs` | integer | Parallel workers. |
| `--provider` | text | Model provider: agent, local or api. |
| `--version` | flag | Print the version. |

## `videocli agent-guide`

Print the packaged agent skill (or one topic of it)

Exit codes: 0, 2

| Name | Type | Required | Help |
|---|---|---|---|
| `--topic` | text |  | core, or a later topic (see data.topics). |

## `videocli analyze`

Run analyzers (probe, audio, silence, loudness, transcript, scenes) and build the index

Exit codes: 0, 1, 2, 3, 4 · supports writes, `--dry-run`, `--estimate`

| Name | Type | Required | Help |
|---|---|---|---|
| `PATH` | text | yes | Media file, registered source id, or folder. |
| `--only LIST` | text |  | Run only these analyzers (comma-separated). |
| `--skip LIST` | text |  | Skip these analyzers. |
| `--model` | text |  | Speech model (default: transcript.model setting). |
| `--language CODE` | text |  | Language code; default auto. |
| `--audio-stream N` | integer |  | Audio stream index. |
| `--prompt` | text |  | Initial prompt for the speech model (names, jargon). |

## `videocli cache clear`

Remove all caches (render nodes, bakes, LUTs, analysis artifacts, logs); keeps edits and project files

Exit codes: 0 · supports writes, `--dry-run`

## `videocli cache prune`

Remove cache entries unused for a while, then least-recently-used over the size limit

Exit codes: 0, 2 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `--older-than` | text |  | e.g. 30d, 12h |

## `videocli cache status`

Disk use of each cache area

Exit codes: 0

## `videocli captions`

Write SRT or VTT captions re-timed to the edit

Exit codes: 0, 2, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes | EDL name. |
| `-o OUT.srt|OUT.vtt` | text | yes |  |
| `--max-chars` | integer |  |  |
| `--max-lines` | integer |  |  |

## `videocli commands`

Every command with its arguments, options, exit codes, and the error-code registry

Exit codes: 0, 2

| Name | Type | Required | Help |
|---|---|---|---|
| `--name CMD` | text |  | Only this command, e.g. 'edl cut'. |

## `videocli config set`

Set a setting in the project (or user) config file

Exit codes: 0, 2 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `KEY` | text | yes | Dotted key, e.g. silence.threshold_db |
| `VALUE` | text | yes | New value |
| `--user` | flag |  | Write the user file instead of the project file. |

## `videocli config show`

Print effective settings and where each value came from

Exit codes: 0, 2

| Name | Type | Required | Help |
|---|---|---|---|
| `KEY` | text |  | Show only this key. |

## `videocli doctor`

Report installed tools, groups, models and the commands that fix what is missing

Exit codes: 0

## `videocli edl audio`

Add music, an effect or room tone at an output time, with gain, ducking under speech, fades and --fit

Exit codes: 0, 2, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes |  |
| `--source FILE` | text | yes |  |
| `--role` | text |  | music, sfx or tone. |
| `--at OUT_T` | time |  | Output time where it starts. |
| `--from T` | time |  | Source in-point. |
| `--to T` | time |  | Source out-point. |
| `--gain DB` | number |  |  |
| `--duck DB` | number |  | Lower by this much under speech (e.g. -10). |
| `--fade S` | number |  |  |
| `--fit` | flag |  | Loop or trim to the end of the edit. |

## `videocli edl copy`

Copy an EDL to a new name (a revision to experiment on)

Exit codes: 0, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `A` | text | yes |  |
| `B` | text | yes |  |

## `videocli edl cut`

Remove a source-time range from the spine

Exit codes: 0, 2, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes | EDL name. |
| `--from T` | time | yes | Source time. |
| `--to T` | time | yes | Source time. |
| `--reason TEXT` | text |  |  |
| `--source ID` | text |  | Needed when the EDL has several sources. |

## `videocli edl diff`

Compare two EDLs clip by clip

Exit codes: 0, 4

| Name | Type | Required | Help |
|---|---|---|---|
| `A` | text | yes |  |
| `B` | text | yes |  |

## `videocli edl keep`

Make sure a source-time range is in the spine

Exit codes: 0, 2, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes | EDL name. |
| `--from T` | time | yes | Source time. |
| `--to T` | time | yes | Source time. |
| `--reason TEXT` | text |  |  |
| `--source ID` | text |  | Needed when the EDL has several sources. |

## `videocli edl lint`

Check an EDL: clipped words, tiny clips and cuts, overlaps, bounds, changed sources, orphan anchors

Exit codes: 0, 4, 5

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes |  |

## `videocli edl lock`

Protect an item from automatic planners (--unlock to release)

Exit codes: 0, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes |  |
| `ITEM_ID ITEM_ID` | text | yes |  |
| `--unlock` | flag |  |  |

## `videocli edl move`

Move a clip before or after another (or to start/end)

Exit codes: 0, 2, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes |  |
| `CLIP_ID CLIP_ID` | text | yes |  |
| `--before CLIP_ID|start` | text |  |  |
| `--after CLIP_ID|end` | text |  |  |

## `videocli edl overlay`

Put B-roll over a spine clip (cover) or picture-in-picture (pip), anchored so it follows the clip

Exit codes: 0, 2, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes |  |
| `--source FILE` | text | yes | Overlay media. |
| `--from T` | time | yes | Overlay source in-point. |
| `--to T` | time | yes | Overlay source out-point. |
| `--on CLIP_ID` | text | yes | Spine clip it is anchored to. |
| `--offset S` | number |  | Seconds after the clip's first frame (output time). |
| `--mode` | text |  | cover or pip. |
| `--pos` | text |  | pip corner tl|tr|bl|br or X,Y (0-1 or pixels). |
| `--scale` | number |  | pip width as a share of the frame (default 0.33). |
| `--sound` | flag |  | Keep the overlay's own sound (default: muted). |
| `--mute` | flag |  |  |
| `--opacity` | number |  |  |

## `videocli edl reframe`

Vertical, square or 4:5 output with a crop per shot (center, or subject from face data)

Exit codes: 0, 2, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes |  |
| `--aspect` | text |  | 9:16, 1:1, 4:5, 4:3 or 16:9. |
| `--mode` | text |  | center or subject. |
| `--clip CLIP_ID` | text |  | Set one clip's crop centre with --x. |
| `--x` | number |  | Crop centre, 0 (left) to 1 (right). |

## `videocli edl restore`

Bring back a previously removed source-time range

Exit codes: 0, 2, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes | EDL name. |
| `--from T` | time | yes | Source time. |
| `--to T` | time | yes | Source time. |
| `--reason TEXT` | text |  |  |
| `--source ID` | text |  | Needed when the EDL has several sources. |

## `videocli edl show`

Summary of an EDL; --clips lists spine clips with source and output times

Exit codes: 0, 4

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes |  |
| `--clips` | flag |  |  |
| `--limit` | integer |  |  |

## `videocli edl sound`

Dialogue clean-up (denoise, levelling, compression, high-pass) and room tone under gaps

Exit codes: 0, 2, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes |  |
| `--denoise F` | number |  |  |
| `--level` | flag |  | Even out levels across speakers. |
| `--compress` | flag |  | Gentle compression. |
| `--highpass HZ` | number |  | High-pass cutoff (0 removes). |
| `--room-tone auto|FILE|none` | text |  |  |

## `videocli edl speed`

Constant speed (--rate), a ramp (--ramp POS:RATE,…) or a freeze of the last frame (--freeze S)

Exit codes: 0, 2, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes |  |
| `CLIP_ID CLIP_ID` | text | yes |  |
| `--rate` | number |  |  |
| `--ramp 0:1.0,0.6:2.5,1:1.0` | text |  |  |
| `--freeze S` | number |  |  |

## `videocli edl split`

Split a clip at a source time; the second part gets a new id

Exit codes: 0, 2, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes |  |
| `CLIP_ID CLIP_ID` | text | yes |  |
| `--at T` | time | yes | Source time inside the clip. |

## `videocli edl split-edit`

J/L cut: sound starts before (--lead) or runs past (--lag) the picture cut

Exit codes: 0, 2, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes |  |
| `CLIP_ID CLIP_ID` | text | yes |  |
| `--lead S` | number |  |  |
| `--lag S` | number |  |  |

## `videocli edl stabilize`

Stabilise a clip (two-pass, baked and cached); --strength 0 removes it

Exit codes: 0, 2, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes |  |
| `CLIP_ID CLIP_ID` | text | yes |  |
| `--strength` | number |  |  |

## `videocli edl transition`

Transition after a spine clip: dissolve, fade (through black), dip (through a colour) or cut; shortens the edit by the overlap

Exit codes: 0, 2, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes |  |
| `CLIP_ID CLIP_ID` | text | yes |  |
| `--type` | text |  | dissolve, fade, dip or cut (removes). |
| `--duration S` | number |  |  |
| `--color` | text |  | dip colour: black or white. |

## `videocli edl trim`

Set a clip's source start and/or end

Exit codes: 0, 2, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes |  |
| `CLIP_ID CLIP_ID` | text | yes |  |
| `--start T` | time |  | New source start. |
| `--end T` | time |  | New source end. |

## `videocli export`

Hand the timeline to an editor: OpenTimelineIO, Final Cut XML or CMX 3600 (with a round-check)

Exit codes: 0, 1, 2, 3, 4 · supports writes

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes |  |
| `--to` | text | yes | otio, fcpxml or edl (CMX 3600). |
| `-o PATH` | text |  |  |
| `--relative-to DIR` | text |  | Write media paths relative to DIR. |

## `videocli find`

Find a phrase (or --regex) in the transcript with word-accurate times

Exit codes: 0, 2, 4

| Name | Type | Required | Help |
|---|---|---|---|
| `FILE` | text | yes | Media file or source id. |
| `QUERY` | text | yes | Phrase (case-insensitive) or regular expression with --regex. |
| `--regex` | flag |  | Treat QUERY as a regular expression over segment text. |
| `--context N` | integer |  | Include N neighbouring segments. |
| `--from T` | time |  |  |
| `--to T` | time |  |  |
| `--limit` | integer |  |  |
| `--mode` | text |  | text (visual search arrives with phase 5). |

## `videocli frames`

Still frames at given times (--at) or at an interval (--every)

Exit codes: 0, 1, 2, 4 · supports writes

| Name | Type | Required | Help |
|---|---|---|---|
| `FILE` | text | yes | Media file or source id. |
| `--at T1,T2,...` | time |  | Comma-separated times. |
| `--every S` | number |  | One frame every S seconds. |
| `--from T` | time |  |  |
| `--to T` | time |  |  |
| `--width` | integer |  |  |
| `--format` | text |  | jpg or png. |
| `--limit` | integer |  |  |

## `videocli index`

Summary and artifact pointers for a source (read this first)

Exit codes: 0, 4

| Name | Type | Required | Help |
|---|---|---|---|
| `FILE` | text | yes | Media file or source id. |

## `videocli jobs`

List unfinished long-running jobs (interrupted renders resume when re-run)

Exit codes: 0

## `videocli plan`

Write a new EDL from rule planners (silence, fillers, ranges); never renders

Exit codes: 0, 2, 4 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `FILE` | text | yes | Media file or source id. |
| `-o NAME` | text | yes | Name of the EDL to write. |
| `--cut-silence min=S,pad=S` | text |  | Remove silences ≥ min (default 1.0 s), keeping pad (0.15 s) each side. |
| `--cut-fillers [words=a,b]` | text |  | Remove filler words (default list for the language, or words=um,uh). |
| `--cut-range A-B` | range |  | Remove a source range (repeatable). |
| `--keep-only A-B` | range |  | Keep only these ranges (repeatable). |
| `--min-clip S` | number |  | Drop clips shorter than this. |
| `--min-cut S` | number |  | Ignore cuts shorter than this. |
| `--no-snap` | flag |  | Do not move boundaries to word edges. |

## `videocli probe`

Read container, streams, duration and colour metadata of a media file

Exit codes: 0, 1, 3, 4

| Name | Type | Required | Help |
|---|---|---|---|
| `FILE` | text | yes | Media file (or a registered source id). |

## `videocli render`

Render an EDL with the cached render graph (resumable)

Exit codes: 0, 1, 2, 3, 4 · supports writes, `--dry-run`, `--estimate`

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text | yes | EDL name. |
| `-o OUT` | text | yes | Output file (.mp4, .mov or .mkv). |
| `--preset` | text |  | source, 1080p, 720p or preview (default: the EDL's output.preset). |
| `--preview` | flag |  | Same graph at the preview preset (480p, fast). |
| `--around-cuts S` | number |  | Only S seconds each side of every join, as one labelled reel. |
| `--burn-captions` | flag |  | Burn captions re-timed from the transcript. |
| `--no-normalize` | flag |  | Skip loudness normalization. |
| `--stems` | flag |  | Also write dialogue, music and effects stems next to the output. |

## `videocli scenes`

Shot boundaries (PySceneDetect), cached per parameters

Exit codes: 0, 1, 2, 4

| Name | Type | Required | Help |
|---|---|---|---|
| `FILE` | text | yes | Media file or source id. |
| `--detector` | text |  | adaptive or content. |
| `--threshold` | number |  | Detector threshold (adaptive 3.0, content 27). |
| `--min-scene S` | number |  | Shortest shot in seconds. |
| `--limit` | integer |  |  |
| `--from T` | time |  |  |
| `--to T` | time |  |  |

## `videocli schema`

JSON Schema for the envelope, a file type, or a command's data

Exit codes: 0, 2

| Name | Type | Required | Help |
|---|---|---|---|
| `NAME` | text |  | e.g. envelope, edl, probe, verify, 'command:render'. Omit to list. |
| `--all` | flag |  | Every schema. |
| `-o DIR` | text |  | With --all: write one file per schema. |

## `videocli setup`

Install a profile's dependency groups and models

Exit codes: 0, 1, 2, 3 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `--profile` | text |  | lecture, finishing, artistic or full. |
| `--with LIST` | text |  | Extra groups, comma-separated. |
| `--offline-check` | flag |  | Only report what is missing; download nothing. |

## `videocli sheet`

Contact sheet: a grid of labelled stills (--every, --scenes, --at or --cuts EDL)

Exit codes: 0, 1, 2, 4 · supports writes

| Name | Type | Required | Help |
|---|---|---|---|
| `FILE` | text |  | Media file or source id (not needed with --cuts). |
| `--every S` | number |  |  |
| `--scenes` | flag |  | The middle frame of each shot. |
| `--at T1,T2,...` | time |  |  |
| `--cuts NAME` | text |  | Before and after frames of each join in an EDL. |
| `--cols` | integer |  |  |
| `--width` | integer |  | Sheet width in pixels (default 1600). |
| `--from T` | time |  |  |
| `--to T` | time |  |  |
| `--max-tiles` | integer |  |  |

## `videocli silences`

Detected silences in a window, longest-first option

Exit codes: 0, 2, 4

| Name | Type | Required | Help |
|---|---|---|---|
| `FILE` | text | yes | Media file or source id. |
| `--min SECONDS` | number |  | Only silences at least this long. |
| `--from T` | time |  |  |
| `--to T` | time |  |  |
| `--limit` | integer |  |  |
| `--longest` | flag |  | Sort longest first. |

## `videocli skill install`

Write the skill to .claude/skills/videocli/SKILL.md (or an AGENTS.md section)

Exit codes: 0, 2 · supports writes, `--dry-run`

| Name | Type | Required | Help |
|---|---|---|---|
| `--target` | text |  | claude or generic (AGENTS.md). |
| `--scope` | text |  | project (current folder) or user (home). |
| `--dir PATH` | text |  | Install under this folder instead. |

## `videocli strip`

Filmstrip of frames over the audio waveform for a short window (inspect one cut)

Exit codes: 0, 1, 2, 4 · supports writes

| Name | Type | Required | Help |
|---|---|---|---|
| `FILE` | text | yes | Media file or source id. |
| `--from T` | time | yes |  |
| `--to T` | time | yes |  |
| `--width` | integer |  |  |
| `--frames` | integer |  | Frames across the window. |

## `videocli transcript`

Transcript text in a window: compact, segments, words or srt

Exit codes: 0, 2, 4

| Name | Type | Required | Help |
|---|---|---|---|
| `FILE` | text | yes | Media file or source id. |
| `--from T` | time |  | Window start (source time). |
| `--to T` | time |  | Window end (source time). |
| `--format` | text |  | compact, segments, words or srt. |
| `--limit` | integer |  | Maximum items (segments or words). |

## `videocli verify`

Check a render against its EDL: duration, streams, sync, black, frozen, clicks, gaps, loudness, clipped words

Exit codes: 0, 1, 2, 4, 5

| Name | Type | Required | Help |
|---|---|---|---|
| `OUTPUT OUT` | text | yes | Rendered file. |
| `--edl NAME` | text | yes | The EDL it was rendered from. |
| `--checks LIST` | text |  | Run only these checks. |
| `--skip LIST` | text |  | Skip these checks. |
| `--sheet` | flag |  | Also write a cut-review sheet for flagged joins. |

## Error codes

| Code | Meaning |
|---|---|
| `INTERRUPTED` | The command was interrupted; re-run it to resume where it stopped |
| `INTERNAL_ERROR` | An unexpected error; the log file named in details has the traceback |
| `FFMPEG_FAILED` | ffmpeg or ffprobe exited with an error |
| `ANALYZER_FAILED` | An analyzer failed while running |
| `BAKE_FAILED` | A baker produced no output or broke the bake contract |
| `RENDER_FAILED` | A render node failed |
| `DOWNLOAD_FAILED` | A download during setup failed or did not match its checksum |
| `INSTALL_FAILED` | Installing a dependency group failed |
| `BAD_ARGUMENT` | An argument or option is missing or invalid |
| `FEATURE_NOT_AVAILABLE` | The request needs a feature that a later phase delivers |
| `NOT_SUPPORTED` | The command does not support this mode (for example --estimate) |
| `FFMPEG_NOT_FOUND` | No usable ffmpeg or ffprobe was found |
| `FFMPEG_TOO_OLD` | ffmpeg is older than the minimum version or lacks a required feature |
| `MODEL_NOT_FOUND` | A required model is not downloaded |
| `FEATURE_NOT_INSTALLED` | An optional dependency group is not installed |
| `FILE_NOT_FOUND` | An input file does not exist |
| `NOT_A_MEDIA_FILE` | ffprobe could not read the input as media |
| `NO_AUDIO` | The source has no audio stream |
| `NO_VIDEO` | The source has no video stream |
| `EDL_INVALID` | The EDL file failed validation |
| `EDL_NOT_FOUND` | No EDL with that name exists in the workspace |
| `EDL_EXISTS` | An EDL with that name already exists |
| `ITEM_NOT_FOUND` | No clip or item with that id exists in the EDL |
| `SOURCE_CHANGED` | A source file changed since it was registered |
| `SOURCE_UNKNOWN` | The source is not registered in this workspace |
| `ARTIFACT_MISSING` | A required analysis artifact is missing; run analyze first |
| `WORKSPACE_LOCKED` | Another videocli process holds the workspace lock |
| `WORKSPACE_INVALID` | The workspace is unreadable or has an unknown schema version |
| `OUTPUT_IS_SOURCE` | Refusing to overwrite a source file |
| `OUTPUT_EXISTS` | The output file exists; pass --force to overwrite |
| `SCHEMA_INVALID` | A data file failed schema validation |
| `ORDER_NOT_FOUND` | No work order with that id exists |
| `ANSWER_INVALID` | A submitted answer does not match the order's schema |
| `TEMPLATE_INVALID` | A graphics template manifest or parameter is invalid |
| `PHRASE_NOT_FOUND` | A phrase could not be found in the transcript |
| `VERIFY_FAILED` | One or more verify checks failed |
| `LINT_FAILED` | EDL lint found errors |
| `AUDIT_FAILED` | Audit found issues at or above the --fail-on level |
| `GFX_LINT_FAILED` | Graphics lint found errors |
| `ORDERS_PENDING` | Work orders are waiting for answers; submit them and re-run |
| `REVIEW_PENDING` | A human review is waiting; ingest notes and re-run |
