"""Generate a self-contained, offline HTML viewer for the library.

`render(records, title)` returns one static HTML file with the catalog embedded as
JSON (so it works by double-click from file:// — no server, no fetch/CORS) and a
small vanilla-JS single-page app: live search, sort, tag filtering, expandable
abstracts, and links to each paper's local PDF + DOI/arXiv. No external/CDN deps.
"""

from __future__ import annotations

import json
from typing import Any

# Fields the viewer needs (keeps the embedded payload lean).
VIEW_FIELDS = (
    "citekey", "title", "authors_text", "year", "venue", "doi", "arxiv_id",
    "pmid", "pmcid", "tags", "file_path", "abstract", "content_state", "source",
)

_CSS = """
:root{--bg:#fafafa;--card:#fff;--ink:#1a1a1a;--mut:#666;--line:#e5e5e5;--accent:#2b6cb0;--chip:#eef2f7}
*{box-sizing:border-box}
body{margin:0;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg)}
.app{display:flex;align-items:flex-start}
.sidebar{width:255px;flex:none;position:sticky;top:0;height:100vh;overflow:auto;border-right:1px solid var(--line);padding:14px 14px 40px;background:#fff}
.facet{margin-bottom:16px}
.facet h3{margin:0 0 6px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}
.facet ul{list-style:none;margin:0;padding:0;max-height:230px;overflow:auto}
.facet li{display:flex;justify-content:space-between;gap:8px;padding:3px 7px;border-radius:6px;cursor:pointer;font-size:13px}
.facet li:hover{background:var(--chip)}
.facet li.active{background:var(--accent);color:#fff}
.facet li .v{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.facet li .c{color:var(--mut);font-variant-numeric:tabular-nums}
.facet li.active .c{color:#dbe7f5}
.facet li.none{color:var(--mut);cursor:default}
.main{flex:1;min-width:0}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:16px 20px;z-index:5}
h1{margin:0 0 6px;font-size:20px}
.stats{color:var(--mut);font-size:13px;margin-bottom:10px}
#q{width:100%;padding:11px 13px;font-size:16px;border:1px solid var(--line);border-radius:8px}
.controls{margin-top:8px;display:flex;gap:10px;align-items:center;font-size:13px;color:var(--mut);flex-wrap:wrap}
select{font:inherit;padding:3px 6px}
#active{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.active-filter,#clearAll{font:inherit;font-size:12px;border:1px solid var(--line);background:var(--chip);border-radius:999px;padding:2px 9px;cursor:pointer}
.active-filter{color:#2b3a4a}
#clearAll{color:var(--accent)}
.list{max-width:900px;padding:16px 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:0 0 10px;cursor:pointer}
.card:hover{border-color:#cfd8e3}
.title{font-weight:600}
.meta{color:var(--mut);font-size:13px;margin-top:2px}
.row{margin-top:8px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:13px}
.row a{color:var(--accent);text-decoration:none;border:1px solid var(--line);border-radius:6px;padding:1px 8px}
.row a:hover{background:var(--chip)}
.nofile{color:var(--mut);font-style:italic}
.tags{margin-left:auto;display:flex;gap:6px;flex-wrap:wrap}
.tag{font:inherit;font-size:12px;color:#3a4a5a;background:var(--chip);border:0;border-radius:999px;padding:2px 9px;cursor:pointer}
.tag:hover{background:#dde6f0}
.abstract{display:none;margin-top:10px;padding-top:10px;border-top:1px dashed var(--line);color:#333;font-size:14px}
.card.expanded .abstract{display:block}
.empty{color:var(--mut);text-align:center;padding:40px}
@media (max-width:760px){.app{flex-direction:column}.sidebar{width:100%;height:auto;position:static;border-right:0;border-bottom:1px solid var(--line)}.facet ul{max-height:150px}}
"""

