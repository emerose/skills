"""Reports — `claims → report` (ROADMAP §5): a grounded human narrative.

A *report* is a human-facing Markdown narrative built FROM grounded claims. Where a claim
is one machine-checkable assertion, a report arranges many — fanning in across experiments
— into a readable argument, embedding figures/tables as exhibits. The same provenance
discipline applies: **no quantitative prose without a backing**, and the unit of backing
is an *existing* grounded ``kind=claim`` (not a raw artifact cell). A report never
re-litigates grounding and can't drift ahead of the evidence.

This module implements ``sci report``: one command that

  1. **validates** every citation — each ``[claim:<id>]`` must resolve in the claim index
     to a *live, current* grounded claim (outcome passed/xpass, strength strong/moderate);
     a contradicted (xfail), drifted (failed), unverifiable (skipped) or weak claim cited
     as positive support is a BLOCKING finding, surfaced with its real outcome+strength;
  2. **validates exhibits** — every embedded figure/table must be a *current sha-pinned
     artifact* (its bytes match an ``artifact_sha256`` recorded in some ``experiment.yml``
     provenance ledger); an untracked or drifted exhibit is BLOCKING;
  3. **flags** quantitative sentences with no claim citation (advisory by default; the
     authoritative semantic pass is the agent's, per references/review-audit.md §3);
  4. **renders** the validated Markdown to a polished PDF (pandoc + LaTeX when available,
     a pure-Python markdown→PDF fallback otherwise), with the cited claims emitted as a
     traceable "Grounded claims" appendix.

A report that cites a claim which has since flipped or drifted **fails the audit**, exactly
as ``sci trace`` flags a broken chain — so a shipped report is provably backed by
currently-true claims.

The citation/claim-id conventions match ``index-claims`` / ``sci query --kind claim`` /
``sci trace``: ``claim_id`` = ``<exp>::<test-file>::<node>``; a citation may use that, the
raw pytest nodeid, or the trailing node name when unambiguous.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Citation + exhibit syntax.
_CLAIM_RE = re.compile(r"\[claim:([^\]]+)\]")
_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")          # ![cap](path) -> figure exhibit
_TABLE_RE = re.compile(r"\[table:([^\]]+)\]")            # [table:path]  -> table exhibit
_FRONT_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

# A grounded claim (acceptable positive backing) per the §3 rule.
_OK_OUTCOMES = {"passed", "xpass"}
_OK_STRENGTHS = {"strong", "moderate"}

# Heuristic: a sentence asserting a quantitative result.
_QUANT_RE = re.compile(
    r"(\d+(?:\.\d+)?\s?%|p\s?[<>=]\s?0?\.\d+|r\s?[=~]\s?0?\.\d+|"
    r"\bn\s?=\s?\d+|\bEC50\b|\bIC50\b|\bfold\b|\d+\s?(?:nM|µM|uM|µg|ug|mg)\b)",
    re.IGNORECASE)


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _home() -> Path:
    r = os.environ.get("SCIENTIST_HOME")
    if not r:
        raise RuntimeError("SCIENTIST_HOME is not set — point it at the data-tree root.")
    return Path(r).resolve()


# --------------------------------------------------------------------------- #
# Claim index — every grounded claim known to the tree.
# --------------------------------------------------------------------------- #
def _exp_id_for(rel_file: str) -> str:
    """The owning experiment code for a claim's test-file path. ``program/...`` -> 'program';
    ``K1-NNNNNN ...`` -> 'K1-NNNNNN'."""
    m = re.search(r"(K1-[0-9A-Za-z]+)", rel_file)
    if m:
        return m.group(1)
    if rel_file.replace("\\", "/").startswith("program/"):
        return "program"
    return Path(rel_file).parts[0] if Path(rel_file).parts else "?"


@dataclass
class ClaimIndex:
    by_alias: dict = field(default_factory=dict)     # alias -> record
    by_node: dict = field(default_factory=lambda: {})  # trailing node -> [records]

    def add(self, rec: dict) -> None:
        nodeid = rec["id"]                            # e.g. "<file>::<node>"
        file_part, _, node = nodeid.partition("::")
        exp = _exp_id_for(file_part)
        base = Path(file_part).name
        claim_id = f"{exp}::{base}::{node}"
        rec = dict(rec, claim_id=claim_id, exp=exp, node=node)
        for alias in (nodeid, claim_id, f"{base}::{node}"):
            self.by_alias[alias] = rec
        self.by_node.setdefault(node, []).append(rec)

    def resolve(self, cid: str):
        """Return (record, error). error is a string when the id is unresolved/ambiguous."""
        cid = cid.strip()
        if cid in self.by_alias:
            return self.by_alias[cid], None
        # trailing node name (the common short form)
        node = cid.split("::")[-1]
        cands = self.by_node.get(node, [])
        uniq = {r["claim_id"]: r for r in cands}
        if len(uniq) == 1:
            return next(iter(uniq.values())), None
        if len(uniq) > 1:
            return None, (f"ambiguous citation '{cid}' — matches {sorted(uniq)}; "
                          f"qualify it as '<exp>::<file>::{node}'")
        return None, f"unresolved citation '{cid}' — no grounded claim with that id"


def build_claim_index(home: Path) -> ClaimIndex:
    """Load every ``grounding_report.json`` under the tree into one claim index."""
    idx = ClaimIndex()
    for jf in sorted(home.glob("**/grounding_report.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for rec in (data.get("claims", data) if isinstance(data, dict) else data):
            if isinstance(rec, dict) and rec.get("id"):
                idx.add(rec)
    return idx


# --------------------------------------------------------------------------- #
# Exhibit index — every sha-pinned analysis artifact recorded in a ledger.
# --------------------------------------------------------------------------- #
def build_artifact_index(home: Path) -> dict:
    """Map absolute artifact path -> recorded ``artifact_sha256`` from every
    ``experiment.yml`` provenance ledger (per-experiment + program)."""
    out: dict[str, str] = {}
    for yml in sorted(home.glob("**/experiment.yml")):
        try:
            meta = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(meta, dict):
            continue
        exp_dir = yml.parent
        for edge in (meta.get("provenance") or []):
            if not isinstance(edge, dict):
                continue
            art = edge.get("artifact")
            sha = edge.get("artifact_sha256")
            if art and sha:
                out[str((exp_dir / art).resolve())] = sha
    return out


# --------------------------------------------------------------------------- #
# Parse + audit a report.
# --------------------------------------------------------------------------- #
@dataclass
class Finding:
    severity: str        # "blocking" | "finding" | "advisory"
    kind: str            # "citation" | "exhibit" | "uncited"
    message: str
    line: int = 0


def _find_md(path: Path) -> Path:
    if path.is_file():
        return path
    if path.is_dir():
        cand = path / "report.md"
        if cand.is_file():
            return cand
        mds = sorted(path.glob("*.md"))
        if len(mds) == 1:
            return mds[0]
        if mds:
            raise FileNotFoundError(
                f"{path} has multiple .md files; name one explicitly: {[m.name for m in mds]}")
    raise FileNotFoundError(f"no report markdown found at {path}")


def audit(md_path: Path, idx: ClaimIndex, artifacts: dict) -> tuple[list[Finding], dict]:
    """Audit one report markdown. Returns (findings, summary)."""
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    findings: list[Finding] = []
    cited: list[dict] = []

    def line_of(pos_substr: str) -> int:
        for i, ln in enumerate(lines, 1):
            if pos_substr in ln:
                return i
        return 0

    # 1) citations
    seen_ids: dict[str, dict] = {}
    for m in _CLAIM_RE.finditer(text):
        cid = m.group(1).strip()
        rec, err = idx.resolve(cid)
        ln = line_of(m.group(0))
        if err or rec is None:
            findings.append(Finding("blocking", "citation", err or f"unresolved '{cid}'", ln))
            continue
        if rec["outcome"] not in _OK_OUTCOMES or rec["strength"] not in _OK_STRENGTHS:
            findings.append(Finding(
                "blocking", "citation",
                f"citation '{cid}' is not a grounded positive backing "
                f"(outcome={rec['outcome']}, strength={rec['strength']}) — "
                f"a contradicted/drifted/weak claim must not be cited as support", ln))
            continue
        seen_ids.setdefault(rec["claim_id"], rec)
    cited = list(seen_ids.values())

    # 2) exhibits (figures + tables)
    exhibits: list[str] = []
    for rx, label in ((_IMG_RE, "figure"), (_TABLE_RE, "table")):
        for m in rx.finditer(text):
            # Drop an optional markdown/pandoc title (`![alt](path "title")`) without
            # splitting on whitespace — experiment folders contain spaces, so a naive
            # split truncates the path. A title is a space-delimited quoted suffix.
            raw = m.group(1).strip()
            mt = re.match(r'^(.*?)(?:\s+["\'][^"\']*["\'])?$', raw)
            src = (mt.group(1) if mt else raw).strip().strip("<>")
            if src.startswith(("http://", "https://", "data:")):
                continue
            exhibits.append(src)
            ln = line_of(m.group(0))
            ap = (md_path.parent / src).resolve()
            if not ap.is_file():
                findings.append(Finding("blocking", "exhibit",
                                        f"{label} exhibit not found on disk: {src}", ln))
                continue
            recorded = artifacts.get(str(ap))
            if recorded is None:
                findings.append(Finding(
                    "blocking", "exhibit",
                    f"{label} exhibit '{src}' is not a tracked analysis artifact "
                    f"(no provenance edge produces it) — embed a sha-pinned artifact from a "
                    f"grounded derivation, not an ad-hoc graphic", ln))
            elif recorded != _sha256(ap.read_bytes()):
                findings.append(Finding(
                    "blocking", "exhibit",
                    f"{label} exhibit '{src}' has drifted from its recorded sha "
                    f"(regenerate the derivation and re-embed)", ln))

    # 3) uncited quantitative prose (advisory heuristic).
    # Markdown prose hard-wraps, so a result and its [claim:] citation routinely land on
    # different physical lines; the check is therefore per *paragraph* (blank-line-delimited
    # block of body prose), not per line. A paragraph that asserts a quantitative result and
    # carries no [claim:] anywhere in it is flagged for the agent's authoritative semantic
    # pass (references/review-audit.md §3) — advisory, not blocking.
    body = _FRONT_RE.sub("", text)
    line_off = len(text[:text.index(body)].splitlines()) if body and body in text else 0
    para_start = line_off + 1
    for block in re.split(r"\n[ \t]*\n", body):
        n_lines = block.count("\n") + 1
        prose = " ".join(
            ln.strip() for ln in block.splitlines()
            if ln.strip() and not ln.lstrip().startswith(("#", ">", "|", "!", "<", "```", "%", "[^")))
        if prose and _QUANT_RE.search(prose) and not _CLAIM_RE.search(prose):
            findings.append(Finding(
                "advisory", "uncited",
                f"quantitative paragraph with no [claim:] citation — verify it maps to a "
                f"grounded claim: \"{prose[:90]}{'…' if len(prose) > 90 else ''}\"", para_start))
        para_start += n_lines + 1

    summary = {
        "report": str(md_path),
        "n_citations": len(list(_CLAIM_RE.finditer(text))),
        "n_unique_claims": len(cited),
        "n_exhibits": len(exhibits),
        "cited_claims": [
            {"claim_id": r["claim_id"], "outcome": r["outcome"],
             "strength": r["strength"], "kind": r["kind"], "statement": r["statement"]}
            for r in cited],
        "blocking": sum(1 for f in findings if f.severity == "blocking"),
        "advisory": sum(1 for f in findings if f.severity == "advisory"),
    }
    return findings, summary


# --------------------------------------------------------------------------- #
# Render — Markdown -> PDF (pandoc primary, pure-python fallback).
# --------------------------------------------------------------------------- #
def _preprocess_for_render(text: str, md_path: Path, cited: list[dict]) -> str:
    """Turn the durable citation/exhibit syntax into human-rendered output:
      * each ``[claim:<id>]`` -> a pandoc footnote ref, defined in a Grounded-claims block;
      * each ``[table:<path>]`` -> the CSV rendered as a Markdown table.
    Front matter is converted to a title block."""
    body = _FRONT_RE.sub("", text)
    front = {}
    fm = _FRONT_RE.search(text)
    if fm:
        try:
            front = yaml.safe_load(fm.group(1)) or {}
        except yaml.YAMLError:
            front = {}

    # claim footnotes: stable order of first appearance
    order: list[str] = []
    notes: dict[str, str] = {}

    # we need a resolver here too; rebuild a small map from cited list by claim_id + node
    by_alias = {}
    by_node: dict[str, list[dict]] = {}
    for r in cited:
        by_alias[r["claim_id"]] = r
        by_node.setdefault(r["claim_id"].split("::")[-1], []).append(r)

    def _resolve(cid: str):
        cid = cid.strip()
        if cid in by_alias:
            return by_alias[cid]
        node = cid.split("::")[-1]
        c = {r["claim_id"]: r for r in by_node.get(node, [])}
        return next(iter(c.values())) if len(c) == 1 else None

    def claim_sub(m):
        cid = m.group(1).strip()
        rec = _resolve(cid)
        key = rec["claim_id"] if rec else cid
        if key not in notes:
            order.append(key)
            n = len(order)
            if rec:
                notes[key] = (f"**[{n}]** {rec['statement']} "
                              f"_(claim `{rec['claim_id']}` — {rec['outcome']}/{rec['strength']})_")
            else:
                notes[key] = f"**[{n}]** unresolved claim `{cid}`"
        n = order.index(key) + 1
        return f"[^c{n}]"

    body = _CLAIM_RE.sub(claim_sub, body)

    def table_sub(m):
        src = m.group(1).strip()
        ap = (md_path.parent / src).resolve()
        try:
            import csv
            with open(ap, newline="", encoding="utf-8") as fh:
                rows = list(csv.reader(fh))
        except OSError:
            return f"`[missing table: {src}]`"
        if not rows:
            return ""
        head, *rest = rows
        # Blank lines on BOTH sides so the pipe table is a standalone block even when the
        # [table:...] directive sits inline after lead-in prose ("Source data: [table:...]").
        out = ["", "", "| " + " | ".join(head) + " |",
               "| " + " | ".join("---" for _ in head) + " |"]
        out += ["| " + " | ".join(r) + " |" for r in rest]
        return "\n".join(out + ["", ""])

    body = _TABLE_RE.sub(table_sub, body)

    # Title as a renderer-agnostic H1 + byline (works identically under pandoc and the
    # pure-Python markdown fallback; the pandoc %-title block does not survive the latter).
    parts = []
    if front.get("title"):
        parts.append(f"# {front['title']}")
        byline = " · ".join(str(front[k]) for k in ("author", "date") if front.get(k))
        if byline:
            parts.append(f"*{byline}*")
        parts.append("")
    parts.append(body)
    if order:
        parts.append("\n\n## Grounded claims\n")
        parts.append("_Each cited result is backed by a grounded, re-runnable claim "
                     "(`<exp>::<test-file>::<node>`); outcome/strength shown._\n")
        for n, key in enumerate(order, 1):
            parts.append(f"[^c{n}]: {notes[key].split('** ', 1)[-1]}")
    return "\n".join(parts)


def _render_pandoc(md_text: str, resource_dir: Path, out_pdf: Path) -> bool:
    if not shutil.which("pandoc"):
        return False
    engine = next((e for e in ("xelatex", "lualatex", "pdflatex", "tectonic", "wkhtmltopdf")
                   if shutil.which(e)), None)
    if engine is None:
        return False
    # pandoc runs with cwd=resource_dir so the report's relative image paths resolve; the
    # output path must therefore be ABSOLUTE (a relative -o would be taken relative to that
    # cwd and silently mis-write, dropping us into the fallback renderer).
    resource_dir = resource_dir.resolve()
    out_pdf = out_pdf.resolve()
    cmd = ["pandoc", "-f", "markdown+footnotes", "-o", str(out_pdf),
           f"--pdf-engine={engine}", "--resource-path", str(resource_dir),
           "-V", "geometry:margin=1in", "-V", "linkcolor:blue", "--toc=false"]
    proc = subprocess.run(cmd, input=md_text, text=True,
                          capture_output=True, cwd=str(resource_dir))
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-2000:] + "\n")
        return False
    return out_pdf.is_file()


def _render_python(md_text: str, resource_dir: Path, out_pdf: Path) -> bool:
    try:
        import markdown
        from xhtml2pdf import pisa
    except ImportError as e:
        raise RuntimeError(
            "no PDF renderer available: pandoc+LaTeX not found and the pure-Python "
            f"fallback needs `markdown` + `xhtml2pdf` ({e}). Install the [report] extra.") from e
    html_body = markdown.markdown(
        md_text, extensions=["tables", "footnotes", "fenced_code", "sane_lists"])
    css = ("body{font-family:Helvetica,Arial,sans-serif;font-size:11pt;line-height:1.4;}"
           "h1{font-size:18pt;} h2{font-size:14pt;border-bottom:1px solid #ccc;}"
           "img{max-width:100%;} table{border-collapse:collapse;} "
           "td,th{border:1px solid #999;padding:3px 6px;font-size:9pt;}")
    html = (f"<html><head><meta charset='utf-8'><style>{css}</style></head>"
            f"<body>{html_body}</body></html>")
    out_pdf = out_pdf.resolve()
    with open(out_pdf, "wb") as fh:
        status = pisa.CreatePDF(html, dest=fh, path=str(resource_dir.resolve()) + "/")
    return not status.err and out_pdf.is_file()


def render(md_path: Path, cited: list[dict], out_path: Path, fmt: str = "pdf") -> Path:
    text = md_path.read_text(encoding="utf-8")
    processed = _preprocess_for_render(text, md_path, cited)
    if fmt == "md":
        out_path.write_text(processed, encoding="utf-8")
        return out_path
    if fmt == "pdf":
        if _render_pandoc(processed, md_path.parent, out_path):
            return out_path
        if _render_python(processed, md_path.parent, out_path):
            return out_path
        raise RuntimeError("PDF render failed (see stderr).")
    raise ValueError(f"unsupported format: {fmt}")


# --------------------------------------------------------------------------- #
# Entry point used by sci.py.
# --------------------------------------------------------------------------- #
def run(path: str, *, fmt: str = "pdf", out: str | None = None,
        audit_only: bool = False, strict: bool = False, as_json: bool = False) -> int:
    home = _home()
    md_path = _find_md(Path(path))
    idx = build_claim_index(home)
    artifacts = build_artifact_index(home)
    findings, summary = audit(md_path, idx, artifacts)

    blocking = [f for f in findings if f.severity == "blocking"]
    advisory = [f for f in findings if f.severity == "advisory"]
    strict_block = blocking + (advisory if strict else [])

    rendered = None
    if not audit_only and not strict_block:
        out_path = Path(out) if out else md_path.with_suffix(".pdf")
        rendered = render(md_path, summary["cited_claims"], out_path, fmt=fmt)

    summary["status"] = "FAIL" if strict_block else "PASS"
    summary["rendered"] = str(rendered) if rendered else None
    summary["findings"] = [vars(f) for f in findings]

    if as_json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0 if not strict_block else 1

    print(f"== sci report: {md_path.name} ==")
    print(f"  citations: {summary['n_citations']} "
          f"({summary['n_unique_claims']} unique grounded claims) · "
          f"exhibits: {summary['n_exhibits']}")
    if summary["cited_claims"]:
        print("  cited claims:")
        for r in summary["cited_claims"]:
            print(f"    ✓ {r['claim_id']}  [{r['outcome']}/{r['strength']}/{r['kind']}]")
    for f in blocking:
        print(f"  ❌ BLOCKING (L{f.line}, {f.kind}): {f.message}")
    for f in advisory:
        print(f"  • advisory (L{f.line}): {f.message}")
    print(f"  -> {'AUDIT PASS' if not strict_block else 'AUDIT FAIL'}"
          f"  ({len(blocking)} blocking, {len(advisory)} advisory)")
    if rendered:
        print(f"  rendered: {rendered}")
    elif audit_only:
        print("  (audit-only; not rendered)")
    return 0 if not strict_block else 1
