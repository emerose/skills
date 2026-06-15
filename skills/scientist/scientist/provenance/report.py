"""The report phase — ``sci report`` (build / audit / render).

A *report* is the terminal phase of the pipeline ``raw → data → analysis → claims →
report``. Where a **claim** is one machine-checkable assertion, a **report** is a
human-facing narrative built *from* claims: it collects grounded claims (often fanning
in across experiments), arranges them into an argument, and embeds figures/tables to
make a point. It is for humans — readable, concise, compelling — but holds the same
grounding discipline as the rest of the pipeline:

    no quantitative prose without a backing,

where the backing is an *existing* grounded ``kind=claim`` (or a sha-pinned analysis
artifact). To assert something new the author writes the claim first; reports never
re-litigate grounding.

## What this module mechanizes (and what it doesn't)

Reports are git-diffable Markdown carrying inline ``[claim:<id>]`` citations — the SAME
syntax §3 (the prose↔claims check) defined for ``README.md`` / ``reports/*.md``. ``<id>``
is the stable ``claim_id`` (``<exp>::<test-file>::<node>``) or its trailing node name.
Figures/tables are embedded with Markdown image syntax ``![caption](path)``.

This module does the **mechanical** half of ``sci report``:

* **parse** the report for ``[claim:<id>]`` citations and ``![..](..)`` embeds;
* **validate citations** — each must resolve to a *live, grounded* claim in some
  experiment's ``grounding_report.json`` (the same source §3 / ``sci trace`` use). The
  grounded rule (``outcome ∈ {passed, xpass}`` AND ``strength ∈ {strong, moderate}``)
  decides ``backed`` vs ``weak-backing`` (surfacing the claim's outcome+strength); an
  unresolvable id is ``missing`` and an ambiguous short id is ``ambiguous`` — both fail
  the audit exactly as ``sci trace`` flags a broken chain;
* **validate embeds** — each embedded figure/table must be a *current* sha-pinned
  ``analysis/`` artifact recorded in some experiment's (or the program's) ledger: a
  drifted, missing, or untracked (ad-hoc) graphic fails;
* **render** — assemble a self-contained Markdown (citations → footnoted references,
  ``*.csv`` table embeds inlined as Markdown tables, figure paths absolutised) that a
  toolchain (pandoc) turns into the primary deliverable, a PDF.

The **semantic** judgment — "is every quantitative sentence actually cited / on-topic /
not over-reaching" — stays the §3 semantic-pass discipline of the authoring agent, NOT a
regex assertion-detector. ``sci report`` mechanizes citation + artifact resolution and
render; it does not reintroduce prose assertion-detection.

Stdlib + PyYAML (pandas only for ``*.csv`` table inlining); pure, store-free — like
:mod:`provenance.trace` / :mod:`provenance.reproduce`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import _load_raw, edges, sha256_file

GROUNDING_REPORT_NAME = "grounding_report.json"

# A claim is *grounded* (a valid backing) only when its outcome is a clean pass AND its
# strength is at least moderate — the identical rule §3 / index-claims / sci trace apply.
GROUNDED_OUTCOMES = {"passed", "xpass"}
GROUNDED_STRENGTHS = {"strong", "moderate"}

# Inline citation: [claim:<id>]. <id> may be a full claim_id (a::b::c), a <file>::<node>
# pair, or a bare node name — optionally parametrized, e.g. test_x[100]. Allow one level
# of square brackets inside so a parametrized nodeid survives the match.
_CITE_RE = re.compile(r"\[claim:\s*([^\[\]]+(?:\[[^\]]*\])?)\s*\]")
# Markdown image embed: ![alt](target "optional title"). Captures the target path.
_EMBED_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
# An experiment folder id prefix (K1-YYMMXX …), to derive an exp_id from a folder name.
_EXP_ID_RE = re.compile(r"^\s*(K1-[A-Za-z0-9]+)")

# --------------------------------------------------------------------------- #
# claim_id formatting (kept in sync with store._meta.claim_id_for — replicated
# here so the provenance layer stays store-free, like trace/reproduce)
# --------------------------------------------------------------------------- #
def claim_id_for(exp_id: str, nodeid: str) -> str:
    """A STABLE logical key for a claim: ``<exp_id>::<test-file basename>::<node>``.

    Mirrors :func:`scientist.store._meta.claim_id_for` exactly (a test asserts they agree)
    — replicated here so :mod:`provenance` need not import the store package."""
    head, sep, rest = nodeid.partition("::")
    basename = head.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or head
    node = rest if sep else ""
    parts = [p for p in (exp_id, basename, node) if p]
    return "::".join(parts)


def _exp_id_for_dir(folder: Path) -> str:
    """The exp_id for an experiment folder (its ``K1-…`` prefix), or the bare folder name
    (so ``program`` claims key as ``program::…``)."""
    m = _EXP_ID_RE.match(folder.name)
    return m.group(1) if m else folder.name


def _short_claim_id(claim_id: str) -> str:
    """A compact display form of a ``<exp>::<test-file>::<node>`` claim id for an endnote
    citation: drop the test-file component and the ``test_`` node prefix, leaving
    ``<exp>::<node>`` (e.g. ``program::lead_is_deepest_protein_knockdown``). Display only —
    the report source still cites the full, unambiguous id."""
    parts = claim_id.split("::")
    exp, node = parts[0], parts[-1]
    if node.startswith("test_"):
        node = node[len("test_"):]
    return f"{exp}::{node}" if node else exp


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def parse_report(text: str) -> dict[str, list[dict[str, Any]]]:
    """Pull ``[claim:<id>]`` citations and ``![..](target)`` embeds out of report
    Markdown, each with its 1-based line number. Citations/embeds inside fenced code
    blocks (```` ``` ````) are skipped so an example in a code block isn't audited.

    Returns ``{"citations": [{id, line}], "embeds": [{target, line}]}``.
    """
    citations: list[dict[str, Any]] = []
    embeds: list[dict[str, Any]] = []
    in_fence = False
    fence_marker = ""
    for n, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif stripped.startswith(fence_marker):
                in_fence, fence_marker = False, ""
            continue
        if in_fence:
            continue
        for m in _CITE_RE.finditer(line):
            citations.append({"id": m.group(1).strip(), "line": n})
        for m in _EMBED_RE.finditer(line):
            embeds.append({"target": m.group(1).strip(), "line": n})
    return {"citations": citations, "embeds": embeds}


def parse_sections(text: str) -> dict[str, Any]:
    """Mechanically split a report into ``{title, abstract, sections}`` for indexing.

    * ``title`` — the first ``# H1`` (or the leading non-blank line);
    * ``abstract`` — the first prose paragraph after the title, OR the body of a section
      titled *Abstract* / *Summary* if one exists;
    * ``sections`` — ``[{heading, summary}]`` for each ``##``/``###`` heading, ``summary``
      being that section's first non-blank, non-heading line (citations/embeds stripped).
    """
    lines = text.splitlines()
    title = ""
    sections: list[dict[str, str]] = []
    cur: dict[str, Any] | None = None
    abstract = ""
    para: list[str] = []
    saw_title = False

    def _clean(s: str) -> str:
        s = _CITE_RE.sub("", s)
        s = _EMBED_RE.sub("", s)
        return s.strip()

    for raw in lines:
        line = raw.rstrip()
        h1 = re.match(r"^#\s+(.*)$", line)
        h2 = re.match(r"^#{2,3}\s+(.*)$", line)
        if h1 and not title:
            title = h1.group(1).strip()
            saw_title = True
            continue
        if h2:
            if cur is not None and not cur["summary"]:
                cur["summary"] = _clean(" ".join(para))
            cur = {"heading": h2.group(1).strip(), "summary": ""}
            sections.append(cur)
            para = []
            continue
        body = _clean(line)
        if not body:
            # paragraph boundary: capture the first real paragraph as the abstract
            if saw_title and not abstract and cur is None and para:
                abstract = " ".join(para).strip()
            if cur is not None and not cur["summary"] and para:
                cur["summary"] = " ".join(para).strip()
                para = []
            elif cur is None:
                para = []
            continue
        para.append(body)
    if cur is not None and not cur["summary"] and para:
        cur["summary"] = " ".join(para).strip()
    if saw_title and not abstract:
        # no blank-line-terminated lead paragraph; fall back to the running buffer
        if cur is None and para:
            abstract = " ".join(para).strip()
    # An explicit Abstract/Summary section wins.
    for s in sections:
        if s["heading"].lower() in ("abstract", "summary") and s["summary"]:
            abstract = s["summary"]
            break
    return {"title": title, "abstract": abstract, "sections": sections}


# --------------------------------------------------------------------------- #
# claim + artifact indexes (across every experiment under the data root)
# --------------------------------------------------------------------------- #
def _grounding_reports(home: Path) -> list[tuple[str, Path]]:
    """``(exp_id, grounding_report.json path)`` for every experiment under ``home`` that
    has one (``<child>/analysis/grounding_report.json`` then ``<child>/…``)."""
    out: list[tuple[str, Path]] = []
    if not home.is_dir():
        return out
    for child in sorted(home.iterdir()):
        if not child.is_dir():
            continue
        for cand in (child / "analysis" / GROUNDING_REPORT_NAME, child / GROUNDING_REPORT_NAME):
            if cand.is_file():
                out.append((_exp_id_for_dir(child), cand))
                break
    return out


def index_claims(home: Path) -> dict[str, dict[str, Any]]:
    """Build ``{full_claim_id -> claim}`` across every experiment's grounding report under
    ``home``. ``full_claim_id`` is ``claim_id_for(exp_id, raw_nodeid)`` so it matches
    ``index-claims`` / ``sci query --kind claim``. Each claim carries its ``exp_id`` and
    the experiment folder ``exp_dir`` (for the downstream report-rooted trace)."""
    index: dict[str, dict[str, Any]] = {}
    for exp_id, report_path in _grounding_reports(home):
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        claims = data.get("claims") if isinstance(data, dict) else data
        if not isinstance(claims, list):
            continue
        exp_dir = report_path.parent.parent if report_path.parent.name == "analysis" else report_path.parent
        for c in claims:
            if not isinstance(c, dict):
                continue
            nodeid = c.get("id") or ""
            full = claim_id_for(exp_id, nodeid)
            index[full] = {**c, "exp_id": exp_id, "exp_dir": str(exp_dir), "claim_id": full}
    return index


def resolve_citation(cid: str, index: dict[str, dict[str, Any]]) -> list[str]:
    """Resolve a cited ``<id>`` to matching full claim_ids: exact full-id match wins;
    else a ``<file>::<node>`` suffix or a bare trailing node-name match (which may be
    ambiguous across experiments → caller treats >1 as ``ambiguous``)."""
    if cid in index:
        return [cid]
    tail = cid.split("::")[-1]
    cands = [fid for fid in index
             if fid.endswith("::" + cid) or fid.split("::")[-1] == tail]
    return sorted(set(cands))


def is_grounded(claim: dict[str, Any]) -> bool:
    """The grounded rule: a clean pass at moderate-or-strong evidence."""
    return (str(claim.get("outcome")) in GROUNDED_OUTCOMES
            and str(claim.get("strength")) in GROUNDED_STRENGTHS)


def index_analysis_artifacts(home: Path) -> dict[str, str | None]:
    """``{repo-relative analysis artifact path -> recorded artifact_sha256}`` across every
    experiment's ledger under ``home`` (including ``program/``). The key is how a report's
    embed is matched to the producing edge; the sha lets the audit flag drift."""
    out: dict[str, str | None] = {}
    if not home.is_dir():
        return out
    for child in sorted(home.iterdir()):
        if not child.is_dir():
            continue
        sidecar = _load_raw(child)
        for e in edges(sidecar, "analysis/"):
            art = str(e.get("artifact", ""))
            if not art:
                continue
            rel = f"{child.name}/{art}"
            out[rel] = e.get("artifact_sha256")
    return out


# --------------------------------------------------------------------------- #
# scope
# --------------------------------------------------------------------------- #
def report_scope(report_path: Path, home: Path) -> dict[str, Any]:
    """Classify a report by where it lives: a cross-experiment report under
    ``program/reports/<slug>/`` (``scope='program'``) or a per-experiment summary under
    ``<exp>/reports/<slug>/`` (``scope='experiment'``, with ``exp_id``). ``slug`` is the
    report-folder name; falls back to the file stem."""
    rp = report_path.resolve()
    try:
        rel_parts = rp.relative_to(home.resolve()).parts
    except ValueError:
        rel_parts = rp.parts
    scope, exp_id, slug = "experiment", None, rp.parent.name or rp.stem
    if rel_parts:
        top = rel_parts[0]
        if top == "program":
            scope = "program"
        else:
            m = _EXP_ID_RE.match(top)
            exp_id = m.group(1) if m else top
    if "reports" in rel_parts:
        i = rel_parts.index("reports")
        if i + 1 < len(rel_parts):
            slug = rel_parts[i + 1]
            if slug.endswith(".md"):
                slug = Path(slug).stem
    return {"scope": scope, "exp_id": exp_id, "slug": slug}


# --------------------------------------------------------------------------- #
# audit
# --------------------------------------------------------------------------- #
def audit(report_path: Path, home: Path | None = None) -> dict[str, Any]:
    """Mechanically validate a report's citations + embeds.

    Returns ``{report, scope, exp_id, citations, embeds, findings, status}`` where
    ``status`` is ``GROUNDED`` (no blocking finding) or ``BROKEN``. Each citation verdict
    is ``backed`` / ``weak-backing`` / ``missing`` / ``ambiguous``; each embed verdict is
    ``current`` / ``drifted`` / ``missing`` / ``untracked`` / ``dangling``. Everything but
    ``backed`` / ``current`` is a blocking finding (the *semantic* off-topic check stays
    with the authoring agent — see the module docstring).
    """
    rp = Path(report_path).resolve()
    home = Path(home).resolve() if home is not None else _infer_home(rp)
    text = rp.read_text(encoding="utf-8")
    parsed = parse_report(text)
    sc = report_scope(rp, home)

    claim_index = index_claims(home)
    artifact_index = index_analysis_artifacts(home)

    findings: list[dict[str, Any]] = []

    # ---- citations -------------------------------------------------------- #
    citations: list[dict[str, Any]] = []
    for cit in parsed["citations"]:
        cid, line = cit["id"], cit["line"]
        cands = resolve_citation(cid, claim_index)
        rec: dict[str, Any] = {"id": cid, "line": line}
        if not cands:
            rec["verdict"] = "missing"
            findings.append({"kind": "missing-claim", "line": line, "cite": cid,
                             "detail": "no grounded claim has this id; write the claim first"})
        elif len(cands) > 1:
            rec["verdict"] = "ambiguous"
            rec["candidates"] = cands
            findings.append({"kind": "ambiguous-claim", "line": line, "cite": cid,
                             "detail": f"matches {len(cands)} claims — qualify it: {cands}"})
        else:
            claim = claim_index[cands[0]]
            rec["claim_id"] = cands[0]
            rec["outcome"] = claim.get("outcome")
            rec["strength"] = claim.get("strength")
            rec["statement"] = claim.get("statement")
            if is_grounded(claim):
                rec["verdict"] = "backed"
            else:
                rec["verdict"] = "weak-backing"
                findings.append({"kind": "weak-backing", "line": line, "cite": cands[0],
                                 "outcome": claim.get("outcome"), "strength": claim.get("strength"),
                                 "detail": f"cited claim is {claim.get('outcome')}/"
                                           f"{claim.get('strength')}, not grounded"})
        citations.append(rec)

    # ---- embeds ----------------------------------------------------------- #
    embeds: list[dict[str, Any]] = []
    for emb in parsed["embeds"]:
        target, line = emb["target"], emb["line"]
        rec = {"target": target, "line": line}
        if re.match(r"^[a-z]+://", target):     # remote URL — ungroundable
            rec["verdict"] = "untracked"
            findings.append({"kind": "untracked-embed", "line": line, "embed": target,
                             "detail": "remote/external image; embed a sha-pinned analysis artifact"})
            embeds.append(rec)
            continue
        rel = _repo_rel(rp.parent, target, home)
        rec["rel"] = rel
        recorded_sha = artifact_index.get(rel)
        abs_path = (home / rel)
        if recorded_sha is not None:
            if not abs_path.is_file():
                rec["verdict"] = "missing"
                findings.append({"kind": "missing-embed", "line": line, "embed": rel,
                                 "detail": "recorded analysis artifact absent on disk"})
            elif sha256_file(abs_path) != recorded_sha:
                rec["verdict"] = "drifted"
                findings.append({"kind": "drifted-embed", "line": line, "embed": rel,
                                 "detail": "artifact bytes differ from the recorded sha "
                                           "(re-run the derivation, or re-record)"})
            else:
                rec["verdict"] = "current"
        else:
            # not produced by any analysis edge
            if abs_path.is_file():
                rec["verdict"] = "untracked"
                findings.append({"kind": "untracked-embed", "line": line, "embed": rel,
                                 "detail": "on disk but no analysis edge records it — produce it "
                                           "via a derivation so it is sha-pinned"})
            else:
                rec["verdict"] = "dangling"
                findings.append({"kind": "dangling-embed", "line": line, "embed": rel,
                                 "detail": "not a recorded analysis artifact and not on disk"})
        embeds.append(rec)

    status = "GROUNDED" if not findings else "BROKEN"
    return {
        "report": _rel_or_name(rp, home),
        "scope": sc["scope"],
        "exp_id": sc["exp_id"],
        "slug": sc["slug"],
        "citations": citations,
        "embeds": embeds,
        "findings": findings,
        "status": status,
    }


def _infer_home(report_path: Path) -> Path:
    """Best-effort data-root for a report path: the parent of the top ``program`` or
    ``K1-…`` folder above it; else the report's grandparent."""
    parts = report_path.parts
    for i, p in enumerate(parts):
        if p == "program" or _EXP_ID_RE.match(p):
            return Path(*parts[:i]) if i else report_path.parent
    return report_path.parent.parent if len(report_path.parents) >= 2 else report_path.parent


def _repo_rel(report_dir: Path, target: str, home: Path) -> str:
    """Resolve an embed ``target`` (relative to the report's directory, or absolute) to a
    home-relative POSIX path."""
    p = Path(target)
    ap = p if p.is_absolute() else (report_dir / p)
    try:
        return ap.resolve().relative_to(home.resolve()).as_posix()
    except (ValueError, OSError):
        return ap.as_posix()


def _rel_or_name(path: Path, home: Path) -> str:
    try:
        return path.resolve().relative_to(home.resolve()).as_posix()
    except ValueError:
        return path.name


# --------------------------------------------------------------------------- #
# render — assemble a self-contained Markdown, then (optionally) call pandoc
# --------------------------------------------------------------------------- #
def render_markdown(report_path: Path, home: Path | None = None) -> str:
    """Assemble a self-contained Markdown from the report (pure; no external tools):

    * ``[claim:<id>]`` → a native pandoc **footnote** carrying the cited claim's statement +
      ``[outcome · strength]`` + its ``claim_id``;
    * ``![cap](*.csv)`` → the CSV inlined as a Markdown table (the derived table, embedded);
    * ``![cap](fig)`` → the same image with its path absolutised so pandoc resolves it.

    Citations are footnotes (hyperlinked, auto-numbered); :func:`render` runs the bundled
    ``endnotes.lua`` pandoc filter so they are relocated to a single "Grounding notes"
    endnotes section at the document end — uniformly across PDF / HTML / docx, with no
    LaTeX-package dependency. The result is what the render is produced from."""
    rp = Path(report_path).resolve()
    home = Path(home).resolve() if home is not None else _infer_home(rp)
    text = rp.read_text(encoding="utf-8")
    claim_index = index_claims(home)

    order: list[str] = []
    num: dict[str, int] = {}           # cid -> 1-based index, first-seen order

    def _cite_sub(m: re.Match) -> str:
        cid = m.group(1).strip()
        if cid not in num:
            order.append(cid)
            num[cid] = len(order)
        return f"[^claim-{num[cid]}]"

    def _embed_sub(m: re.Match) -> str:
        path = m.group(1).strip()
        if re.match(r"^[a-z]+://", path):
            return m.group(0)
        ap = Path(path)
        ap = ap if ap.is_absolute() else (rp.parent / ap)
        if ap.suffix.lower() == ".csv" and ap.is_file():
            return _csv_to_md_table(ap)
        # a figure: rewrite to an absolute path so the renderer finds it
        alt_m = re.match(r"!\[([^\]]*)\]", m.group(0))
        alt = alt_m.group(1) if alt_m else ""
        return f"![{alt}]({ap.resolve().as_posix()})"

    # Bind each note marker to the preceding word: drop any whitespace (incl. a soft line
    # wrap) immediately before a citation, so the superscript attaches like a footnote mark
    # rather than drifting onto the next line.
    body = re.sub(r"[^\S\n]*\n?[^\S\n]*" + _CITE_RE.pattern, _cite_sub, text)
    # embeds can span only a line each; substitute per match on the citation-substituted text
    body = _EMBED_RE.sub(_embed_sub, body)

    def _note_text(cid: str) -> str:
        # A true endnote: the claim's statement reads as the note, followed by a compact
        # claim-id citation. No outcome (a cited claim passed by construction) and no
        # strength (low signal in prose); the id is shortened (drop the test-file and the
        # `test_` node prefix) and set in monospace so it reads as a subdued reference.
        cands = resolve_citation(cid, claim_index)
        if len(cands) == 1:
            c = claim_index[cands[0]]
            stmt = (c.get("statement") or "").strip().replace("\n", " ")
            return f"{stmt} `{_short_claim_id(cands[0])}`"
        return f"claim `{cid}` ({'unresolved' if not cands else 'ambiguous'})"

    if order:
        # pandoc footnote definitions; endnotes.lua relocates them to the end on render
        defs = [f"[^claim-{num[cid]}]: {_note_text(cid)}" for cid in order]
        body = body.rstrip() + "\n\n" + "\n".join(defs) + "\n"
    return body


def _csv_to_md_table(path: Path) -> str:
    """Render a derived ``.csv`` as a GitHub-flavoured Markdown table (pipe-escaped)."""
    import csv as _csv

    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(_csv.reader(fh))
    if not rows:
        return f"_(empty table: {path.name})_"

    def esc(v: str) -> str:
        return str(v).replace("|", "\\|").replace("\n", " ").strip()

    header = rows[0]
    out = ["| " + " | ".join(esc(c) for c in header) + " |",
           "| " + " | ".join("---" for _ in header) + " |"]
    for r in rows[1:]:
        cells = (r + [""] * len(header))[:len(header)]
        out.append("| " + " | ".join(esc(c) for c in cells) + " |")
    return "\n".join(out)


# A restrained, modern house style for the PDF target (xelatex). Headings come out
# sans-serif for free from the KOMA `scrartcl` class; the body is a serif via fontspec
# `mainfont`. This header only adds a thin running header (section · page) + tightened
# rules — it touches no colors, so it is order-independent w.r.t. pandoc's hyperref setup.
# A centered single column with conventional margins (Tufte margins were dropped — an
# empty wide margin is wasted space unless it holds sidenotes, which a citation-dense
# report can't use well). Title block, headings, and the running header/footer all come
# out sans-serif; the body is serif. Margins set via -V geometry:margin in render().
_PDF_HEADER_TEX = r"""
% --- modern report style (injected by `sci report`) ---
\usepackage{graphicx}   % layout.lua emits raw \includegraphics, so load it unconditionally
                        % (pandoc only auto-loads graphicx when it still sees an Image)
\usepackage{fancyhdr}
\usepackage{caption}
\captionsetup{font=small,labelfont=bf,justification=raggedright,singlelinecheck=false}
\setkomafont{author}{\normalfont\sffamily}   % subtitle/byline + date in sans, like the title
\setkomafont{date}{\normalfont\sffamily}
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0.4pt}
\renewcommand{\footrulewidth}{0pt}
\fancyhead[L]{\footnotesize\sffamily @@RUNNING_TITLE@@}
\fancyhead[R]{\footnotesize\sffamily @@HEAD_RIGHT@@}
\fancyfoot[L]{\scriptsize\sffamily @@FOOT_LEFT@@}
\fancyfoot[R]{\footnotesize\sffamily \thepage}
\setlength{\headheight}{14pt}
"""


def _front_field(text: str, key: str) -> str:
    """A scalar YAML front-matter field (empty if absent). Front matter is a leading
    ``---``-fenced block."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return ""
    for line in m.group(1).splitlines():
        mt = re.match(rf"\s*{re.escape(key)}\s*:\s*(.+?)\s*$", line)
        if mt:
            return mt.group(1).strip().strip("\"'")
    return ""


def _report_title(text: str) -> str:
    """The report's YAML front-matter ``title`` — used for the running header."""
    return _front_field(text, "title")


def _git_revision(folder: Path, ignore: Path | None = None) -> tuple[str, bool]:
    """``(<short-sha>, dirty)`` for the repo containing ``folder`` — stamps the rendered PDF
    with the source revision. ``dirty`` reflects uncommitted changes to *source*, excluding
    ``ignore`` (the output file we are about to write — otherwise rendering would always mark
    its own PDF dirty). ``("", False)`` when not a git repo / git is unavailable."""
    import shutil
    import subprocess
    git = shutil.which("git")
    if not git:
        return "", False
    try:
        sha = subprocess.run([git, "-C", str(folder), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        if sha.returncode != 0 or not sha.stdout.strip():
            return "", False
        top = subprocess.run([git, "-C", str(folder), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
        root = Path(top.stdout.strip()) if top.returncode == 0 else None
        ignore_rel = None
        if ignore is not None and root is not None:
            try:
                ignore_rel = ignore.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                ignore_rel = None
        st = subprocess.run([git, "-C", str(folder), "status", "--porcelain"],
                            capture_output=True, text=True, timeout=10)
        dirty = False
        if st.returncode == 0:
            for line in st.stdout.splitlines():
                path = line[3:].strip().strip('"')        # "XY <path>"; rename keeps new name after "->"
                if "->" in path:
                    path = path.split("->")[-1].strip()
                if path and path != ignore_rel:
                    dirty = True
                    break
        return sha.stdout.strip(), dirty
    except (OSError, subprocess.SubprocessError):
        return "", False


def _short_running_title(title: str, limit: int = 60) -> str:
    """A compact running-header form of the title: the part before a colon, truncated."""
    head = title.split(":", 1)[0].strip() or title.strip()
    if len(head) > limit:
        head = head[:limit].rstrip() + "…"
    return head


def _latex_escape(s: str) -> str:
    repl = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
            "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
            "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(repl.get(ch, ch) for ch in s)

# Serif body / sans headings: prefer the named fonts the user asked for (Times /
# Helvetica), then portable equivalents. Probed against the system via `fc-list`, so a
# missing font is skipped rather than failing the xelatex run.
_SERIF_CANDIDATES = ["Times New Roman", "TeX Gyre Termes", "Times", "Liberation Serif",
                     "Georgia", "Nimbus Roman"]
_SANS_CANDIDATES = ["Helvetica Neue", "Helvetica", "TeX Gyre Heros", "Arial",
                    "Liberation Sans", "Nimbus Sans"]
# A modern monospace for inline code / claim ids — deliberately NOT a LaTeX-world font
# (no Latin Modern / Computer Modern Typewriter). Prefer clean coding faces without
# distracting programming ligatures (claim ids are full of `::` / `_`); ligature-heavy
# faces like Fira Code come last.
_MONO_CANDIDATES = ["JetBrains Mono", "Cascadia Mono", "SF Mono", "Source Code Pro",
                    "IBM Plex Mono", "DejaVu Sans Mono", "Menlo", "Roboto Mono",
                    "Monaco", "Fira Mono", "Fira Code"]


def _available_font_families() -> set[str]:
    """The set of font family names xelatex can resolve, via ``fc-list`` (empty if the
    tool is absent — callers then fall back to the LaTeX default font)."""
    import shutil
    import subprocess
    fc = shutil.which("fc-list")
    if not fc:
        return set()
    try:
        out = subprocess.run([fc, ":", "family"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    fams: set[str] = set()
    for line in out.splitlines():
        for fam in line.split(","):
            fams.add(fam.strip())
    return fams


def _pick_font(candidates: list[str], available: set[str]) -> str | None:
    return next((c for c in candidates if c in available), None)


# Bundled pandoc filters: relocate footnotes → endnotes, and widen exhibits (all formats).
_ENDNOTES_LUA = Path(__file__).with_name("endnotes.lua")
_LAYOUT_LUA = Path(__file__).with_name("layout.lua")


class RenderError(RuntimeError):
    """A render toolchain (pandoc) is unavailable or failed."""


def render(report_path: Path, out_path: Path, home: Path | None = None,
           *, to: str = "pdf") -> dict[str, Any]:
    """Render the report to ``out_path`` via **pandoc** (the documented toolchain), in
    ``to`` ∈ ``pdf`` / ``html`` / ``docx``. Assembles the self-contained Markdown with
    :func:`render_markdown` first (embeds inlined/absolutised, citations footnoted).

    Returns ``{output, format}``; raises :class:`RenderError` if pandoc is absent or the
    conversion fails (with the install hint)."""
    import shutil
    import subprocess
    import tempfile

    rp = Path(report_path).resolve()
    home = Path(home).resolve() if home is not None else _infer_home(rp)
    out = Path(out_path)

    pandoc = shutil.which("pandoc")
    if pandoc is None:
        raise RenderError(
            "pandoc not found — it is the report render toolchain. Install it "
            "(macOS: `brew install pandoc`; a PDF target also needs a LaTeX engine, "
            "e.g. `brew install --cask basictex`), or render to a format you have "
            "(`--to html`).")

    md = render_markdown(rp, home)        # citations as native footnotes

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_md = tmp_header = None
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tf:
        tf.write(md)
        tmp_md = Path(tf.name)
    try:
        # endnotes.lua relocates footnotes → a "Grounding notes" endnotes section;
        # layout.lua widens tables/figures. Both are structural AST transforms (every
        # target, no LaTeX package).
        cmd = [pandoc, str(tmp_md), "-o", str(out), "--standalone",
               f"--lua-filter={_ENDNOTES_LUA}", f"--lua-filter={_LAYOUT_LUA}",
               f"--resource-path={rp.parent}", f"--resource-path={home}"]
        if to == "pdf":
            # modern house style: KOMA `scrartcl` (sans headings), serif body + modern
            # monospace via fontspec, half-space block paragraphs, running header, links.
            src = rp.read_text(encoding="utf-8")
            running = _latex_escape(_short_running_title(_report_title(src)))
            # header-right: a classification stamp (front-matter `classification:`,
            # e.g. CONFIDENTIAL / INTERNAL / DRAFT), in a muted red so it reads as a warning
            classification = _front_field(src, "classification")
            head_right = (rf"\textcolor{{red!60!black}}{{\textbf{{{_latex_escape(classification)}}}}}"
                          if classification else "")
            # footer-left: the source revision, so the rendered PDF is traceable to a
            # commit. A trailing asterisk (rather than "-dirty") marks an uncommitted tree
            # — unobtrusive and legible to a non-technical reader.
            sha, dirty = _git_revision(rp.parent, ignore=out)
            foot_left = (rf"rev~\texttt{{{_latex_escape(sha)}}}{'*' if dirty else ''}"
                         if sha else "")
            header_tex = (_PDF_HEADER_TEX
                          .replace("@@RUNNING_TITLE@@", running)
                          .replace("@@HEAD_RIGHT@@", head_right)
                          .replace("@@FOOT_LEFT@@", foot_left))
            with tempfile.NamedTemporaryFile("w", suffix=".tex", delete=False,
                                             encoding="utf-8") as hf:
                hf.write(header_tex)
                tmp_header = Path(hf.name)
            fams = _available_font_families()
            serif = _pick_font(_SERIF_CANDIDATES, fams)
            sans = _pick_font(_SANS_CANDIDATES, fams)
            mono = _pick_font(_MONO_CANDIDATES, fams)
            cmd += [
                "--pdf-engine=xelatex",
                "-V", "documentclass=scrartcl",
                "-V", "classoption=parskip=half",
                "-V", "geometry:margin=1in",
                "-V", "fontsize=11pt",
                "-V", "linestretch=1.12",
                "-V", "colorlinks=true", "-V", "linkcolor=teal",
                "-V", "urlcolor=teal", "-V", "toccolor=teal",
                "--include-in-header", str(tmp_header),
            ]
            if serif:
                cmd += ["-V", f"mainfont={serif}"]
            if sans:
                cmd += ["-V", f"sansfont={sans}"]
            if mono:
                # smaller so a long claim_id fits the measure; modern coding face
                cmd += ["-V", f"monofont={mono}", "-V", "monofontoptions=Scale=0.85"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RenderError(f"pandoc failed ({proc.returncode}):\n{proc.stderr.strip()}")
    finally:
        tmp_md.unlink(missing_ok=True)
        if tmp_header is not None:
            tmp_header.unlink(missing_ok=True)
    return {"output": str(out), "format": to}


# --------------------------------------------------------------------------- #
# rendering (human-readable audit)
# --------------------------------------------------------------------------- #
_CITE_MARK = {"backed": "✅ backed", "weak-backing": "⚠️ weak-backing",
              "missing": "❌ missing", "ambiguous": "❌ ambiguous"}
_EMBED_MARK = {"current": "✅ current", "drifted": "❌ drifted", "missing": "❌ missing",
               "untracked": "❌ untracked", "dangling": "❌ dangling"}


def render_audit(result: dict[str, Any]) -> str:
    """Human-readable audit output, matching the ``sci trace`` / ``sci reproduce`` style."""
    lines = [f"{result['report']}: {result['status']}  "
             f"(scope: {result['scope']}"
             + (f", {result['exp_id']}" if result.get("exp_id") else "") + ")"]
    for c in result["citations"]:
        mark = _CITE_MARK.get(c["verdict"], c["verdict"])
        tail = ""
        if c["verdict"] == "weak-backing":
            tail = f"  ({c.get('outcome')} · {c.get('strength')})"
        elif c["verdict"] == "backed":
            tail = f"  → {c.get('claim_id')}"
        lines.append(f"  [cite L{c['line']}] {c['id']}: {mark}{tail}")
    for e in result["embeds"]:
        mark = _EMBED_MARK.get(e["verdict"], e["verdict"])
        lines.append(f"  [embed L{e['line']}] {e.get('rel') or e['target']}: {mark}")
    for f in result["findings"]:
        if f.get("detail"):
            loc = f.get("cite") or f.get("embed") or ""
            lines.append(f"  ! {f['kind']} (L{f['line']}) {loc}: {f['detail']}")
    return "\n".join(lines)
