# Phase 9: Story and style

**Goal:** give the tool an editorial model: what the edit is for, what style it should follow, which material serves the story, and planners that apply editing grammar.

**Depends on:** phases 5 and 6. Uses 7 and 8 when present.
**Unlocks:** rough cuts of artistic material (autonomy level L2) and phase 10.

**Why this phase is built the way it is:** on Timeline-Bench, agents under-cut, held static shots too long, and gained nothing measurable from written editorial guidance. So taste here comes from **reference edits turned into numbers**, and craft is **measured** by a new `audit` step. See [RESEARCH-ARTISTIC.md](../RESEARCH-ARTISTIC.md) §1.

**Test the central bet first:** before building everything below, run the small study in the first task group. If style profiles do not move human preference, rethink the phase.

---

## Scope

**In**

- Brief
- Style profiles learned from reference edits
- Story structure, selects and stringouts
- Assembly: radio edit, then picture
- Editorial planners: coverage, split edits, cut on action, cut to beat, pacing, match cuts
- Music fitting
- `audit`: craft metrics against a style profile

**Out**

- Independent critique, human notes and variants (phase 10)
- Writing narration or generating footage

---

## Commands delivered

```
videocli brief set [--logline TEXT] [--audience TEXT] [--duration S] [--aspect A] [--style NAME]
                   [--must-include "phrase"]... [--must-avoid "phrase"]... [--tone TEXT]
videocli brief show
videocli style learn REF... -o NAME
videocli style show NAME
videocli style compare NAME EDL_OR_VIDEO
videocli story init [--archetype doc|interview|tutorial|promo|music|custom]
videocli story show | set BEAT --intent TEXT --seconds S
videocli selects [--beat ID] [--per-beat N] [--provider NAME]
videocli stringout [--beat ID] -o NAME
videocli assemble -o NAME [--stage radio|picture|music]
videocli refine NAME --passes coverage,split-edits,action,beats,pacing,match [--style NAME]
videocli audit NAME|VIDEO --style NAME [--fail-on LEVEL]
```

---

## First: test the central bet

- [ ] Take three short pieces with existing human edits and raw footage
- [ ] Build two rough cuts of each by hand-driving the tools: one with default planner weights, one with weights taken from a style profile of similar reference edits
- [ ] Run a small blind preference test with at least five viewers, some of them editors
- [ ] Record the result in decisions.md (D10, X8). Continue as designed only if the profile version is preferred

---

## Brief

- [ ] `brief.json`: logline, audience, target duration, aspect ratio, tone, style name, must-include and must-avoid phrases, deliverables
- [ ] Must-include and must-avoid phrases resolve to transcript ranges and become locked or forbidden ranges for every planner
- [ ] The brief is the source for explicit requirements, which `audit` checks one by one

---

## Style profiles (`editorial/style.py`)

A profile is measured from finished videos using the same analyzers as source footage. Schema in SPEC §8.7.

- [ ] Run scenes, shots, motion, music, prosody, loudness and colour scopes on each reference
- [ ] **Pace:** average and median shot length, 90th percentile, longest static hold, cuts per minute across the normalised timeline
- [ ] **Grammar:** shot-size transition frequencies, dissolve rate, share of time under B-roll, split-edit rate (estimated from the offset between speaker change and picture cut)
- [ ] **Music:** share of time with music, share of cuts within a tolerance of a beat or downbeat, ducking depth
- [ ] **Speech:** words per minute, lengths of pauses kept
- [ ] **Colour and sound:** contrast, saturation, warmth, integrated loudness
- [ ] Merge several references into one profile, with spread, so planners know how strict each number is
- [ ] `style compare` reports each metric of an edit beside the profile, with the deviation

---

## Story and selects (`editorial/story.py`)

- [ ] `story.json` per SPEC §8.8: beats with intent and target seconds
- [ ] Archetypes as starting structures (for example hook, context, turn, payoff), always editable
- [ ] `selects`: for each beat, rank candidate ranges from transcript and shot records. Scoring combines delivery (prosody, no retakes, no clipped words), content match, and shot usability
- [ ] Content match uses a work order, so the calling agent or another provider judges which passages serve a beat
- [ ] Every select carries a `why`, so a human can audit the choice
- [ ] `stringout`: all selects for a beat in one EDL, for review
- [ ] Coverage report: beats with too few selects, and footage never selected

---

## Assembly (`editorial/assemble.py`)

Built in the stages editors use.

- [ ] **Radio edit:** choose and order spoken selects to fit the target duration; sound only matters at this stage. Tighten with the phase-2 rule planners
- [ ] **Picture:** keep the speaker on screen where it serves, and mark ranges that need covering
- [ ] **Music:** place a music bed if the brief or style calls for one
- [ ] Each stage writes a new EDL revision, so stages can be reviewed and redone separately
- [ ] Duration control: when over budget, trim tails first, then drop the lowest-scoring select, and report what was dropped

---

## Editorial planners (`editorial/idioms/`)

Each idiom proposes changes and scores them. A solver combines idioms, with weights from the style profile. `refine --passes` chooses which to run. Locked items are never touched.

