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
