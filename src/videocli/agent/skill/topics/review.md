# videocli topic: critique, review and notes

Load with `videocli agent-guide --topic review`. Whatever built an edit never approves it.

## Independent critique

- `videocli critique NAME --fresh` builds a pack (preview, strips around every cut, transcript on
  output time, audit) without the EDL history or your reasoning, and issues a `critique` work
  order. With `--fresh` and the agent provider, **answer it with a separate sub-agent** that has not
  seen how the edit was built. Find problems; do not praise. Every problem needs a timecode,
  category, evidence and fix.

## Human review

- `videocli review build NAME` → a static page (no server). The person watches, adds notes at the
  playhead, can tick "approve", and exports `notes.json`.
- `--variants A,B` shows versions side by side with blind labels for a choice.
- `videocli notes ingest notes.json` (or a text file: `12.4 pacing high: hold longer`, with `--edl`).
- `videocli notes apply NAME -o NEW --dry-run`, then without `--dry-run`. Each note becomes a
  `note_ops` work item: answer with commands on NEW that touch **only** the clips in the note's
  window (others are rejected), or `action: question` when the note is ambiguous — never guess.
- `videocli notes list NAME --open` shows what is still open; `verify` and `audit` run again after.

## Variants and the loop

- `videocli variants NAME --vary pace|music|open|coverage|look -n 3` then
  `videocli tournament NAME.pace1 NAME.pace2 NAME.pace3` (pairwise, both orders, blind).
- `videocli direct -o NAME --gate human` runs assemble → refine → preview → verify → audit →
  critique → review page, then stops with `REVIEW_PENDING`. After notes are ingested, run it again:
  it applies them and starts the next round. It stops on approval, the round limit (hard cap 8),
  the time budget, or two rounds without improvement — and reports what is still open.
