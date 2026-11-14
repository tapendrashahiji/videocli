"""Preference evaluation (phase 10) and the Timeline-Bench adapter.

``python -m eval preference build TASKS_DIR -o PAGES_DIR``
    One blind paired page per task (human reference edit vs videocli edit, order randomised).
``python -m eval preference collect PAGES_DIR JUDGMENTS.json [...]``
    Merge exported judgments ({task, choice: A|B|none, reason, judge, role}) and un-blind them.
``python -m eval preference report PAGES_DIR``
    Win-or-tie rate for videocli per task and overall, with Wilson 95% intervals; reasons by category.

``python -m eval timeline-bench run BENCH_DIR`` prints the adapter plan; ``report RESULTS.json`` summarises
the benchmark's own verifier output (resolved rate and failing tests).
"""

from __future__ import annotations

import argparse
import html
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

CATEGORY_WORDS = {
    "pacing": ("pace", "slow", "fast", "drag", "rhythm", "long", "short", "tight"),
    "story": ("story", "structure", "open", "ending", "point", "clear", "order", "arc"),
    "shot": ("shot", "take", "angle", "b-roll", "broll", "coverage", "framing", "visual"),
    "sound": ("sound", "audio", "music", "level", "voice", "loud"),
    "finish": ("colour", "color", "grade", "title", "graphic", "caption"),
}


