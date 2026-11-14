# Phase 10: Director loop and evaluation (v1.0: artistic)

**Goal:** close the loop on artistic work. Judge edits independently, take human notes and apply them, explore variants, and measure against human editors by blind preference.

**Depends on:** phase 9. Uses every earlier phase.
**Unlocks:** autonomy level L3 (fine cut with notes) as a supported workflow, and a published measure of L4.

**Why this phase exists:** agents on Timeline-Bench could check renders for defects but could not judge edit quality, and their fixes missed what editors care about: pacing, story and shot choice. So judgement here is independent, organised around those three, and anchored to human preference. See [RESEARCH-ARTISTIC.md](../RESEARCH-ARTISTIC.md) §1.

---

## Scope

**In**

- Independent critique with a rubric
- Static review page and structured human notes
- Applying notes as EDL changes
- Variants and tournaments
- The `direct` loop
- Preference evaluation harness and Timeline-Bench runs
- Published results and release gates

**Out**

- A hosted review service
- Training or fine-tuning models

---

## Commands delivered

```
videocli critique NAME|VIDEO [--rubric NAME] [--refs REF...] [--provider NAME] [--fresh]
videocli review build NAME [-o DIR] [--variants A,B]
videocli notes ingest FILE
videocli notes list NAME [--open]
videocli notes apply NAME -o NEW [--only ID,...] [--dry-run]
videocli variants NAME --vary pace|music|open|coverage|look -n N
videocli tournament A B [C ...] [--provider NAME]
videocli direct -o NAME [--rounds N] [--gate human|critic] [--budget-minutes M]
python -m eval preference build|collect|report
python -m eval timeline-bench run|report
```

---

## Critique (`judge/critique.py`)

A critic is a provider that did not build the edit and does not see how it was built.

### Rubric

| Dimension | Weight | Looks at |
|---|---|---|
| Story | High | Clear through-line, strong open, payoff lands, nothing essential cut off |
| Pacing | High | Rhythm, holds, energy changes, drags |
| Shot choice | High | Best available take, coverage fits the words, variety, no repeats |
| Sound | Medium | Intelligibility, levels, music fit, transitions |
| Picture finish | Medium | Colour consistency, graphics timing and legibility |
| Technical | Low | Already covered by `verify` |

### Tasks

- [ ] Build a **critique pack**: a low-resolution preview, strips around every cut, sheets per scene, the compact transcript on output time, the `audit` report, and any reference videos. The EDL history and the builder's reasoning are left out
- [ ] Issue it as a work order of kind `critique`; the answer schema requires a score per dimension and a list of problems, each with a timecode, a category, evidence and a suggested fix
- [ ] `--fresh` requires a provider or context separate from the builder; with the `agent` provider, the skill tells the agent to use a separate sub-agent
- [ ] Providers that accept video get the preview file; others get strips and sheets sampled densely around cuts
- [ ] The brief tells the critic to find problems, not to praise
- [ ] Store `videocli.critique/v1` with provider provenance
- [ ] Calibrate: compare critic scores with human preference from the evaluation harness, and publish the agreement. A critic that does not track humans is not used as a gate

---

## Review page and notes (`judge/review.py`, `judge/notes.py`)

- [ ] `review build` writes a static folder: one HTML file, the preview video and thumbnails. No server
- [ ] The page plays the preview, lists cuts and markers, and lets the reviewer attach a note to a moment or a range, with a category (story, pacing, shot, sound, colour, graphics, other) and a priority
- [ ] With `--variants`, the page shows versions side by side for a choice, with blind labels
- [ ] The reviewer exports `notes.json`; `notes ingest` validates and stores it
- [ ] Notes are also accepted as plain text lines with timecodes, for people who prefer to type them
- [ ] `videocli.notes/v1`: id, output time or range, category, priority, text, status (open, applied, declined, question)

### Applying notes

- [ ] `notes apply --dry-run` turns each open note into proposed EDL operations, shown with the note text beside them
- [ ] Translation of note text into operations is a work order, constrained to existing `edl`, `grade`, `gfx` and `refine` commands
- [ ] Scope rule: a note changes only what it mentions. Everything else is locked for that round
- [ ] Ambiguous notes become questions for the human, not guesses
- [ ] Each applied note records the operations it produced; `notes list` shows what is still open
- [ ] After applying, `verify` and `audit` run again, and a note that made a metric worse is reported

