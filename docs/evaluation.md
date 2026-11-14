# Evaluation

## Clean-up accuracy (L1)

`python -m eval run DIR` runs videocli on tasks that have a human-made edit and reports:

- **Cut-boundary precision and recall** within a tolerance (default 0.25 s): how many of the
  tool's cut points a human also made, and how many of the human's the tool found.
- **Agreement**: the share of the source duration where tool and human agree on keep versus cut.
- **Lint errors** and, with `--render`, **verify failures**.

Task layout:

```
DIR/<task>/task.json
{
  "source": "lecture.mp4",
  "human": "human.edl.json",          // a videocli EDL, or [[start, end], ...] keep ranges
  "plan_args": ["--cut-silence", "min=1.2,pad=0.15", "--cut-fillers"],
  "analyze_args": [],
  "licence": "CC-BY 4.0"
}
```

Run it on your own footage the same way: put a source and your own edit in a folder, write
`task.json`, and run `python -m eval run DIR` from a checkout of this repository. Results go
to `DIR/results.json`.

## Published results

No results are published yet. The plan (phase 4) is at least five openly licensed lectures,
with the footage type stated for each. Numbers will be published here as measured.

## Artistic work (L2–L4)

- `python -m eval preference build TASKS -o PAGES` makes blind paired pages (human reference vs
  videocli, order randomised; the key stays outside the pages).
- `python -m eval preference collect PAGES judgments*.json` then `report PAGES`: win-or-tie rate per
  task and overall with Wilson 95% intervals, losses grouped by reason, and the judge mix.
- `python -m eval timeline-bench run BENCH` prints the adapter plan; `report RESULTS.json`
  summarises the benchmark's own verifier (resolved rate, most-failed tests). Pin the benchmark
  version in every report.

Release gates (decision X6) are set after the first baseline. Every result is published here,
including poor ones, with footage type and judge mix.
