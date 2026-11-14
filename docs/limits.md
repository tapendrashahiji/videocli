# What videocli does well, and what it cannot judge

## v0.1 (lectures): autonomy level L1, clean-up

**Good at**

- Removing dead air and filler words from lectures, tutorials and talking heads.
- Cutting and restoring ranges precisely, with clip edges kept off spoken words.
- Frame-accurate renders whose picture and sound stay in sync across hundreds of joins.
- Captions re-timed to the edit.
- Checking its own renders: duration, streams, sync, black or frozen frames not in the source,
  clicks at joins, long silences, loudness, words cut in half.

**Cannot judge**

- Whether the edit tells the story well, whether the pacing is right, or which take is best.
- Whether a tangent is worth keeping. It can find where something was said; deciding what
  matters is the agent's or the person's call.
- Visual quality beyond black and frozen frames.

**Known limits**

- Transcription is English-first; other languages are best-effort (choose `--language` and a
  larger `--model`).
- One fixed silence threshold can fail in noisy rooms; `analyze` warns with a suggested value.
- Click detection reports warnings, not failures, until calibrated on more real renders.
- Overlays, music, transitions, split edits, grades and graphics validate in the EDL but render
  only from later releases (`FEATURE_NOT_AVAILABLE` names the phase).
- Accuracy numbers come from the evaluation harness ([evaluation.md](evaluation.md)); they are
  published as measured, including where they disappoint.

## Finishing and artistic work (0.5–0.9)

- Compositing, colour, graphics and the editorial planners are implemented and tested on
  synthetic material. Their quality on real footage is not yet measured; the targets in
  [evaluation.md](evaluation.md) decide the autonomy level each material type earns.
- Without the optional groups, perception is local and light: faces use OpenCV's Haar detector,
  music uses a built-in beat tracker (sections unlabelled), events are heuristics, and shot
  descriptions come from the calling agent through work orders.
- The critic is an agent answering a work order. It is **not** used as a gate until its agreement
  with human preference is measured (decision X5).
- Laughter/applause detection, speaker diarization, visual search by text, and matting need the
  optional `perception`, `diarize` and `matte` groups and their models.
