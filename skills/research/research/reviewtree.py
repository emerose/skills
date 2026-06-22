"""The review-node tree — Phase 3 of the litreview redesign.

A large literature review saturates a reader if written flat: the faithful length of a survey
grows only ~log in the richness of the field, so past some size a single document either drops
findings or exhausts the reader. The fix (redesign §5) is to store the review as a **tree of
claim-citing nodes** — parents *reference* children, never contain them — so richness is absorbed
by **more nodes** (a bushier/deeper tree), each node stays ~one screenful (``B``), and any fact is
reached in ``depth × B``. **The root rollup IS "the review."**

This module is the tree's mechanical audit + render. It is store-free and **reuses the report
engine (:mod:`reportkit.report`) + the ``[lit:]`` citation layer (:mod:`research.literature_cites`)
wholesale** — a node file is the same ``[lit:]``/``[claim:]``/``[litreview:]``-citing Markdown the
report audit already parses, so per-node grounding is the engine's ``audit`` and only the *tree*
layer is new here.

## The node model

A review lives in ``<scope>/litreviews/<slug>/``:

```
<slug>/
  protocol.md          # Phase 1, review-level (frozen pre-registration)
  screening.jsonl      # Phase 1, review-level, append-only (the PRISMA funnel)
  review.md            # the ROOT node (its frontmatter id, conventionally "root")
  nodes/<node-id>.md   # every non-root node
```

Each node carries frontmatter ``id`` / ``parent`` / ``summary`` (its rollup-facing abstract) and,
on a rollup, ``rolled_against: {<child-id>: <summary-sha>}``. The **skeleton** is the parsed,
ordered citation list (derived, not stored): leaves cite primary claims (``[lit:]``), rollups cite
children (``[litreview:<child-id>]``) plus a thin cross-cutting layer and **never re-cite a
primary claim that belongs to a descendant**. The **synthesis** is the prose body — stored, never
regenerated (irrecoverable judgment about how the references relate).

## What `sci` enforces here (mechanical) vs the critic

`sci` checks only the **objective** tree rules, deterministically + offline: the ``[litreview:]``
node-edge graph is a single rooted tree (one root, acyclic, every non-root cited by exactly one
parent); each node's rendered load ≤ ``B``; reference-don't-contain; and per-node ``stale-rollup``
(a child's ``summary`` sha drifted). The **fuzzy** calls stay with the completeness critic
(``references/reviews-tree.md``): the **conflict-survival** obligation (every child-level
unresolved conflict named in the parent's synthesis), whether the split is at a real seam, whether
``ε``-saturation was honest. This is the redesign's KEEP-as-code-vs-guide line.

A **flat review is the degenerate one-node tree** (``levels=0``): a review.md with no ``nodes/``
and no ``[litreview:]`` child edges is exactly Phase 1's review, audited unchanged by
:mod:`research.litreview` — there is no migration.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from reportkit import report as RK
from . import literature_cites as LIT
from . import paperclaims as _paperclaims

# Reader load ceiling per node — a cheap word-count proxy for "~one screenful" (a *max* on a
# single retrieval unit, not a target every node is compressed to). The core invariant is *never
# compress past B; add nodes* — so over-ceiling is blocking, but the remedy (where to split) is the
# critic's. Overridable via $SCIENTIST_REVIEW_B_WORDS for calibration (the knob is a calibrated
# default, never a hard threshold beyond this objective proxy — redesign §6/§7).
DEFAULT_B_WORDS = 500


def b_ceiling() -> int:
    raw = os.environ.get("SCIENTIST_REVIEW_B_WORDS")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_B_WORDS


@dataclass
class Node:
    """One review node: its identity, tree wiring, rollup-facing ``summary``, and the parsed
    citation skeleton of its synthesis body."""

    id: str
    path: Path
    is_root: bool
    summary: str
    rolled_against: dict[str, str]
    parent_fm: str | None
    body: str
    child_edges: list[str] = field(default_factory=list)   # [litreview:] ids, in order
    lit_ids: list[str] = field(default_factory=list)        # [lit:] ids
    claim_ids: list[str] = field(default_factory=list)      # [claim:] ids
    report_ids: list[str] = field(default_factory=list)     # [report:] ids


def _summary_sha(summary: str) -> str:
    """Sha over a node's whitespace-collapsed ``summary`` — the rollup pin. A parent records a
    12-char prefix in ``rolled_against``; the audit recomputes this and flags ``stale-rollup`` on
    drift (matched with ``startswith``, like the report's ``litreview_pins``)."""
    return hashlib.sha256(" ".join((summary or "").split()).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# detection + discovery
# --------------------------------------------------------------------------- #
def is_tree(review_path: Path) -> bool:
    """Is this review a multi-node tree (vs a flat Phase-1 review.md)? True iff a ``nodes/``
    directory holds ≥1 ``*.md`` **or** the root cites at least one ``[litreview:]`` child edge. A
    flat review never does either (a flat review citing ``[litreview:]`` was Phase-1
    nested-litreview, blocked), so every existing review stays on the unchanged Phase-1 path."""
    rp = Path(review_path).resolve()
    nodes_dir = rp.parent / "nodes"
    if nodes_dir.is_dir() and any(nodes_dir.glob("*.md")):
        return True
    try:
        text = rp.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(RK.parse_report(text).get("litreview_cites"))


def _node_from_file(path: Path, *, is_root: bool) -> Node:
    text = path.read_text(encoding="utf-8")
    fm = RK._front_matter(text)
    nid = str(fm.get("id") or ("root" if is_root else path.stem)).strip() or path.stem
    parsed = RK.parse_report(text)
    ra = fm.get("rolled_against") if isinstance(fm.get("rolled_against"), dict) else {}
    return Node(
        id=nid, path=path, is_root=is_root,
        summary=str(fm.get("summary") or ""),
        rolled_against={str(k): str(v) for k, v in (ra or {}).items()},
        parent_fm=(str(fm.get("parent")) if fm.get("parent") is not None else None),
        body=text,
        child_edges=[c["id"] for c in parsed.get("litreview_cites", [])],
        lit_ids=[c["id"] for c in parsed.get("lit_cites", [])],
        claim_ids=[c["id"] for c in parsed.get("citations", [])],
        report_ids=[c["id"] for c in parsed.get("report_cites", [])],
    )


def discover_nodes(review_path: Path) -> tuple[dict[str, Node], list[dict[str, Any]]]:
    """Load every node of the review: the root (``review.md``) plus each ``nodes/*.md``. Returns
    ``({node-id: Node}, findings)`` — ``duplicate-node-id`` (blocking) when two files declare the
    same ``id`` (the later file is dropped from the map)."""
    rp = Path(review_path).resolve()
    files = [(rp, True)]
    nodes_dir = rp.parent / "nodes"
    if nodes_dir.is_dir():
        files += [(p, False) for p in sorted(nodes_dir.glob("*.md"))]
    nodes: dict[str, Node] = {}
    findings: list[dict[str, Any]] = []
    for path, is_root in files:
        node = _node_from_file(path, is_root=is_root)
        if node.id in nodes:
            findings.append({"kind": "duplicate-node-id", "line": 0, "node": node.id,
                             "detail": f"node id `{node.id}` is declared by two files "
                                       f"({RK._rel_or_name(nodes[node.id].path, rp.parent)} and "
                                       f"{RK._rel_or_name(path, rp.parent)}) — ids must be unique"})
            continue
        nodes[node.id] = node
    return nodes, findings


# --------------------------------------------------------------------------- #
# tree well-formedness
# --------------------------------------------------------------------------- #
def build_tree(nodes: dict[str, Node]) -> dict[str, Any]:
    """Validate the ``[litreview:]`` node-edge graph is a single rooted tree. Returns
    ``{root, children, parents, findings}``:

    * ``unknown-node-edge`` — a ``[litreview:<id>]`` resolving to no node in this review (a tree
      keeps each survey self-contained — node edges never point outside it);
    * ``multiple-parents`` — a node cited by >1 parent (a tree, not a DAG);
    * ``orphan-node`` — a non-root node no parent cites (unreachable from the root);
    * ``root-is-child`` — the root (``review.md``) is cited as someone's child;
    * ``cycle`` — the edges form a cycle.
    """
    findings: list[dict[str, Any]] = []
    children: dict[str, list[str]] = {nid: [] for nid in nodes}
    parents: dict[str, list[str]] = {nid: [] for nid in nodes}
    root_id = next((nid for nid, n in nodes.items() if n.is_root), None)

    for nid, node in nodes.items():
        for child in node.child_edges:
            if child not in nodes:
                findings.append({"kind": "unknown-node-edge", "line": 0, "node": nid, "cite": child,
                                 "detail": f"node `{nid}` cites [litreview:{child}] but no node in "
                                           f"this review has that id (node edges stay within the "
                                           f"review; for the root of ANOTHER review cite it from a "
                                           f"report, not a node)"})
                continue
            children[nid].append(child)
            parents[child].append(nid)

    for nid, ps in parents.items():
        if len(ps) > 1:
            findings.append({"kind": "multiple-parents", "line": 0, "node": nid,
                             "detail": f"node `{nid}` is cited by {len(ps)} parents ({', '.join(ps)})"
                                       f" — a review is a tree; each node has exactly one parent"})
    for nid, node in nodes.items():
        if node.is_root and parents[nid]:
            findings.append({"kind": "root-is-child", "line": 0, "node": nid,
                             "detail": f"the root `{nid}` (review.md) is cited as a child by "
                                       f"{', '.join(parents[nid])} — the root is the node nothing cites"})
        if not node.is_root and not parents[nid]:
            findings.append({"kind": "orphan-node", "line": 0, "node": nid,
                             "detail": f"node `{nid}` is cited by no parent — every non-root node "
                                       f"must be reached by exactly one [litreview:] edge"})
        # parent-frontmatter mirror (advisory-grade, surfaced as a finding only on real disagreement)
        if node.parent_fm and parents.get(nid) and node.parent_fm not in parents[nid]:
            findings.append({"kind": "parent-mismatch", "line": 0, "node": nid,
                             "detail": f"node `{nid}` frontmatter parent=`{node.parent_fm}` but it "
                                       f"is actually cited by {', '.join(parents[nid])} — fix the mirror"})

    # cycle detection over the child graph
    WHITE, GREY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in nodes}
    in_cycle: set[str] = set()

    def _visit(u: str, stack: list[str]) -> None:
        color[u] = GREY
        stack.append(u)
        for v in children.get(u, []):
            if color[v] == GREY:
                # back-edge → everything from v to u on the stack is in a cycle
                if v in stack:
                    in_cycle.update(stack[stack.index(v):])
            elif color[v] == WHITE:
                _visit(v, stack)
        stack.pop()
        color[u] = BLACK

    for nid in nodes:
        if color[nid] == WHITE:
            _visit(nid, [])
    if in_cycle:
        findings.append({"kind": "cycle", "line": 0, "node": sorted(in_cycle)[0],
                         "detail": f"the [litreview:] edges form a cycle through "
                                   f"{', '.join(sorted(in_cycle))} — a review is acyclic"})

    return {"root": root_id, "children": children, "parents": parents, "findings": findings}


def _descendants(root: str, children: dict[str, list[str]]) -> set[str]:
    """Every node reachable from ``root`` (exclusive of ``root``), cycle-safe."""
    out: set[str] = set()
    stack = list(children.get(root, []))
    while stack:
        n = stack.pop()
        if n in out:
            continue
        out.add(n)
        stack.extend(children.get(n, []))
    return out


# --------------------------------------------------------------------------- #
# node-load proxy
# --------------------------------------------------------------------------- #
def _synthesis_word_count(node: Node) -> int:
    """The node-load proxy: words in the synthesis body with frontmatter, citation tokens, and
    embeds stripped (the connective prose the reader actually loads). A cheap stand-in for "~one
    screenful" — not a real tokenizer (redesign §6 keeps the proxy objective + deterministic)."""
    text = node.body
    text = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL)  # drop frontmatter
    for pat in (RK._CITE_RE, LIT._LIT_RE, LIT._LITREVIEW_RE, RK._REPORT_RE,
                RK._EMBED_RE):
        text = pat.sub("", text)
    # drop heading/markup punctuation so it doesn't inflate the count
    return len([w for w in re.split(r"\s+", text) if w.strip(" #*_`>-|")])


