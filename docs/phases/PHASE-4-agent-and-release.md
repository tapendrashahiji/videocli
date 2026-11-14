# Phase 4: Agent integration and first release (v0.1: lectures)

**Goal:** make videocli easy for an agent to discover and use correctly, measure how well it works, and publish v0.1.0.

**Depends on:** phases 0 to 3.
**Unlocks:** public users, outside contributions, and real accuracy numbers.

---

## Scope

**In**

- The agent skill and self-description commands
- Installer hardening and release automation
- Documentation site or docs folder
- Evaluation harness
- v0.1.0 on PyPI

**Out**

- MCP server (deferred; decisions.md D12)
- Standalone binary (deferred; decisions.md D12)
- Evaluation of artistic edits by blind preference (phase 10)

---

## Commands delivered

```
videocli agent-guide [--topic NAME]
videocli skill install [--target claude|generic] [--scope project|user] [--dir PATH]
videocli schema [NAME] [--all]
videocli commands [--name CMD]
```

### `commands` data

```json
{
  "commands": [
    {
      "name": "edl cut",
      "summary": "Remove a source-time range from an EDL",
      "args": [ { "name": "NAME", "required": true } ],
      "options": [ { "name": "--from", "type": "time", "required": true }, { "name": "--to", "type": "time", "required": true } ],
      "writes": true,
      "exit_codes": [ 0, 2, 4 ]
    }
  ],
  "error_codes": { "EDL_INVALID": "The EDL file failed validation" }
}
```

Generated from the Typer app, so it cannot drift from the real interface.

---

## Tasks

### `agent/SKILL.md`

- [ ] Front matter with `name` and a `description` that says when to use the skill
- [ ] The workflow from SPEC §11 as a numbered recipe
- [ ] The rules from SPEC §11
- [ ] A short table: goal → command
- [ ] How to read the envelope, `next`, and exit codes
- [ ] Recovery recipes: missing model, lint failure, verify failure, `SOURCE_CHANGED`
- [ ] Token advice: read `index` first, use `--from` and `--to`, prefer `--format compact`, limit sheet size
- [ ] Keep it under about 300 lines
- [ ] Topic layout from the start: `core` now; `colour`, `compositing`, `graphics`, `story` and `review` are added by later phases and loaded with `agent-guide --topic`
- [ ] State the autonomy level honestly: this release is L1 clean-up, and the skill says what it cannot judge

### `agent/guide.py`

- [ ] `agent-guide` prints the packaged skill text
- [ ] `skill install` writes `.claude/skills/videocli/SKILL.md` (project) or the user-level equivalent
- [ ] `--target generic` writes a plain `AGENTS.md` section for other agents
- [ ] The installed file records the tool version; `doctor` warns when it is stale

### `agent/schema.py`

- [ ] Export JSON Schema from the Pydantic models for every file type and for the envelope
- [ ] `commands` introspects the Typer app and the error registry

### Agent trials

- [ ] Write five task prompts, such as "remove dead air from this lecture and add captions"
- [ ] Run each with an agent that has only the skill and a shell
- [ ] Record every wrong command, misread output and unnecessary step
- [ ] Fix the tool first (better `next` hints, clearer errors); fix the skill text second
- [ ] Keep the transcripts as regression cases

### Evaluation harness (`eval/`)

- [ ] Input format: a source, a human-made EDL, and the plan arguments to test
- [ ] Metrics:
  - cut-boundary precision and recall within a tolerance (default 0.25 s)
  - percent of source duration where human and tool agree on keep or cut
  - count of lint errors and verify failures
- [ ] `python -m eval run DIR` prints a table and writes JSON
- [ ] Publish results for a small set of openly licensed lectures
- [ ] Document how users run it on their own footage

### Installer and release

- [ ] Test `install.sh` on clean Linux and macOS, and `install.ps1` on clean Windows, in CI
- [ ] Installer is safe to re-run and upgrades in place
- [ ] `videocli setup` handles offline and proxy failure with clear errors
- [ ] Release workflow: tag → build → publish to PyPI with trusted publishing
- [ ] Changelog and version policy: the agent contract follows semantic versioning
- [ ] Uninstall instructions

### Documentation

- [ ] README: what it is, the one-line install, a 60-second example, limits
- [ ] Quick start for humans and for agents
- [ ] Command reference generated from `videocli commands`
- [ ] Schema reference generated from `videocli schema --all`
- [ ] Honest limits page: what it does well, and what it cannot judge
- [ ] Contributing guide: how to add an analyzer, a planner rule, a verify check

### Hardening

- [ ] Run the full workflow on at least ten varied real recordings; fix what breaks
- [ ] Odd inputs: vertical phone video, screen recordings with variable frame rate, audio-only, multiple audio tracks, files over two hours
- [ ] Freeze v1 schemas and the envelope

---

## Tests

- The packaged `SKILL.md` exists in the built wheel
- Every command named in the skill exists in `videocli commands`
- Every command's JSON output validates against its exported schema
- `skill install` is idempotent and reports the path it wrote
- Evaluation metrics are correct on a hand-computed example
- Installer smoke test on each operating system in CI

---

## Acceptance criteria

1. One line installs the tool on a clean machine on all three operating systems.
2. An agent given only `videocli skill install` and a lecture file produces a verified cut and captions in all five trial tasks, without a human correcting commands.
3. The evaluation harness reports precision, recall and agreement for at least five lectures, and the numbers are published in the docs.
4. v0.1.0 is on PyPI with a tagged release and changelog.
5. Schemas and the envelope are frozen and documented.

---

## Risks

| Risk | Mitigation |
|---|---|
| The skill text grows until agents ignore parts of it | Push guidance into `next` hints and error `hint` fields; keep the skill short |
| Agents hand-edit EDL JSON anyway | Validation with precise errors; make `edl` ops cover the common edits |
| Published accuracy disappoints | Publish it regardless, with the footage type stated; it tells users where the tool fits |
| Name conflict on PyPI | Resolve in phase 0, before any public mention |
