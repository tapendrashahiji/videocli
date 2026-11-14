"""``review build``: a static review folder (one HTML file, the preview, thumbnails). No server, no network."""

from __future__ import annotations

import html
import json
import random
import shutil
from pathlib import Path
from typing import Any

from videocli.core.fsutil import write_json
from videocli.core.workspace import Workspace
from videocli.edl.model import EDL

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Review: __TITLE__</title>
<style>
:root{--bg:#fafafa;--fg:#111;--mut:#666;--acc:#c0392b;--card:#fff;--line:#ddd}
@media (prefers-color-scheme:dark){:root{--bg:#151515;--fg:#eee;--mut:#999;--card:#1f1f1f;--line:#333}}
body{margin:0;font:15px/1.45 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
main{max-width:1100px;margin:0 auto;padding:16px}
.players{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
video{width:100%;background:#000;border-radius:6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin-top:12px}
table{width:100%;border-collapse:collapse}td,th{padding:4px 6px;border-bottom:1px solid var(--line);text-align:left;font-size:14px}
button,select,input,textarea{font:inherit;padding:6px 8px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
button.primary{background:var(--acc);color:#fff;border:0}.mut{color:var(--mut)}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
textarea{width:100%;min-height:60px;box-sizing:border-box}a{color:inherit}
</style></head><body><main>
<h1>__TITLE__</h1><p class="mut">Watch, then add notes at the playhead (or mark a range). Export the notes and give the file back: <code>videocli notes ingest notes.json</code>.</p>
<div class="players">__PLAYERS__</div>
<div class="card"><div class="row">
<label>Version <select id="ver">__VEROPTS__</select></label>
<button id="mark">Mark start</button><span id="range" class="mut"></span>
<label>Category <select id="cat"><option>story</option><option>pacing</option><option>shot</option><option>sound</option><option>colour</option><option>graphics</option><option>other</option></select></label>
<label>Priority <select id="pri"><option>medium</option><option>high</option><option>low</option></select></label></div>
<textarea id="txt" placeholder="What should change? Be specific: 'hold longer on the hands'"></textarea>
<div class="row"><button class="primary" id="add">Add note at playhead</button>__CHOICE__
<label><input type="checkbox" id="ok"> Approve this cut</label><button id="export">Export notes.json</button></div></div>
<div class="card"><h3>Notes</h3><table id="notes"><tr><th>Time</th><th>Category</th><th>Priority</th><th>Note</th><th></th></tr></table></div>
<div class="card"><h3>Cuts and markers</h3><table>__CUTS__</table></div>
</main><script>
const EDL=__EDL__;const notes=[];let start=null;const vids=[...document.querySelectorAll('video')];
const cur=()=>vids[Math.max(0,document.getElementById('ver').selectedIndex)];
document.getElementById('mark').onclick=()=>{start=cur().currentTime;document.getElementById('range').textContent='from '+start.toFixed(2)+'s';};
function render(){const t=document.getElementById('notes');t.querySelectorAll('tr.n').forEach(r=>r.remove());
notes.forEach((n,i)=>{const r=t.insertRow();r.className='n';r.insertCell().textContent=(n.from!==undefined?n.from.toFixed(2)+'-'+n.to.toFixed(2):n.at.toFixed(2))+'s';
r.insertCell().textContent=n.category;r.insertCell().textContent=n.priority;r.insertCell().textContent=n.text;const b=document.createElement('button');b.textContent='×';b.onclick=()=>{notes.splice(i,1);render()};r.insertCell().appendChild(b);});}
document.getElementById('add').onclick=()=>{const tx=document.getElementById('txt').value.trim();if(!tx)return;const t=cur().currentTime;
const n={edl:EDL.versions[document.getElementById('ver').selectedIndex]||EDL.name,category:document.getElementById('cat').value,priority:document.getElementById('pri').value,text:tx};
if(start!==null&&t>start){n.from=+start.toFixed(3);n.to=+t.toFixed(3);}else{n.at=+t.toFixed(3);}notes.push(n);start=null;document.getElementById('range').textContent='';document.getElementById('txt').value='';render();};
function seek(t){cur().currentTime=t;cur().play();}
document.getElementById('export').onclick=()=>{const c=document.querySelector('input[name=choice]:checked');
const doc={schema:'videocli.notes/v1',edl:EDL.name,approved:document.getElementById('ok').checked,choice:c?c.value:null,notes:notes};
const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(doc,null,2)],{type:'application/json'}));a.download='notes.json';a.click();};
</script></body></html>
"""


def build(ws: Workspace, edl: EDL, out_dir: Path, settings: Any, variants: list[EDL] | None = None, seed: int | None = None) -> dict[str, Any]:
    from videocli.render.run import render

    out_dir.mkdir(parents=True, exist_ok=True)
    versions = [edl] + (variants or [])
    labels = [chr(ord("A") + i) for i in range(len(versions))]
    order = list(range(len(versions)))
    if variants:
        random.Random(seed).shuffle(order)  # blind: the page never shows which edit is which
    players, opts = [], []
    key = {}
    for slot, idx in enumerate(order):
        v = versions[idx]
        fname = f"version-{labels[slot]}.mp4" if variants else "preview.mp4"
        render(ws, v, out_dir / fname, preset_name="preview", settings=settings, jobs=int(settings.jobs))
        players.append(f'<div><h3>{"Version " + labels[slot] if variants else html.escape(v.name)}</h3><video controls preload="metadata" src="{fname}"></video></div>')
        opts.append(f"<option>{labels[slot] if variants else html.escape(v.name)}</option>")
        key[labels[slot]] = v.name
    tm = edl.timemap()
    rows = [f'<tr><td><a href="#" onclick="seek({sp.out_end:.3f});return false">{sp.out_end:.2f}s</a></td><td>cut {sp.id} → {nxt.id}</td></tr>'
            for sp, nxt in zip(tm.spans, tm.spans[1:])] if not variants else []
    rows += [f'<tr><td><a href="#" onclick="seek({m.at:.3f});return false">{m.at:.2f}s</a></td><td>{html.escape(m.kind)}: {html.escape(m.text)}</td></tr>'
             for m in edl.markers]
    choice = ""
    if variants:
        choice = "Prefer: " + " ".join(f'<label><input type="radio" name="choice" value="{lab}">{lab}</label>' for lab in labels) + \
                 ' <label><input type="radio" name="choice" value="none">no preference</label>'
    page = (PAGE.replace("__TITLE__", html.escape(edl.name if not variants else "Which version works better?"))
            .replace("__PLAYERS__", "".join(players)).replace("__VEROPTS__", "".join(opts)).replace("__CUTS__", "".join(rows) or "<tr><td class=mut>—</td></tr>")
            .replace("__CHOICE__", choice)
            .replace("__EDL__", json.dumps({"name": edl.name, "versions": [key[lab] for lab in labels] if not variants else [edl.name] * len(labels)})))
    (out_dir / "index.html").write_text(page, encoding="utf-8")
    if variants:
        write_json(out_dir.parent / f".{out_dir.name}-key.json", {"labels": key})  # kept outside the page folder
    return {"dir": str(out_dir), "page": str(out_dir / "index.html"), "versions": len(versions), "blind": bool(variants)}


def clean(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