_JS = r"""
const PAPERS = window.PAPERS || [];
const $ = s => document.querySelector(s);
const esc = s => { const d=document.createElement("div"); d.textContent = s==null?"":String(s); return d.innerHTML; };
const auth = p => p.authors_text || "";
// every author on the paper (primary + co-authors), each as "Family, Given"
const allAuthors = p => (p.authors_text || "").split(";").map(s=>s.trim()).filter(Boolean);
const tagsWith = (p,pre) => (p.tags||[]).filter(t=>t.startsWith(pre)).map(t=>t.slice(pre.length));

// Facets: each maps a paper to the value(s) it contributes to that group.
const FACETS = [
  {key:"author", label:"Authors",      get:allAuthors},
  {key:"topic",  label:"Topics",       get:p=>tagsWith(p,"topic:")},
  {key:"type",   label:"Types",        get:p=>tagsWith(p,"type:")},
  {key:"venue",  label:"Publications", get:p=>p.venue?[p.venue]:[]},
  {key:"year",   label:"Years",        get:p=>p.year?[String(p.year)]:[]},
];
const state = {q:"", sort:"year-desc", sel:{}};

function matches(p,q){
  if(!q) return true;
  const hay = [p.title,p.authors_text,p.venue,(p.tags||[]).join(" "),p.year,p.doi,p.citekey,p.abstract].join(" ").toLowerCase();
  return q.toLowerCase().split(/\s+/).filter(Boolean).every(t => hay.includes(t));
}
function passes(p, exceptKey){
  return FACETS.every(f => {
    const sel = state.sel[f.key];
    if(!sel || f.key===exceptKey) return true;
    return f.get(p).includes(sel);
  });
}
// papers matching the search + every active facet except `exceptKey` (for facet counts)
function filtered(exceptKey){ return PAPERS.filter(p=>matches(p,state.q) && passes(p,exceptKey)); }
function cmp(a,b){
  if(state.sort==="year-asc") return (a.year||0)-(b.year||0) || (a.title||"").localeCompare(b.title||"");
  if(state.sort==="author")   return auth(a).localeCompare(auth(b)) || (b.year||0)-(a.year||0);
  if(state.sort==="title")    return (a.title||"").localeCompare(b.title||"");
  return (b.year||0)-(a.year||0) || (a.title||"").localeCompare(b.title||"");
}
function card(p){
  const ids=[];
  if(p.doi) ids.push(`<a href="https://doi.org/${encodeURIComponent(p.doi)}" target="_blank" rel="noopener">DOI</a>`);
  if(p.arxiv_id) ids.push(`<a href="https://arxiv.org/abs/${encodeURIComponent(p.arxiv_id)}" target="_blank" rel="noopener">arXiv</a>`);
  const pdf = p.file_path
    ? `<a class="pdf" href="${encodeURI(p.file_path)}" target="_blank" rel="noopener">PDF</a>`
    : `<span class="nofile">citation only</span>`;
  const tags=(p.tags||[]).map(t=>`<button class="tag" data-tag="${esc(t)}">${esc(t)}</button>`).join("");
  const meta=[auth(p),p.year,p.venue].filter(Boolean).map(esc).join(" · ");
  const abs=p.abstract?`<div class="abstract">${esc(p.abstract)}</div>`:"";
  return `<article class="card">
    <div class="title">${esc(p.title||"(untitled)")}</div>
    <div class="meta">${meta}</div>
    <div class="row">${pdf} ${ids.join(" ")}<span class="tags">${tags}</span></div>${abs}
  </article>`;
}
function sidebar(){
  return FACETS.map(f=>{
    const counts=new Map();
    for(const p of filtered(f.key)) for(const v of f.get(p)) counts.set(v,(counts.get(v)||0)+1);
    let vals=[...counts.entries()];
    if(f.key==="year") vals.sort((a,b)=>b[0]-a[0]);               // years: newest first
    else vals.sort((a,b)=> a[0].localeCompare(b[0], undefined, {sensitivity:"base"}));  // names: A→Z
    const rows = vals.map(([v,c])=>{
      const on = state.sel[f.key]===v;
      return `<li class="${on?'active':''}" data-facet="${f.key}" data-val="${esc(v)}"><span class="v">${esc(v)}</span><span class="c">${c}</span></li>`;
    }).join("") || `<li class="none">—</li>`;
    return `<section class="facet"><h3>${f.label}</h3><ul>${rows}</ul></section>`;
  }).join("");
}
function activeBar(){
  const chips = FACETS.filter(f=>state.sel[f.key])
    .map(f=>`<button class="active-filter" data-clear="${f.key}">${f.label}: ${esc(state.sel[f.key])} ×</button>`);
  const any = chips.length || state.q;
  $("#active").innerHTML = chips.join("") + (any ? `<button id="clearAll">clear all</button>` : "");
}
function render(){
  const items = filtered(null).sort(cmp);
  $("#stats").textContent = `${items.length} of ${PAPERS.length} papers`;
  $("#list").innerHTML = items.length ? items.map(card).join("") : `<p class="empty">No matches.</p>`;
  $("#sidebar").innerHTML = sidebar();
  activeBar();
}
$("#q").addEventListener("input", e=>{state.q=e.target.value; render();});
$("#sort").addEventListener("change", e=>{state.sort=e.target.value; render();});
$("#sidebar").addEventListener("click", e=>{
  const li=e.target.closest("li[data-facet]"); if(!li) return;
  const k=li.dataset.facet, v=li.dataset.val;
  state.sel[k] = (state.sel[k]===v) ? null : v;   // toggle
  render();
});
document.addEventListener("click", e=>{
  if(e.target.dataset && e.target.dataset.clear){ state.sel[e.target.dataset.clear]=null; render(); return; }
  if(e.target.id==="clearAll"){ state.sel={}; state.q=""; $("#q").value=""; render(); return; }
  const tag=e.target.closest(".tag");
  if(tag){ const t=tag.dataset.tag;
    if(t.startsWith("topic:")) state.sel.topic=t.slice(6);
    else if(t.startsWith("type:")) state.sel.type=t.slice(5);
    window.scrollTo(0,0); render(); return; }
  const c=e.target.closest(".card");
  if(c && !e.target.closest("a") && !e.target.closest(".tag")) c.classList.toggle("expanded");
});
render();
"""