| Pass | What it does | Data used |
|---|---|---|
| `coverage` | Covers jump cuts and long talking-head stretches with B-roll that matches the words | Shot descriptions, visual search, transcript |
| `split-edits` | Turns hard cuts between speakers into J and L cuts | Speaker turns, pauses |
| `action` | Moves picture cuts onto motion peaks; avoids cutting during a blink | Motion peaks, blink data |
| `beats` | Moves cuts to beats or downbeats within a tolerance, where the style supports it | Music structure |
| `pacing` | Splits or shortens holds beyond the style's limits; varies shot length toward the profile's distribution | Style profile, shots |
| `match` | Proposes match cuts between visually similar shots | Shot similarity |
| `punch` | Adds a punch-in on a same-angle cut to avoid a jump | Shot size, face position |

Tasks:

- [ ] Idiom interface: `propose(edl, facts, style) -> list[Change]` with a cost for each
- [ ] Solver: dynamic programming over candidate cut points for local decisions; greedy with look-ahead for B-roll placement
- [ ] Jump-cut detection: same subject, similar size and angle across a cut
- [ ] B-roll matching: query from the spoken words, filtered by usability and by not repeating a shot
- [ ] Each applied change records its idiom and reason in `history`
- [ ] `refine --dry-run` lists proposed changes with their scores, so the agent can accept or reject them

---

## Music fitting (`sound/musicfit.py`)

- [ ] Fit a track to the edit length by rearranging sections at downbeats, with cross-fades
- [ ] Align the strongest section change to a chosen story beat
- [ ] Report where the fit required a compromise

---

## Audit (`judge/audit.py`)

`audit` measures craft. It is separate from `verify`, which measures technical correctness.

```json
{
  "schema": "videocli.audit/v1",
  "style": "house",
  "passed": false,
  "brief": [ { "requirement": "must-include: 'three hundred years'", "status": "pass", "at": 14.2 } ],
  "metrics": [
    { "name": "average_shot_length", "value": 6.1, "target": 3.8, "tolerance": 1.2, "status": "fail" },
    { "name": "longest_static_hold", "value": 19.4, "limit": 11.0, "status": "fail", "at": 62.0 },
    { "name": "broll_coverage", "value": 0.41, "target": 0.46, "tolerance": 0.10, "status": "pass" },
    { "name": "cut_on_beat_rate", "value": 0.38, "target": 0.41, "tolerance": 0.10, "status": "pass" }
  ],
  "issues": [
    { "kind": "jump_cut", "at": 31.2, "clips": [ "c0007", "c0008" ], "fix": "videocli refine rough --passes coverage,punch" },
    { "kind": "repeated_shot", "at": 88.0, "shot": "s0113" }
  ]
}
```

- [ ] Metrics computed from the EDL where possible, and from the rendered video for anything the EDL cannot show
- [ ] Pace checks weighted toward too slow, because that is the direction editors penalised
- [ ] Issues: jump cuts, repeated shots, unmotivated dissolves, graphics timing, orphaned sound
- [ ] Brief requirements checked one by one
- [ ] Each issue carries a `fix` command where one exists
- [ ] `--fail-on` sets the severity that returns exit code 5
- [ ] Story topic added to the agent skill

---

## Tests

- Style metrics on synthetic edits with known shot lengths, beat alignment and dissolve counts
- Split-edit rate estimated correctly on a synthetic two-speaker edit with known offsets
- Selects scoring prefers a clean take over a retake of the same line
- Radio edit meets a target duration within tolerance and reports what it dropped
- Each idiom on a minimal fixture: a jump cut gets covered; a cut 80 ms off a beat moves to it; a 20-second static hold gets split
- Locked clips and must-include ranges survive every pass
- `audit` flags a deliberately slow edit and passes one built to the profile
- Solver is deterministic for fixed inputs

---

## Acceptance criteria

1. `videocli style learn refs/*.mp4 -o house` produces a profile whose pace numbers match a manual count on one reference.
2. From a brief and a logged shoot, `selects`, `assemble` and `refine` produce a rough cut of a three-minute piece with no human editing.
3. `videocli audit rough --style house` reports pace within tolerance after `refine`, where it failed before.
4. A human editor reviewing the rough cut judges it a usable starting point in at least half of five trial pieces. The count is published.
5. The result of the central-bet study is recorded.

---

## Risks

| Risk | Mitigation |
|---|---|
| Style numbers do not capture taste | Test the bet first; treat profiles as constraints and defaults, with human notes on top (phase 10) |
| Idioms applied mechanically look robotic | Weights and tolerances from the profile; solver trades idioms off; `refine --dry-run` lets the agent choose |
| B-roll matches the words but not the meaning | Work-order check on proposed coverage; sheets for confirmation |
| Selects miss the best moment | Stringouts for review; coverage report of unused footage |
| Scope is large | Passes are independent; ship `coverage`, `split-edits` and `pacing` first |
| Measuring "usable starting point" is subjective | Fixed questionnaire, several reviewers, published counts |