---

## Variants and tournaments

- [ ] `variants` produces N versions that differ along one axis: pace (tighter or looser), music choice, opening, coverage density, or look
- [ ] Variants share cached render nodes, so N versions cost far less than N renders
- [ ] `tournament` runs pairwise comparisons through a critic work order, with blind labels and both orders, and returns a ranking with win counts
- [ ] A human can settle a tournament through the review page instead

---

## The `direct` loop

`direct` runs the artistic workflow end to end, stopping at gates.

```
brief → selects → assemble → refine → grade/gfx → preview
      → verify (must pass) → audit → critique → [gate] → notes apply → repeat
```

- [ ] Each round writes a new EDL revision and a round report: what changed, which metrics moved, which notes are open
- [ ] `--gate human` (default): stop with `REVIEW_PENDING` and a review page after each round
- [ ] `--gate critic`: continue while the critic's top problems keep changing, up to `--rounds`
- [ ] Stop conditions: human approval, round limit, time budget, or two rounds without improvement in critic or audit results
- [ ] Never loops forever: a hard cap on rounds, and remaining problems are reported, not hidden
- [ ] The loop is resumable from its state files
- [ ] Review topic added to the agent skill

---

## Evaluation (`eval/`)

### Preference harness

- [ ] Task format: raw material, brief, a human reference edit, and the tool's edit
- [ ] `preference build` makes blind paired review pages with randomised order
- [ ] `preference collect` gathers judgments: prefer A, prefer B, or no meaningful preference, with an optional reason
- [ ] `preference report`: **win-or-tie rate** per task and overall, with confidence intervals; reasons grouped by category
- [ ] Judges include working editors where possible; the mix is reported
- [ ] A project task set of at least ten pieces across documentary, interview, promo and music-driven material, with rights to use them

### Timeline-Bench

- [ ] Adapter that runs videocli, driven by an agent, on the public tasks and produces deliverables in the required format
- [ ] Use the benchmark's public verifier; report resolved rate and which tests failed
- [ ] Keep the harness, agent and model fixed between releases, so numbers are comparable

### Reporting and gates

- [ ] Publish every result in the docs, including poor ones, with footage type and judge mix
- [ ] After the first baseline, set release gates: the minimum win-or-tie rate and resolved rate a release must not fall below (decisions.md X6)
- [ ] State the autonomy level each material type has earned, from measured results

---

## Tests

- Critique pack excludes builder context; the answer schema rejects a critique without timecodes
- Notes: ingest, validation errors with line numbers, status changes
- `notes apply`: a note about one clip changes only that clip; an ambiguous note becomes a question
- Variants differ only along the requested axis and share cached nodes
- Tournament ranking is correct on a mocked judge with a known preference order, in both presentation orders
- `direct` stops at each stop condition, and resumes after interruption
- Win-or-tie rate and intervals correct on a hand-computed example
- Review page loads from the file system with no network access

---

## Acceptance criteria

1. A human can review a cut on the page, export notes, and `notes apply` produces a new revision that addresses each clear note and asks about each unclear one.
2. `videocli direct -o film --gate human` takes a logged shoot and a brief to a fine cut over several rounds, with every round reported.
3. Critic agreement with human preference is measured and published, and the critic is used as a gate only if it tracks humans.
4. The preference harness reports a win-or-tie rate against human reference edits for the project task set.
5. A Timeline-Bench run completes, and the resolved rate is published beside the benchmark's reported baselines.
6. **v1.0 release:** the docs state, from these numbers, which material types the tool handles at L1, L2 and L3, and what L4 measured.

---

## Risks

| Risk | Mitigation |
|---|---|
| The critic praises weak edits or misses pacing problems | Independent context, a problem-finding brief, calibration against human judgments, and no gating by an uncalibrated critic |
| The loop polishes technical details and ignores story | Rubric weights; `audit` first; stop when the top problems stop changing |
| Notes get over-applied and break other parts | Scope rule, per-round locks, re-running `verify` and `audit` |
| Human judges are hard to recruit | Start small, publish the judge mix, and add judges over time |
| Results are disappointing | Publish them; they define the autonomy level honestly and show where to work next |
| Benchmark format changes | Pin the benchmark version in each report |
| Preference tests need footage rights | Use openly licensed or commissioned material; record the licence per task |
