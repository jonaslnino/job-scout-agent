"""
Rate the jobs on a page instead of editing the CSV by hand.

    python review.py            # opens http://127.0.0.1:8765 in your browser
    python review.py --port 9000 --no-browser

Each job shows the agent's recommendation and reasons, with three buttons: High (apply now),
Medium (maybe) and Pass, plus a box for a few words on why. Every click is saved straight into
output/tracker.csv, in the same columns learn.py and measure.py read. Stop it with Ctrl+C.

It only listens on your own machine (127.0.0.1) and uses nothing outside Python's standard library.
"""

import argparse
import csv
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import output

sys.stdout.reconfigure(encoding="utf-8")
LOCK = threading.Lock()
EDITABLE = ("my_verdict", "my_note", "status")
VERDICTS = {"", "High", "Medium", "Pass"}


def load() -> tuple[list[str], list[dict]]:
    if not output.TRACKER_PATH.exists():
        return output.COLUMNS, []
    with output.TRACKER_PATH.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or output.COLUMNS), list(reader)


def save(row_id: int, link: str, changes: dict) -> str:
    """Updates one row, checked by its link so a run that appended rows meanwhile can't misplace it."""
    with LOCK:
        fields, rows = load()
        if not 0 <= row_id < len(rows) or rows[row_id].get("link") != link:
            return "The tracker changed since the page loaded. Reload the page."
        for k in EDITABLE:
            if k in changes:
                rows[row_id][k] = str(changes[k])[:500]
        for k in EDITABLE:
            if k not in fields:
                fields.append(k)
        tmp = output.TRACKER_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, output.TRACKER_PATH)
    return ""


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rate your jobs · Job Scout</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--muted:#6b6b66;--line:#e3e1db;--high:#1f7a4d;--med:#b7791f;--pass:#9b3b3b}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#21211f;--ink:#ecebe6;--muted:#9c9b95;--line:#34342f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:14px 20px;display:flex;gap:16px;align-items:center;flex-wrap:wrap;z-index:1}
h1{font-size:18px;margin:0}#count{color:var(--muted)}main{max-width:820px;margin:0 auto;padding:20px}
.tabs button{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:5px 12px;border-radius:999px;cursor:pointer}
.tabs button.on{background:var(--ink);color:var(--bg)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-bottom:16px}
.card.done{opacity:.72}.top{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
.title{font-weight:650;font-size:16px}.title a{color:inherit}.meta{color:var(--muted);font-size:13px;margin:4px 0 10px}
.agent{font-size:13px;white-space:nowrap;color:var(--muted)}.agent b{color:var(--ink)}
dl{margin:8px 0 12px;display:grid;grid-template-columns:110px 1fr;gap:4px 12px;font-size:14px}dt{color:var(--muted)}dd{margin:0}
.summary{font-size:14px;margin:0 0 6px}.rate{display:flex;gap:8px;flex-wrap:wrap;align-items:center;border-top:1px solid var(--line);padding-top:12px}
.v{border:1.5px solid var(--line);background:transparent;color:var(--ink);padding:6px 14px;border-radius:8px;cursor:pointer;font-weight:600}
.v[data-v=High].on{background:var(--high);border-color:var(--high);color:#fff}.v[data-v=Medium].on{background:var(--med);border-color:var(--med);color:#fff}
.v[data-v=Pass].on{background:var(--pass);border-color:var(--pass);color:#fff}
input.note{flex:1;min-width:200px;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:transparent;color:var(--ink)}
select{padding:6px;border:1px solid var(--line);border-radius:8px;background:transparent;color:var(--ink)}
.saved{font-size:12px;color:var(--muted);min-width:46px}.empty{color:var(--muted);text-align:center;padding:40px}
.hint{background:var(--card);border:1px dashed var(--line);border-radius:12px;padding:14px 18px;margin-bottom:16px;font-size:14px}
</style></head><body>
<header><h1>Rate your jobs</h1><span id="count"></span>
<span class="tabs"><button data-f="todo" class="on">To rate</button> <button data-f="all">All</button></span></header>
<main><div class="hint">High = apply now · Medium = maybe · Pass = not for you. A few words on <i>why</i> teach the agent
the most. Everything saves as you go. When you're done, say <b>"learn from my ratings"</b> in Claude Code, or run
<code>python learn.py</code>.</div><div id="list"></div></main>
<script>
let rows=[], filter="todo";
const el=(t,c,txt)=>{const e=document.createElement(t);if(c)e.className=c;if(txt!=null)e.textContent=txt;return e};
async function load(){rows=await (await fetch("/api/rows")).json();render()}
function render(){
  const list=document.getElementById("list");list.replaceChildren();
  const todo=rows.filter(r=>!r.my_verdict).length;
  document.getElementById("count").textContent=`${todo} to rate · ${rows.length} in total`;
  const shown=rows.filter(r=>filter==="all"||!r.my_verdict);
  if(!shown.length){list.append(el("div","empty",rows.length?"All rated. Nice.":"No jobs yet. Run the agent first."));return}
  for(const r of shown){
    const c=el("div","card"+(r.my_verdict?" done":""));
    const top=el("div","top"),t=el("div","title"),a=el("a",null,`${r.title} · ${r.company}`);
    a.href=r.link;a.target="_blank";a.rel="noopener";t.append(a);
    const ag=el("div","agent");ag.append("Agent: ",el("b",null,r.recommendation||"?"),` · fit ${r.fit} · interest ${r.interest}`);
    top.append(t,ag);c.append(top);
    c.append(el("div","meta",[r.location,r.work_mode,r.salary,r.source,r.date_found].filter(Boolean).join(" · ")));
    if(r.summary)c.append(el("p","summary",r.summary));
    const dl=el("dl");
    for(const[k,l]of[["why","Why"],["fit_notes","Fit"],["gaps","Gaps"],["level","Level"],["tradeoff","Tradeoff"]])
      if(r[k]){dl.append(el("dt",null,l),el("dd",null,r[k]))}
    c.append(dl);
    const rate=el("div","rate"),saved=el("span","saved");
    for(const v of["High","Medium","Pass"]){const b=el("button","v"+(r.my_verdict===v?" on":""),v);b.dataset.v=v;
      b.onclick=()=>{r.my_verdict=r.my_verdict===v?"":v;send(r,saved).then(render)};rate.append(b)}
    const n=el("input","note");n.placeholder="Why? (e.g. too junior, love the mandate, on-site only)";n.value=r.my_note||"";
    n.onchange=()=>{r.my_note=n.value;send(r,saved)};
    const s=el("select");for(const o of["to review","applied","not applying"]){const op=el("option",null,o);op.value=o;s.append(op)}
    if(r.status&&![...s.options].some(o=>o.value===r.status)){const op=el("option",null,r.status);op.value=r.status;s.append(op)}
    s.value=r.status||"to review";s.onchange=()=>{r.status=s.value;send(r,saved)};
    rate.append(n,s,saved);c.append(rate);list.append(c);
  }
}
async function send(r,saved){
  const res=await fetch("/api/save",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({id:r._id,link:r.link,my_verdict:r.my_verdict,my_note:r.my_note,status:r.status})});
  const j=await res.json();saved.textContent=j.error?"":"saved";if(j.error)alert(j.error);
  setTimeout(()=>saved.textContent="",1500);
}
document.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll(".tabs button").forEach(x=>x.classList.remove("on"));b.classList.add("on");filter=b.dataset.f;render()});
load();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the terminal quiet
        pass

    def _send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/rows":
            _, rows = load()
            data = [dict(r, _id=i) for i, r in enumerate(rows)]
            data.sort(key=lambda r: (bool(r.get("my_verdict")),
                                     {"High": 0, "Medium": 1, "Pass": 2}.get(r.get("recommendation"), 3),
                                     -int(r.get("interest") or 0) if str(r.get("interest", "")).isdigit() else 0))
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path != "/api/save":
            return self._send(404, b"not found", "text/plain")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if body.get("my_verdict", "") not in VERDICTS:
                raise ValueError("verdict must be High, Medium or Pass")
            error = save(int(body["id"]), body.get("link", ""), body)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        self._send(200, json.dumps({"error": error}).encode("utf-8"), "application/json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rate your jobs in the browser")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    a = parser.parse_args()
    _, rows = load()
    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}"
    todo = sum(1 for r in rows if not r.get("my_verdict"))
    print(f"Rate your jobs at {url}  ({todo} to rate, {len(rows)} in total). Ctrl+C to stop.", flush=True)
    if not a.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Your ratings are in output/tracker.csv.")


if __name__ == "__main__":
    main()