def wilson(k: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def win_or_tie(judgments: list[dict[str, Any]]) -> dict[str, Any]:
    """Win-or-tie rate of the tool against the human reference."""
    n = len(judgments)
    k = sum(1 for j in judgments if j["winner"] in ("tool", "tie"))
    lo, hi = wilson(k, n)
    return {"n": n, "win_or_tie": round(k / n, 4) if n else None, "ci95": [lo, hi],
            "tool_wins": sum(1 for j in judgments if j["winner"] == "tool"), "ties": sum(1 for j in judgments if j["winner"] == "tie"),
            "human_wins": sum(1 for j in judgments if j["winner"] == "human")}


def categorise(reason: str) -> list[str]:
    r = reason.lower()
    cats = [c for c, words in CATEGORY_WORDS.items() if any(w in r for w in words)]
    return cats or ["other"]


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Which edit is better?</title>
<style>body{font:15px system-ui;margin:0;padding:16px;max-width:1100px;margin:auto}video{width:100%;background:#000}
.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}textarea{width:100%;min-height:60px}</style></head><body>
<h1>Task __TASK__: which edit works better?</h1><p>__BRIEF__</p>
<div class="g"><div><h3>A</h3><video controls src="__A__"></video></div><div><h3>B</h3><video controls src="__B__"></video></div></div>
<p><label><input type="radio" name="c" value="A"> A</label> <label><input type="radio" name="c" value="B"> B</label>
<label><input type="radio" name="c" value="none"> no meaningful preference</label></p>
<p><label>Judge <input id="j"></label> <label>Role <select id="r"><option>editor</option><option>viewer</option><option>other</option></select></label></p>
<textarea id="why" placeholder="Why? (optional)"></textarea><p><button id="x">Export judgment</button></p>
<script>document.getElementById('x').onclick=()=>{const c=document.querySelector('input[name=c]:checked');if(!c){alert('Choose A, B or none');return}
const d={task:'__TASK__',choice:c.value,reason:document.getElementById('why').value,judge:document.getElementById('j').value,role:document.getElementById('r').value};
const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(d)],{type:'application/json'}));a.download='judgment-__TASK__.json';a.click();};</script>
</body></html>"""


def build(tasks_dir: Path, out: Path, seed: int | None = None) -> dict[str, Any]:
    """Each task folder holds task.json: {"human": "human.mp4", "tool": "tool.mp4", "brief": "...", "licence": "..."}."""
    import shutil

    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    key = {}
    for t in sorted(p for p in tasks_dir.iterdir() if (p / "task.json").is_file()):
        spec = json.loads((t / "task.json").read_text())
        pair = [("human", t / spec["human"]), ("tool", t / spec["tool"])]
        rng.shuffle(pair)
        d = out / t.name
        d.mkdir(exist_ok=True)
        for lab, (who, src) in zip("AB", pair, strict=False):
            shutil.copyfile(src, d / f"{lab}{src.suffix}")
            key.setdefault(t.name, {})[lab] = who
        page = (PAGE.replace("__TASK__", html.escape(t.name)).replace("__BRIEF__", html.escape(spec.get("brief", "")))
                .replace("__A__", f"A{pair[0][1].suffix}").replace("__B__", f"B{pair[1][1].suffix}"))
        (d / "index.html").write_text(page, encoding="utf-8")
    (out / ".key.json").write_text(json.dumps(key, indent=2), encoding="utf-8")
    return {"tasks": len(key), "dir": str(out)}


def collect(pages: Path, files: list[Path]) -> dict[str, Any]:
    key = json.loads((pages / ".key.json").read_text())
    store = pages / "judgments.json"
    all_j = json.loads(store.read_text()) if store.is_file() else []
    added = 0
    for f in files:
        raw = json.loads(f.read_text())
        for j in raw if isinstance(raw, list) else [raw]:
            if j["task"] not in key or j["choice"] not in ("A", "B", "none"):
                print(f"skipping invalid judgment in {f}: {j}", file=sys.stderr)
                continue
            j["winner"] = "tie" if j["choice"] == "none" else key[j["task"]][j["choice"]]
            all_j.append(j)
            added += 1
    store.write_text(json.dumps(all_j, indent=2), encoding="utf-8")
    return {"added": added, "total": len(all_j)}


def report(pages: Path) -> dict[str, Any]:
    js = json.loads((pages / "judgments.json").read_text())
    tasks = sorted({j["task"] for j in js})
    per = {t: win_or_tie([j for j in js if j["task"] == t]) for t in tasks}
    reasons: dict[str, int] = {}
    for j in js:
        if j.get("reason") and j["winner"] == "human":
            for c in categorise(j["reason"]):
                reasons[c] = reasons.get(c, 0) + 1
    mix: dict[str, int] = {}
    for j in js:
        mix[j.get("role") or "unknown"] = mix.get(j.get("role") or "unknown", 0) + 1
    return {"overall": win_or_tie(js), "per_task": per, "losses_by_category": reasons, "judge_mix": mix}


def bench_report(results: Path) -> dict[str, Any]:
    """Summarise Timeline-Bench verifier output: [{task, resolved: bool, failed_tests: [...]}, ...]."""
    rs = json.loads(results.read_text())
    n = len(rs)
    resolved = sum(1 for r in rs if r.get("resolved"))
    fails: dict[str, int] = {}
    for r in rs:
        for t in r.get("failed_tests", []):
            fails[t] = fails.get(t, 0) + 1
    return {"tasks": n, "resolved": resolved, "resolved_rate": round(resolved / n, 4) if n else None,
            "most_failed": sorted(fails.items(), key=lambda kv: -kv[1])[:10], "benchmark_version": rs[0].get("benchmark_version") if rs else None}


def cli(name: str, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog=f"python -m eval {name}")
    ap.add_argument("action")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("-o", "--output")
    ap.add_argument("--seed", type=int)
    a = ap.parse_args(argv)
    if name == "preference":
        if a.action == "build":
            res = build(Path(a.paths[0]), Path(a.output or "preference-pages"), a.seed)
        elif a.action == "collect":
            res = collect(Path(a.paths[0]), [Path(p) for p in a.paths[1:]])
        elif a.action == "report":
            res = report(Path(a.paths[0]))
        else:
            print("actions: build, collect, report", file=sys.stderr)
            return 2
    else:
        if a.action == "report":
            res = bench_report(Path(a.paths[0]))
        elif a.action == "run":
            res = {"plan": ["Check out the public Timeline-Bench tasks and verifier (pin the version).",
                            "For each task: give an agent the brief, the raw material, `videocli skill install`, and a shell.",
                            "Collect the deliverable in the benchmark's required format; run the public verifier.",
                            "Write verifier results as JSON and run: python -m eval timeline-bench report RESULTS.json"],
                   "bench_dir": a.paths[0] if a.paths else None}
        else:
            print("actions: run, report", file=sys.stderr)
            return 2
    print(json.dumps(res, indent=2))
    return 0
