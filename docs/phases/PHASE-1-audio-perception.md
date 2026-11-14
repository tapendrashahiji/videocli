# Phase 1: Audio perception

**Goal:** turn a video's audio into text and numbers an agent can read: transcript, silences, loudness, and one merged index.

**Depends on:** phase 0.
**Unlocks:** planning (phase 2). After this phase an agent can answer "what is said, when, and where are the pauses" without rendering anything.

---

## Scope

**In**

- Analyzer framework and registry
- Analysis audio extraction
- Transcript with word timestamps
- Silence detection and loudness measurement
- Index builder
- Read commands: `index`, `transcript`, `silences`, `find`

**Out**

- Anything visual (phase 3)
- Speaker labels (phase 5)
- Semantic search (phase 5)

---

## Commands delivered

```
videocli analyze FILE [--only LIST] [--skip LIST] [--model NAME] [--language CODE]
videocli index FILE
videocli transcript FILE [--from T] [--to T] [--format compact|segments|words|srt] [--limit N]
videocli silences FILE [--min SECONDS] [--from T] [--to T] [--limit N]
videocli find FILE "QUERY" [--regex] [--context N] [--limit N]
```

### `analyze` data

```json
{
  "source_id": "a1b2c3d4e5f6",
  "ran": [ "audio", "silence", "loudness", "transcript" ],
  "cached": [ "probe" ],
  "skipped": [],
  "seconds": { "transcript": 412.6, "silence": 3.1 },
  "summary": { "speech_seconds": 2890.1, "silence_seconds": 722.4, "word_count": 21840, "language": "en" }
}
```

### `transcript --format compact`

One line per segment, with long silences shown inline so the agent sees pacing:

```
[0012.34-0015.60] So today we will look at binary search.
[0015.60-0017.90] (silence 2.30)
[0017.90-0024.05] Um, it works on a sorted array.
```

### `find` data

```json
{
  "query": "binary search",
  "total": 7,
  "truncated": false,
  "matches": [
    { "start": 13.10, "end": 13.92, "segment": 0, "text": "…we will look at binary search." }
  ]
}
```

---

## Tasks

### `analyze/base.py`

- [ ] `Analyzer` protocol and `RunContext` (workspace, settings, progress, ffmpeg) per SPEC §10.1
- [ ] Registry dictionary and `resolve(names)` with dependency ordering
- [ ] Cache check before each run; `--force` bypass
- [ ] `available()` gate that raises `FEATURE_NOT_INSTALLED` or `MODEL_NOT_FOUND` before any work starts
- [ ] Per-analyzer timing recorded in the result

### `media/audio.py` (analyzer `audio`)

- [ ] Extract the chosen audio stream to 16 kHz mono 16-bit WAV in the workspace
- [ ] `--audio-stream N` selection; default is the first stream
- [ ] Raise `NO_AUDIO` when the source has no audio

### `analyze/silence.py`

- [ ] Run ffmpeg `silencedetect` on the analysis audio
- [ ] Parse `silence_start`, `silence_end` and `silence_duration` from stderr
- [ ] Parameters: `threshold_db` (default −35), `min_duration` (default 0.3)
- [ ] Close a silence that runs to the end of the file
- [ ] Derive speech ranges as the complement

### `analyze/loudness.py`

- [ ] Run ffmpeg `ebur128` and parse the summary: integrated, range, true peak
- [ ] Store momentary loudness downsampled to one value per second
- [ ] Warn when integrated loudness is far from the default target

### `analyze/transcript.py`

- [ ] Load faster-whisper lazily; choose device and compute type automatically
- [ ] Word timestamps on; voice-activity filter on
- [ ] Parameters: `model`, `language` (auto by default), `initial_prompt`
- [ ] Progress events from segment end times against total duration
- [ ] Store segments, words and per-word probability
- [ ] Warning `LOW_CONFIDENCE_WORDS` when the share of low-probability words is high
- [ ] Clamp word times to the file duration; fix zero-length words

### `index/build.py`

- [ ] Merge available artifacts into `index.json` per SPEC §8.4
- [ ] Summary statistics: speech and silence seconds, counts, language, loudness
- [ ] Rebuild whenever an analyzer writes a new artifact

### `index/search.py`

- [ ] Case-insensitive phrase search across word sequences, returning word-accurate times
- [ ] `--regex` over segment text
- [ ] `--context N` returns N neighbouring segments

### CLI commands

- [ ] `analyze` with `--only` and `--skip`
- [ ] `index`, `transcript`, `silences`, `find`
- [ ] All list outputs honour `--limit`, `--from`, `--to` and report `total` and `truncated`
- [ ] Each command fills `next` with a sensible follow-up

---

## Tests

- Silence parser against captured ffmpeg stderr samples, including silence at the start and at the end
- Synthetic audio (tone, gap, tone) produces the expected silence ranges within 50 ms
- Loudness of a calibrated tone lands within 0.5 LU of the expected value
- Transcriber interface is mocked in unit tests
- One `slow` test runs the real `tiny` model on a short speech clip and checks for key words
- Cache: a second `analyze` runs nothing; changing a parameter re-runs only that analyzer
- `find` returns correct word times for a multi-word phrase spanning two segments
- Compact transcript is deterministic for a fixed transcript

---

## Acceptance criteria

1. `videocli analyze lecture.mp4` on a 60-minute lecture completes and writes probe, audio, silence, loudness, transcript and index artifacts.
2. A second run returns in under one second with everything listed under `cached`.
3. `videocli transcript lecture.mp4 --from 600 --to 660 --format compact` prints only that minute.
4. `videocli find lecture.mp4 "binary search"` returns timestamps that land on the spoken phrase when checked by ear.
5. Removing the speech model produces `MODEL_NOT_FOUND`, exit 3, with `videocli setup` as the hint, before any audio is extracted.

---

## Risks

| Risk | Mitigation |
|---|---|
| Word timestamps drift on long files or music | Voice-activity filter; phase 2 snapping uses silence edges as a second signal |
| Weak accuracy on Nepali or mixed-language speech | Make `model` and `language` easy to set; measure on real recordings before fixing the default |
| Transcription is slow on CPU | Smaller default model, progress events, cache, `--only` to skip it |
| One fixed silence threshold fails on noisy rooms | Report the noise floor from loudness data and suggest a threshold in `warnings` |
