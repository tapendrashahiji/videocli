# Quick start

## For people

```bash
curl -LsSf https://<project-domain>/install.sh | sh     # asks Lecture / Finishing / Full
videocli doctor                                          # ready: true?
```

Clean up a lecture in five commands:

```bash
videocli analyze lecture.mp4
videocli plan lecture.mp4 --cut-silence min=1.2,pad=0.15 --cut-fillers -o cut1
videocli edl lint cut1
videocli render cut1 -o lecture-cut.mp4
videocli verify lecture-cut.mp4 --edl cut1
```

Then look at what changed:

```bash
videocli edl show cut1                       # before/after durations, what was removed and why
videocli render cut1 --around-cuts 2 -o review.mp4   # a short reel of every join
videocli sheet --cuts cut1                   # before/after stills of every join
videocli captions cut1 -o lecture-cut.srt
```

Undo or adjust with commands, never by editing JSON:

```bash
videocli edl restore cut1 --from 612.0 --to 618.5     # bring back a removed passage (source times)
videocli edl cut cut1 --from 1810 --to 1954 --reason tangent
```

Output is human-readable in a terminal and JSON when piped (or with `--json`).

## For agents

```bash
videocli skill install           # writes .claude/skills/videocli/SKILL.md
videocli skill install --target generic   # or an AGENTS.md section for other agents
videocli agent-guide             # the same text on stdout
videocli commands                # every command, option and error code as JSON
videocli schema edl              # JSON Schema of any file type or command output
```

The skill tells the agent the workflow, the rules (never invent timestamps, lint before
render, verify after, never overwrite a source) and how to recover from each error code.
