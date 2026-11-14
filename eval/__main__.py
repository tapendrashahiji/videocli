"""``python -m eval run DIR``: run videocli on tasks with human EDLs and report accuracy.

Task layout::

    DIR/<task>/task.json   {"source": "lecture.mp4", "human": "human.edl.json" | [[start, end], ...],
                            "plan_args": ["--cut-silence", "min=1.2,pad=0.15", "--cut-fillers"],
                            "analyze_args": [], "licence": "CC-BY 4.0"}

``human`` is either a videocli EDL (its spine clips are the keep ranges) or a list of keep ranges in source seconds.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Any

from eval.metrics import agreement, precision_recall


def _videocli(args: list[str], workspace: Path) -> dict[str, Any]:
    from videocli.cli.app import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(["--json", "--progress", "none", "--workspace", str(workspace), *args])
    doc = json.loads(buf.getvalue().strip().splitlines()[-1])
    doc["_exit"] = code
    return doc


def _human_keep(task_dir: Path, spec: Any) -> list[tuple[float, float]]:
    if isinstance(spec, list):
        return [(float(a), float(b)) for a, b in spec]
    doc = json.loads((task_dir / spec).read_text(encoding="utf-8"))
    return [(c["start"], c["end"]) for c in doc["tracks"]["main"]]


def run_task(task_dir: Path, tol: float, render: bool) -> dict[str, Any]:
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    src = (task_dir / task["source"]).resolve()
    ws = task_dir / "eval-work"  # videocli puts its .videocli/ inside
    res: dict[str, Any] = {"task": task_dir.name, "source": task["source"], "licence": task.get("licence")}
    a = _videocli(["analyze", str(src), *task.get("analyze_args", [])], ws)
    if not a["ok"]:
        res["error"] = a["error"]
        return res
    p = _videocli(["plan", str(src), "-o", "tool", "--force", *task.get("plan_args", [])], ws)
    if not p["ok"]:
        res["error"] = p["error"]
        return res
    edl = json.loads((ws / ".videocli" / "edits" / "tool.edl.json").read_text(encoding="utf-8"))
    duration = next(iter(edl["sources"].values()))["duration"]
    tool_keep = [(c["start"], c["end"]) for c in edl["tracks"]["main"]]
    human = _human_keep(task_dir, task["human"])
    res.update(precision_recall(tool_keep, human, duration, tol))
    res["agreement"] = agreement(tool_keep, human, duration)
    lint = _videocli(["edl", "lint", "tool"], ws)
    res["lint_errors"] = 0 if lint["ok"] else lint["error"]["details"].get("errors", 1)
    if render:
        out = ws / "tool.mp4"
        r = _videocli(["render", "tool", "-o", str(out), "--force"], ws)
        if r["ok"]:
            v = _videocli(["verify", str(out), "--edl", "tool"], ws)
            res["verify_failures"] = 0 if v["ok"] else len(v["error"]["details"].get("failed", []))
        else:
            res["verify_failures"] = None
            res["render_error"] = r["error"]["code"]
    return res


def table(rows: list[dict[str, Any]]) -> str:
    cols = ["task", "precision", "recall", "f1", "agreement", "lint_errors", "verify_failures"]
    lines = ["  ".join(f"{c:>15}" for c in cols)]
    for r in rows:
        lines.append("  ".join(f"{str(r.get(c, r.get('error', {}).get('code', '-') if c == 'precision' else '-')):>15}" for c in cols))
    ok = [r for r in rows if "precision" in r]
    if ok:
        mean = {c: round(sum(r[c] for r in ok) / len(ok), 4) for c in ("precision", "recall", "f1", "agreement")}
        lines.append("  ".join(f"{str(v):>15}" for v in ["mean", mean["precision"], mean["recall"], mean["f1"], mean["agreement"], "", ""]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="Run clean-up accuracy tasks")
    r.add_argument("dir", type=Path)
    r.add_argument("--tolerance", type=float, default=0.25)
    r.add_argument("--render", action="store_true", help="Also render and verify each tool EDL")
    r.add_argument("-o", "--output", type=Path, default=None, help="JSON results file (default DIR/results.json)")
    for name in ("preference", "timeline-bench"):
        sp = sub.add_parser(name, help="Phase 10 harness")
        sp.add_argument("action", nargs="?", default="report")
        sp.add_argument("rest", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    if args.cmd in ("preference", "timeline-bench"):
        from eval import preference

        return preference.cli(args.cmd, [args.action, *args.rest])
    tasks = sorted(p for p in args.dir.iterdir() if (p / "task.json").is_file())
    if not tasks:
        print(f"no tasks found in {args.dir}", file=sys.stderr)
        return 2
    rows = [run_task(t, args.tolerance, args.render) for t in tasks]
    out = args.output or (args.dir / "results.json")
    out.write_text(json.dumps({"tolerance": args.tolerance, "tasks": rows}, indent=2) + "\n", encoding="utf-8")
    print(table(rows))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