# --------------------------------------------------------------------------- #
# the tree audit
# --------------------------------------------------------------------------- #
def _node_grounding(node: Node, home: Path) -> list[dict[str, Any]]:
    """Per-node grounding + the literature-only contract, reusing ``report.audit``. Keeps every
    ``[lit:]``/embed finding; drops ``missing-litreview`` for ids that are valid in-tree node edges
    (the tree layer owns those); and overlays the litreview-only rule — a ``[claim:]`` (Kicho data)
    or ``[report:]`` citation in a review node is blocking (data meets the literature only in the
    citing report). Each finding is tagged with the node id."""
    res = RK.audit(node.path, home=home)
    # The tree layer (build_tree) is the sole authority on [litreview:] node edges — drop
    # report.audit's whole-review [litreview:] verdicts (it resolves them against
    # litreviews/<slug>/review.md, not sibling nodes, so it can only mis-flag them).
    _EDGE_KINDS = {"missing-litreview", "ambiguous-litreview", "stale-litreview"}
    out: list[dict[str, Any]] = []
    for f in res.get("findings", []):
        if f.get("kind") in _EDGE_KINDS:
            continue
        out.append({**f, "node": node.id})
    for c in res.get("citations", []):
        out.append({"kind": "kicho-data-in-litreview", "line": c["line"], "node": node.id,
                    "cite": c["id"],
                    "detail": "a litreview surveys only third-party literature — use [lit:], not "
                              "[claim:] (Kicho data meets the literature in the citing report)"})
    for rc in res.get("report_cites", []):
        out.append({"kind": "report-cite-in-litreview", "line": rc["line"], "node": node.id,
                    "cite": rc["id"],
                    "detail": "a litreview does not rest on a report's conclusion — survey the "
                              "literature directly with [lit:]"})
    return out


