"""Variants, tournaments and the ``direct`` loop (phase 10)."""

from __future__ import annotations

import time
from itertools import combinations
from typing import Any

from videocli.core.errors import Pending, UsageError
from videocli.core.fsutil import read_json, write_json
from videocli.core.workspace import Workspace, now_iso
from videocli.edl.model import EDL

AXES = ("pace", "music", "open", "coverage", "look")
HARD_ROUND_CAP = 8


# --------------------------------------------------------------------------- variants


def variants(ws: Workspace, edl: EDL, axis: str, n: int) -> list[EDL]:
    """N copies that differ along exactly one axis; untouched clips keep their render nodes."""
    from videocli.edl import ops

    if axis not in AXES:
        raise UsageError("BAD_ARGUMENT", f"--vary must be one of {', '.join(AXES)}.")
    out = []
    for k in range(1, n + 1):
        v = edl.model_copy(deep=True)
        v.name = f"{edl.name}.{axis}{k}"
        f = (k - (n + 1) / 2) / max(1, n)  # spread around the original: negative = tighter/less
        if axis == "pace":
            from videocli.editorial.idioms import Facts

            facts = Facts(ws)
            for c in v.tracks.main:
                if c.locked:
                    continue
                words = [w for w in facts.words(c.source) if c.start <= w["start"] and w["end"] <= c.end]
                if not words:
                    continue
                head = words[0]["start"] - c.start
                tail = c.end - words[-1]["end"]
                if f < 0:  # tighter: remove part of the air around the words
                    keep = 1 + 2 * f
                    ns, ne = c.start + head * (1 - keep), c.end - tail * (1 - keep)
                    if ne - ns > 0.2:
                        ops.trim(v, c.id, start=round(ns, 3), end=round(ne, 3))
                elif f > 0:  # looser: give back up to 0.4 s of air at each end
                    dur = v.sources[c.source].duration
                    ops.trim(v, c.id, start=round(max(0.0, c.start - 0.4 * f * 2), 3), end=round(min(dur, c.end + 0.4 * f * 2), 3))
        elif axis == "music":
            for it in v.tracks.audio:
                if it.role == "music":
                    it.gain_db = round(it.gain_db + 6 * f, 2)
                    it.start = round(max(0.0, it.start + 4 * k - 4), 3)
        elif axis == "open":
            if len(v.tracks.main) > k:
                ops.move(v, v.tracks.main[k].id, before=v.tracks.main[0].id)
        elif axis == "coverage":
            keep = max(0, round(len(v.tracks.overlay) * (k / n)))
            v.tracks.overlay = v.tracks.overlay[:keep]
        elif axis == "look":
            from videocli.edl.model import Grade

            ops_ = list((v.grades.get("look") or Grade()).ops)
            ops_.append({"op": "cdl", "slope": [1.0 + 0.05 * f] * 3, "sat": round(1.0 + 0.3 * f, 3)})
            v.grades["look"] = Grade(ops=ops_)
        out.append(v)
    return out


# --------------------------------------------------------------------------- tournament


def tournament_order(ws: Workspace, names: list[str], previews: dict[str, str]) -> dict[str, Any]:
    """Pairwise comparisons, each pair shown in both orders with blind labels X and Y."""
    from videocli.providers import orders as om

    items = []
    for a, b in combinations(names, 2):
        for first, second, tag in ((a, b, "ab"), (b, a, "ba")):
            items.append({"id": f"{a}|{b}|{tag}", "images": [], "videos": {"X": previews[first], "Y": previews[second]},
                          "question": "Watch X then Y. Which edit works better as a finished piece (story, pacing, shot choice)? Answer X, Y or tie.",
                          "answer_schema": {"winner": "X|Y|tie", "why": "string"}})
    return om.create(ws, "pairwise", items, meta={"edls": names})


def rank(names: list[str], answers: dict[str, Any]) -> list[dict[str, Any]]:
    wins = {n: 0.0 for n in names}
    played = {n: 0 for n in names}
    for iid, a in answers.items():
        x, y, tag = iid.split("|")
        first, second = (x, y) if tag == "ab" else (y, x)
        w = a["answer"]["winner"]
        played[first] += 1
        played[second] += 1
        if w == "X":
            wins[first] += 1
        elif w == "Y":
            wins[second] += 1
        else:
            wins[first] += 0.5
            wins[second] += 0.5
    table = [{"edl": n, "wins": wins[n], "played": played[n]} for n in names]
    table.sort(key=lambda r: (-r["wins"], r["edl"]))
    return table


# --------------------------------------------------------------------------- direct


def state_path(ws: Workspace, name: str):
    return ws.dir("direct") / f"{name}.json"


def load_state(ws: Workspace, name: str) -> dict[str, Any]:
    p = state_path(ws, name)
    if p.is_file():
        return read_json(p)
    return {"name": name, "round": 0, "started": now_iso(), "started_ts": time.time(), "rounds": [], "status": "running"}


def save_state(ws: Workspace, st: dict[str, Any]) -> None:
    write_json(state_path(ws, st["name"]), st)


def improved(prev: dict[str, Any] | None, cur: dict[str, Any]) -> bool:
    if prev is None:
        return True
    pc, cc = prev.get("critic"), cur.get("critic")
    if pc is not None and cc is not None and cc > pc + 0.05:
        return True
    return cur.get("audit_fails", 0) < prev.get("audit_fails", 0)


def stop_reason(st: dict[str, Any], rounds: int, budget_minutes: float | None) -> str | None:
    if st.get("approved"):
        return "approved"
    if st["round"] >= min(rounds, HARD_ROUND_CAP):
        return "round limit"
    if budget_minutes is not None and (time.time() - st.get("started_ts", time.time())) / 60 > budget_minutes:
        return "time budget"
    rs = st["rounds"]
    if len(rs) >= 3 and not improved(rs[-3], rs[-2]) and not improved(rs[-2], rs[-1]):
        return "no improvement in two rounds"
    return None


def review_pending(name: str, page: str, rnd: int) -> Pending:
    return Pending("REVIEW_PENDING", f"Round {rnd} of '{name}' is ready for review.",
                   hint=f"Open {page}, export notes, then: videocli notes ingest notes.json && videocli direct -o {name}",
                   details={"page": page, "round": rnd})