_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<div class="app">
  <aside class="sidebar" id="sidebar"></aside>
  <div class="main">
    <header>
      <h1>{title}</h1>
      <div class="stats" id="stats"></div>
      <input id="q" type="search" placeholder="Search title, author, venue, tag, year…" autofocus>
      <div class="controls">
        <label>Sort
          <select id="sort">
            <option value="year-desc">Year (newest)</option>
            <option value="year-asc">Year (oldest)</option>
            <option value="author">Author A–Z</option>
            <option value="title">Title A–Z</option>
          </select>
        </label>
        <span id="active"></span>
      </div>
    </header>
    <div class="list" id="list"></div>
  </div>
</div>
<script>window.PAPERS = {data};</script>
<script>{js}</script>
</body>
</html>
"""


def view_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Slim a full record down to the fields the viewer displays."""
    return {k: rec.get(k) for k in VIEW_FIELDS if rec.get(k) not in (None, "", [])}


def render(records: list[dict[str, Any]], title: str = "Bibliography") -> str:
    """Build the self-contained viewer HTML for a list of records.

    Deterministic: records are sorted by citekey and no timestamp is embedded, so
    regenerating an unchanged library yields an identical file (no needless churn).
    """
    rows = sorted((view_record(r) for r in records), key=lambda r: r.get("citekey") or "")
    # Embed JSON safely inside a <script> (escape the only sequence that can break out).
    data = json.dumps(rows, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    esc_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _HTML.format(title=esc_title, css=_CSS, js=_JS, data=data)
