"""A self-contained HTML viewer for a regulator library.

Renders the whole catalog into a single ``index.html`` with client-side filter +
free-text search, grouped by ``doc_type``. No server, no build step — open the
file in a browser. Regenerated on demand by ``reg viewer``.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

_TYPE_LABELS = {
    "guidance": "Guidance",
    "drugsfda": "Drugs@FDA",
    "adcomm": "Advisory Committee",
    "personnel": "Personnel",
}


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def render(records: list[dict[str, Any]]) -> str:
    rows = []
    for r in records:
        dt = r.get("doc_type") or "misc"
        title = r.get("title") or "(untitled)"
        url = r.get("source_url") or ""
        meta_bits = [
            r.get("application_number"), r.get("sponsor_name"), r.get("brand_name"),
            r.get("review_type"), r.get("fda_org"), r.get("status"), r.get("topic"),
            r.get("committee_abbr"), r.get("material_type"), r.get("meeting_date"),
            r.get("role"), r.get("division"),
        ]
        meta = " · ".join(_esc(b) for b in meta_bits if b)
        link = f'<a href="{_esc(url)}" target="_blank">{_esc(title)}</a>' if url else _esc(title)
        state = r.get("content_state") or ""
        rows.append(
            f'<tr data-type="{_esc(dt)}" data-search="{_esc((title + " " + meta).lower())}">'
            f'<td class="ck">{_esc(r.get("citekey"))}</td>'
            f'<td class="ty">{_esc(_TYPE_LABELS.get(dt, dt))}</td>'
            f'<td>{link}<div class="m">{meta}</div></td>'
            f'<td class="st">{_esc(state)}</td></tr>'
        )
    counts = {}
    for r in records:
        counts[r.get("doc_type") or "misc"] = counts.get(r.get("doc_type") or "misc", 0) + 1
    chips = " ".join(
        f'<button class="chip" data-f="{_esc(k)}">{_esc(_TYPE_LABELS.get(k, k))} ({v})</button>'
        for k, v in sorted(counts.items())
    )
    return _TEMPLATE.replace("__COUNT__", str(len(records))).replace("__CHIPS__", chips).replace(
        "__ROWS__", "\n".join(rows)
    ).replace("__DATA__", json.dumps({"n": len(records)}))


_TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>regulator — FDA regulatory library</title>
<style>
:root{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
body{margin:0;padding:1.2rem 1.4rem;color:#1a1a1a;background:#fafafa}
h1{font-size:1.1rem;margin:0 0 .2rem} .sub{color:#666;font-size:.85rem;margin-bottom:.8rem}
#q{width:100%;padding:.55rem .7rem;font-size:.95rem;border:1px solid #ccc;border-radius:8px;margin-bottom:.6rem}
.chip{border:1px solid #ccc;background:#fff;border-radius:14px;padding:.2rem .6rem;margin:0 .3rem .4rem 0;cursor:pointer;font-size:.8rem}
.chip.on{background:#0b5;color:#fff;border-color:#0b5}
table{width:100%;border-collapse:collapse;font-size:.88rem;background:#fff;border-radius:8px;overflow:hidden}
td{padding:.45rem .6rem;border-top:1px solid #eee;vertical-align:top}
.ck{font-family:ui-monospace,monospace;color:#777;font-size:.78rem;white-space:nowrap}
.ty{white-space:nowrap;color:#0a6;font-size:.8rem} .st{color:#a60;font-size:.78rem}
.m{color:#777;font-size:.78rem;margin-top:.15rem} a{color:#06c;text-decoration:none} a:hover{text-decoration:underline}
</style></head><body>
<h1>regulator</h1><div class="sub">__COUNT__ FDA regulatory documents</div>
<input id="q" placeholder="filter by title / sponsor / org / topic…" autofocus>
<div id="chips">__CHIPS__</div>
<table><tbody id="tb">__ROWS__</tbody></table>
<script>
const q=document.getElementById('q'),tb=document.getElementById('tb');let filt=null;
function apply(){const s=q.value.toLowerCase().split(/\\s+/).filter(Boolean);
 for(const tr of tb.rows){const hay=tr.dataset.search,ty=tr.dataset.type;
  const okT=!filt||ty===filt, okS=s.every(t=>hay.includes(t));tr.style.display=(okT&&okS)?'':'none';}}
q.addEventListener('input',apply);
for(const c of document.querySelectorAll('.chip')){c.onclick=()=>{
 if(filt===c.dataset.f){filt=null;c.classList.remove('on');}
 else{filt=c.dataset.f;document.querySelectorAll('.chip').forEach(x=>x.classList.remove('on'));c.classList.add('on');}apply();};}
</script></body></html>"""


def write(home: Path, records: list[dict[str, Any]]) -> Path:
    out = home / "index.html"
    out.write_text(render(records), encoding="utf-8")
    return out