def audit(review_path: Path, home: Path | None = None) -> dict[str, Any]:
    """Audit a review **tree**: per-node grounding + the literature-only contract, the tree
    well-formedness graph, node-load ≤ ``B``, reference-don't-contain, and per-rollup
    ``stale-rollup``; plus the review-level Phase-1 discipline (protocol, screening + coverage
    cross-check over **all** nodes, the mandatory gaps section). Returns a report-audit-shaped dict
    augmented with ``{kind:'litreview', tree, nodes, funnel, …}`` and a recomputed ``status``.

    The conflict-survival obligation and the split-seam judgment are the completeness critic's, not
    enforced here (see the module docstring / ``references/reviews-tree.md``)."""
    rp = Path(review_path).resolve()
    home = RK._resolve_home(home, rp)
    findings: list[dict[str, Any]] = []
    advisories: list[dict[str, Any]] = []

    nodes, dup = discover_nodes(rp)
    findings.extend(dup)
    tree = build_tree(nodes)
    findings.extend(tree["findings"])
    children = tree["children"]

    # per-node grounding (reuses report.audit) + literature-only overlay
    for nid, node in nodes.items():
        findings.extend(_node_grounding(node, home))

    # node-load ≤ B
    B = b_ceiling()
    for nid, node in nodes.items():
        words = _synthesis_word_count(node)
        if words > B:
            findings.append({"kind": "node-over-B", "line": 0, "node": nid, "value": words,
                             "detail": f"node `{nid}` synthesis is ~{words} words > B={B} — never "
                                       f"compress past B; split it into subtopic leaves + a rollup "
                                       f"(`res litreview {RK.report_scope(rp, home)['slug']} "
                                       f"--add-node <id> --parent {nid}`)"})

    # reference-don't-contain: a rollup must not re-cite a primary claim owned by a descendant
    for nid, node in nodes.items():
        kids = children.get(nid, [])
        if not kids:
            continue
        desc = _descendants(nid, children)
        desc_primary: set[str] = set()
        for d in desc:
            dn = nodes[d]
            desc_primary.update(dn.lit_ids)
            desc_primary.update(dn.claim_ids)
        overlap = (set(node.lit_ids) | set(node.claim_ids)) & desc_primary
        if overlap:
            findings.append({"kind": "rollup-recites-primary", "line": 0, "node": nid,
                             "cites": sorted(overlap),
                             "detail": f"rollup `{nid}` re-cites primary claim(s) "
                                       f"{sorted(overlap)} that belong to a descendant — a rollup "
                                       f"cites its children + a thin cross-cutting layer, never a "
                                       f"descendant's primary claims (reference, don't contain)"})

    # stale-rollup: each rollup pins its children's summary shas (rolled_against)
    for nid, node in nodes.items():
        for child in children.get(nid, []):
            cur = _summary_sha(nodes[child].summary)
            recorded = node.rolled_against.get(child)
            if not recorded:
                advisories.append({"kind": "rollup-pin-unrecorded", "line": 0, "node": nid,
                                   "cites": [child],
                                   "detail": f"rollup `{nid}` has no rolled_against pin for child "
                                             f"`{child}` — record rolled_against: {{{child}: "
                                             f"\"{cur[:12]}\"}} so a child-summary edit re-opens the roll-up"})
            elif not cur.startswith(str(recorded)):
                findings.append({"kind": "stale-rollup", "line": 0, "node": nid, "cite": child,
                                 "detail": f"child `{child}`'s summary changed since `{nid}` rolled "
                                           f"it up (pin={str(recorded)[:12]}, now={cur[:12]}) — "
                                           f"re-roll `{nid}` and re-pin rolled_against"})

    # review-level Phase-1 discipline (protocol/screening/coverage/gaps), at the root
    from . import litreview as LITREVIEW
    claim_index = RK.index_claims(home)
    paper_claim_index = _paperclaims.load_paper_claims(home)
    all_text = "\n\n".join(n.body for n in nodes.values())

    proto, proto_findings = LITREVIEW.validate_protocol(rp)
    findings.extend(proto_findings)
    rows, screen_findings = LITREVIEW.parse_screening(LIT.litreview_screening_path(rp))
    findings.extend(screen_findings)
    funnel = LITREVIEW.prisma_funnel(rows)
    cov_findings, cov_advisories = LITREVIEW._coverage_crosscheck(
        all_text, claim_index, rows, paper_claim_index)
    findings.extend(cov_findings)
    advisories.extend(cov_advisories)

    if not LITREVIEW._GAPS_HEADING_RE.search(all_text):
        findings.append({"kind": "missing-gaps-section", "line": 0,
                         "detail": "a review must close with a gaps / open-questions section "
                                   "(at the root or a node) — what the literature does NOT settle"})

    status = "GROUNDED" if not findings else "BROKEN"
    node_summaries = [{"id": n.id, "is_root": n.is_root, "parent": (tree["parents"].get(n.id) or [None])[0],
                       "children": children.get(n.id, []), "words": _synthesis_word_count(n)}
                      for n in nodes.values()]
    return {
        "report": RK._rel_or_name(rp, home),
        "kind": "litreview", "tree": True,
        "root": tree["root"], "node_count": len(nodes), "nodes": node_summaries,
        "funnel": funnel, "protocol_present": proto["present"], "screening_rows": len(rows),
        "contested_status_addressed": LITREVIEW._addresses_contested_status(all_text),
        "B": B, "findings": findings, "advisories": advisories, "status": status,
        "warnings": RK.stale_grounding_warnings(home),
    }


