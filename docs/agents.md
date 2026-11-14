# The agent contract

This page is the public interface. It follows semantic versioning: v0.x may still change
it, with every change listed in the changelog; v1.0 freezes it.

## Output

- JSON mode when `--json` is given, `VIDEOCLI_OUTPUT=json` is set, or stdout is not a terminal.
- stdout carries exactly one JSON document. Logs and progress go to stderr
  (`--progress json` gives newline-delimited events).
- Success: `{"ok": true, "command", "version", "data", "artifacts", "warnings", "next"}`.
- Failure: `{"ok": false, "command", "version", "error": {"code", "message", "hint", "retryable", "details"}}`.
- Schemas: `videocli schema envelope`, `videocli schema error`, `videocli schema command:NAME`.

## Exit codes

| Code | Meaning | Examples |
|---|---|---|
| 0 | Success | |
| 1 | Runtime failure | `FFMPEG_FAILED`, `ANALYZER_FAILED`, `RENDER_FAILED`, `INTERNAL_ERROR` |
| 2 | Usage error | `BAD_ARGUMENT`, `FEATURE_NOT_AVAILABLE`, `NOT_SUPPORTED` |
| 3 | Missing dependency | `FFMPEG_NOT_FOUND`, `MODEL_NOT_FOUND`, `FEATURE_NOT_INSTALLED` |
| 4 | Bad input | `FILE_NOT_FOUND`, `EDL_INVALID`, `SOURCE_CHANGED`, `ARTIFACT_MISSING` |
| 5 | Checks failed | `VERIFY_FAILED`, `LINT_FAILED` |
| 6 | Waiting on an answer | `ORDERS_PENDING`, `REVIEW_PENDING` |
| 130 | Interrupted | `INTERRUPTED` (re-run to resume) |

The full registry is in `videocli commands` (`data.error_codes`) and [commands.md](commands.md).

## Global options

`--json` · `--workspace PATH` · `--progress auto|bar|json|none` · `--dry-run` · `--estimate` ·
`--force` · `--quiet` · `--verbose` · `--jobs N` · `--provider NAME` · `--version`.
They may appear anywhere on the command line.

## Time

- Public times are seconds with millisecond precision. Inputs also accept `MM:SS` and `HH:MM:SS.mmm`.
- Ranges are half-open.
- `edl cut|keep|restore` and `transcript|find|silences` use **source** time; `verify` items carry
  output time plus the mapped clip id and source time.
- EDL clip times snap to timeline frames on save.

## Files

| Path | Holds |
|---|---|
| `.videocli/sources/<id>/` | probe, audio.wav, transcript, silence, loudness, scenes, index, frames, sheets |
| `.videocli/edits/<name>.edl.json` | EDLs (`videocli.edl/v1`) |
| `.videocli/graph/<key>/` | cached render nodes |
| `.videocli/reports/` | verify reports and review sheets |
| `.videocli/logs/` | ffmpeg logs and internal-error tracebacks |

A source id is the first 12 hex characters of SHA-256 over the file size plus three 1 MiB
blocks, so it survives renames and moves.
