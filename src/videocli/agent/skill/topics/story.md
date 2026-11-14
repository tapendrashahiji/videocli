# videocli topic: story and style

Load with `videocli agent-guide --topic story`. Taste comes from reference edits turned into
numbers and from human notes — not from longer instructions. Use the tools to measure; do not
argue from your own sense of pacing.

## Order of work

1. `videocli analyze footage/ --profile artistic` then `videocli log footage/` (answer the work
   orders: one honest sentence per shot).
2. `videocli style learn refs/*.mp4 -o house` — two or three finished edits the person likes.
3. `videocli brief set --logline "…" --duration 180 --style house --must-include "…"`.
4. `videocli story init --archetype doc|interview|tutorial|promo|music`; adjust with
   `videocli story set BEAT --intent … --seconds …`.
5. `videocli selects` — exits 6 with a `beat_match` work order: read the candidates in the order's
   brief and answer `{"picks": [indices best first], "why": "…"}` per beat. Re-run.
   Every select carries a `why`. Review with `videocli stringout -o strings`.
6. `videocli assemble -o rough` (radio edit: sound first; fits the duration; must-include locked),
   then `--stage picture` (marks holds that need coverage), then `--stage music --music score.wav`.
7. `videocli refine rough --passes coverage,split-edits,pacing` (`--dry-run` first to see each
   proposal and its reason). Ship `coverage`, `split-edits`, `pacing` first; `beats`, `action`,
   `match`, `punch` when the material supports them.
8. `videocli audit rough --style house` — pace too slow fails hardest. Fix what it names, then
   render, `verify`, and hand over for review.

## Rules

- Locked clips and must-include ranges are never changed by planners.
- A planner proposal is a suggestion with a cost; read the reason before accepting a pass.
- Report audit numbers to the person; do not call a cut "good" yourself.