# --------------------------------------------------------------------------- #
# write rollup pins (the mechanized "paste the surfaced pin" step)
# --------------------------------------------------------------------------- #
def write_rollup_pins(review_path: Path, home: Path | None = None) -> dict[str, dict[str, str]]:
    """For every rollup, write ``rolled_against: {<child-id>: <12-char summary sha>}`` into its
    frontmatter (the mechanized form of "copy the surfaced pin"). Returns ``{node-id: merged
    rolled_against}`` for nodes touched. Surgical: replaces the ``rolled_against`` block in place,
    leaving other frontmatter byte-for-byte."""
    rp = Path(review_path).resolve()
    home = RK._resolve_home(home, rp)
    nodes, _ = discover_nodes(rp)
    tree = build_tree(nodes)
    children = tree["children"]
    touched: dict[str, dict[str, str]] = {}
    for nid, kids in children.items():
        if not kids:
            continue
        merged = {child: _summary_sha(nodes[child].summary)[:12] for child in kids}
        _write_node_rolled_against(nodes[nid].path, merged)
        touched[nid] = merged
    return touched


def _write_node_rolled_against(path: Path, rolled: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    fm_body, rest = (m.group(1), text[m.end():]) if m else ("", text)
    cleaned: list[str] = []
    skipping = False
    for line in fm_body.splitlines():
        if re.match(r"^rolled_against\s*:", line):
            skipping = True
            continue
        if skipping:
            if line.strip() and re.match(r"^\s+\S", line):
                continue
            skipping = False
        cleaned.append(line)
    block = ["rolled_against:"] + [f'  {k}: "{rolled[k]}"' for k in sorted(rolled)]
    new_fm = "\n".join([ln for ln in cleaned if ln.strip()] + block)
    path.write_text(f"---\n{new_fm}\n---\n{rest}", encoding="utf-8")


# --------------------------------------------------------------------------- #
# add-node — scaffold a child node file (judgment of WHERE to split stays the agent's)
# --------------------------------------------------------------------------- #
def add_node(home: Path, slug: str, new_id: str, parent_id: str, *,
             scope: str = "program", summary: str = "") -> dict[str, Any]:
    """Scaffold ``litreviews/<slug>/nodes/<new_id>.md`` (frontmatter + an empty synthesis stub).
    **Minimal by design** (SPEC §9.1): `sci` only lays out the file; the agent re-homes the
    ``[lit:]`` citations and adds the ``[litreview:<new_id>]`` edge from the parent per
    ``references/reviews-tree.md``. Returns ``{node, path, created, parent, reminder}``; never
    overwrites an existing file."""
    home = Path(home).resolve()
    review_dir = home / scope / "litreviews" / slug
    node_path = review_dir / "nodes" / f"{new_id}.md"
    if node_path.exists():
        return {"node": new_id, "path": RK._rel_or_name(node_path, home), "created": False,
                "parent": parent_id, "reminder": "node already exists — not overwritten"}
    node_path.parent.mkdir(parents=True, exist_ok=True)
    stub = (
        f"---\nid: {new_id}\nparent: {parent_id}\n"
        f"summary: >\n  {summary or 'one- to two-sentence rollup-facing abstract of this node — '}\n"
        f"  {'' if summary else 'what the parent summarizes and pins against.'}\n"
        f"rolled_against: {{}}   # only on a rollup: {{<child-id>: <summary-sha>}}\n---\n\n"
        f"<!-- Synthesis: prose about how THIS node's references relate (agree / outlier / conflict\n"
        f"     / gap). A leaf cites primary [lit:] claims; a rollup cites [litreview:<child>] + a\n"
        f"     thin cross-cutting layer and NEVER re-cites a descendant's primary claims. Keep it\n"
        f"     under B (~{b_ceiling()} words) — if it grows past B, split again. Every child-level\n"
        f"     unresolved conflict MUST be named here (conflict-survival). See\n"
        f"     references/reviews-tree.md. -->\n")
    node_path.write_text(stub, encoding="utf-8")
    reminder = (f"add [litreview:{new_id}] to `{parent_id}`'s synthesis (the edge), move the "
                f"relevant [lit:] citations into nodes/{new_id}.md, then `res litreview {slug} "
                f"--write-rollup-pins` and re-audit")
    return {"node": new_id, "path": RK._rel_or_name(node_path, home), "created": True,
            "parent": parent_id, "reminder": reminder}


# --------------------------------------------------------------------------- #
# render — depth-first linearization (facts resolved fresh by report.render_markdown)
# --------------------------------------------------------------------------- #
def _node_heading(node: Node) -> str:
    """A section heading for a node: its frontmatter ``title`` if any, else a Title-Cased id."""
    fm = RK._front_matter(node.body)
    if fm.get("title"):
        return str(fm["title"]).strip()
    return node.id.replace("-", " ").replace("_", " ").strip().title()


def _node_synthesis(node: Node) -> str:
    """A node's synthesis body with frontmatter stripped and ``[litreview:<child>]`` edge tokens
    removed (the child's section follows inline in the linearization, so the edge marker is noise).
    ``[lit:]``/``[claim:]`` are LEFT for ``report.render_markdown`` to footnote with fresh facts."""
    text = re.sub(r"^---\n.*?\n---\n", "", node.body, count=1, flags=re.DOTALL)
    text = LIT._LITREVIEW_RE.sub("", text)
    # drop a leading H1 (the linearizer supplies headings) so we don't double-title
    text = re.sub(r"^\s*#\s+.*\n", "", text, count=1)
    return text.strip()


def linearize(review_path: Path, home: Path | None = None) -> str:
    """Depth-first linearization of the tree into one Markdown document: the root's synthesis is
    the overview, each child a nested section (heading depth = tree depth), recursing in citation
    order. The result still carries ``[lit:]``/``[claim:]`` tokens, so feeding it through
    :func:`report.render_markdown` resolves every fact **fresh** at render (storage ≠ presentation;
    the rendered doc is a disposable view)."""
    rp = Path(review_path).resolve()
    home = RK._resolve_home(home, rp)
    nodes, _ = discover_nodes(rp)
    tree = build_tree(nodes)
    children = tree["children"]
    root = tree["root"]
    title = _node_heading(nodes[root]) if root in nodes else "Review"
    out: list[str] = [f"# {title}\n"]
    seen: set[str] = set()

    def _emit(nid: str, depth: int) -> None:
        if nid in seen or nid not in nodes:
            return
        seen.add(nid)
        node = nodes[nid]
        if depth > 0:                              # root heading already emitted as H1
            out.append(f"\n{'#' * min(depth + 1, 6)} {_node_heading(node)}\n")
        body = _node_synthesis(node)
        if body:
            out.append(body + "\n")
        for child in children.get(nid, []):
            _emit(child, depth + 1)

    if root is not None:
        _emit(root, 0)
    return "\n".join(out).rstrip() + "\n"


def render(review_path: Path, out_path: Path, home: Path | None = None, to: str = "pdf") -> dict[str, Any]:
    """Linearize the tree (:func:`linearize`) and render it to ``out_path`` via the existing
    ``sci report`` markdown→pandoc path. Writes the linearized Markdown to a temporary
    ``.render-<n>.md`` beside ``review.md`` (so relative embeds resolve), renders, then removes it."""
    rp = Path(review_path).resolve()
    home = RK._resolve_home(home, rp)
    md = linearize(rp, home)
    tmp = rp.parent / f".render-{os.getpid()}.md"
    tmp.write_text(md, encoding="utf-8")
    try:
        return RK.render(tmp, Path(out_path), home=home, to=to)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# rendering the audit result
# --------------------------------------------------------------------------- #
def render_audit(result: dict[str, Any]) -> str:
    lines = [f"{result['report']}: {result['status']}  "
             f"(review tree — {result['node_count']} node(s), root `{result.get('root')}`, "
             f"B={result.get('B')} words)"]
    for w in result.get("warnings", []):
        mods = ", ".join(w.get("modules", []))
        lines.append(f"  ⚠ stale-grounding: {w['detail']}" + (f" [{mods}]" if mods else ""))
    for n in result.get("nodes", []):
        kind = "root" if n["is_root"] else f"child of {n['parent']}"
        kids = f" → {', '.join(n['children'])}" if n["children"] else ""
        lines.append(f"  • {n['id']} ({kind}, ~{n['words']}w){kids}")
    f = result.get("funnel") or {}
    lines.append(f"  PRISMA funnel: {f.get('identified', 0)} identified → "
                 f"{f.get('included', 0)} included, {f.get('excluded', 0)} excluded"
                 + (f", {f.get('pending', 0)} pending" if f.get("pending") else ""))
    for fd in result.get("findings", []):
        node = f"[{fd['node']}] " if fd.get("node") else ""
        loc = fd.get("cite") or ""
        lines.append(f"  ! {node}{fd['kind']}{(' ' + str(loc)) if loc else ''}: {fd.get('detail', '')}")
    for a in result.get("advisories", []):
        node = f"[{a['node']}] " if a.get("node") else ""
        lines.append(f"  ~ {node}{a['kind']}: {a.get('detail', '')}")
    return "\n".join(lines)
