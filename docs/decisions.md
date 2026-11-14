# Decisions

**Date:** 2026-11-14 · Supports [SPEC.md](SPEC.md) v0.2

> This file was reconstructed from the planning session summary, because the original
> `decisions.md` was not available when the repository was created. The twelve decisions
> below are exactly as answered. The IDs for defaults and deferred items (D-, T-, X-) are
> the ones the phase documents cite; their wording here is a best reconstruction and can be
> corrected against the original.

## Decided

| ID | Topic | Choice | Notes |
|---|---|---|---|
| D1 | Name | `videocli` | Free on PyPI when checked on 2026-11-14. Reserve it with a placeholder release. |
| D2 | Licence | MIT | ffmpeg is run as a separate process and never redistributed in the wheel. |
| D3 | ffmpeg | System copy first, otherwise a pinned, checksummed download | Order: `VIDEOCLI_FFMPEG` → `PATH` (≥ 5.0 with required features) → user cache. |
| D4 | Languages | English first, others best-effort | Default speech model `small`. |
| D5 | Model judgement | The calling agent, through work orders | `local` and `api` providers are optional. |
| D6 | Install | The installer asks which profile | Terminal attached: show Lecture, Finishing, Full with sizes and ask (read from `/dev/tty`). `--profile NAME` skips the question. No terminal: install `lecture` and print how to add more. |
| D7 | Colour working space | Rec.709, ACES optional | ACES through OpenColorIO (`ocio` group). |
| D8 | Motion-graphics engines | Manim and HyperFrames, both optional | Remotion is not bundled (source-available licence); attach via the `generic` baker. |
| D9 | Artistic goal at v1.0 | Fine cut with human notes (L3) | L4 is measured and published, never promised. |
| D10 | Style profiles | Test first with a small blind study | Gates phase 9. Result recorded under X8. |
| D11 | Build order after v0.1 | Phase 6, then 7 and 8, then 5, 9, 10 | Phase 5's work-order protocol may be pulled forward. |
| D12 | Packaging | Command line only until after v0.1 | No MCP server or standalone binary yet. |

## Defaults set without asking

Each has a conventional answer and can be reopened.

| ID | Default |
|---|---|
| T1 | JSON output when stdout is not a terminal, `--json` is given, or `VIDEOCLI_OUTPUT=json`. |
| T2 | The workspace is a `.videocli/` folder in the current directory (override with `--workspace`). |
| T3 | Alpha intermediates: `qtrle` for short bakes, ProRes 4444 for long ones. |
| T4 | Lists default to `--limit 50` and report `total` and `truncated`. |
| T5 | Public times are seconds with millisecond precision; ranges are half-open. |
| T6 | `source_id` is a content hash (size + three 1 MiB blocks), not a path hash. |
| T7 | Contact sheets default to 6 columns, 48 tiles and 1600 px wide. |
| T8 | Default loudness target −14 LUFS integrated, −1 dBTP ceiling. |
| T9 | Progress goes to stderr; stdout carries exactly one JSON document. |

## Deferred until measured

| ID | Question | Decided by | When |
|---|---|---|---|
| X1 | Which static ffmpeg build to pin per platform | Feature check against SPEC §4.3 on each OS | Phase 0/4 |
| X2 | Rule-planner defaults (silence `min`, `pad`, filler lists) | Evaluation harness on real lectures | Phase 4 |
| X3 | Shot-logging default provider (`agent` vs `local`) | Measured comparison on a sample | Phase 5 |
| X4 | Music analysis library | Accuracy and install weight of two candidates | Phase 5 |
| X5 | Critic gating | Critic agreement with human preference | Phase 10 |
| X6 | Artistic release gates (minimum win-or-tie and resolved rates) | First baseline | Phase 10 |
| X7 | MCP server or standalone binary | Demand after v0.1 | After phase 4 |
| X8 | Whether style profiles move human preference | The phase-9 central-bet study | Phase 9 start |
