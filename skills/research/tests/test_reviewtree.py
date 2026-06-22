"""Phase 3 — the review-node tree (storage, audit, render).

Builds review trees on disk (no bibliographer library: leaf `[lit:]` cites resolve to Phase-2
paper-claims in the store) and exercises the tree audit, the per-node grounding overlay, staleness,
and the depth-first render. Run::

    uv run --with-editable skills/scientist pytest skills/scientist/tests/test_reviewtree.py -q
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

from research import reviewtree as T
from research import litreview as LITREVIEW
from research import paperclaims as PC


# --------------------------------------------------------------------------- #
# fixtures / builders
# --------------------------------------------------------------------------- #
_PROTOCOL = textwrap.dedent("""\
    ---
    slug: it-aso
    as_of: "2026-06-19"
    sources: [openalex, pubmed]
    ---
    ## Question & scope
    IT ASO biodistribution.
    ## Search queries
    "intrathecal ASO biodistribution"
    ## Inclusion criteria
    primary biodistribution data
    ## Exclusion criteria
    reviews only
    """)


def _paperclaim(home: Path, citekey: str, slug: str) -> None:
    rec = {
        "id": f"{citekey}::{slug}", "paper": "doi:10.1/x", "citekey": citekey,
        "kind": "attributed", "paraphrase": "cord exceeds cortex at low dose",
        "quote": "cord >> cortex", "evidence_sha": "a" * 64, "strength": "moderate",
        "methods_qualifier": "in vivo, mouse", "precis": True,
    }
    p = PC.claims_path(home, citekey)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec) + "\n", encoding="utf-8")


def _review_dir(home: Path, slug: str = "it-aso") -> Path:
    d = home / "program" / "litreviews" / slug
    (d / "nodes").mkdir(parents=True, exist_ok=True)
    (d / "protocol.md").write_text(_PROTOCOL, encoding="utf-8")
    (d / "screening.jsonl").write_text(
        '{"id":"doi:10.1/x","title":"X","decision":"included","citekey":"silvasantos2015"}\n',
        encoding="utf-8")
    return d


def _write_root(d: Path, body: str, *, summary: str = "Root rollup of CNS distribution.",
                rolled_against: dict | None = None) -> None:
    ra = json.dumps(rolled_against or {})
    (d / "review.md").write_text(
        f"---\nid: root\nsummary: >\n  {summary}\nrolled_against: {ra}\n---\n\n{body}\n",
        encoding="utf-8")


def _write_node(d: Path, nid: str, body: str, *, parent: str = "root",
                summary: str = "A leaf summary.") -> None:
    (d / "nodes" / f"{nid}.md").write_text(
        f"---\nid: {nid}\nparent: {parent}\nsummary: >\n  {summary}\n---\n\n{body}\n",
        encoding="utf-8")


def _clean_tree(home: Path) -> Path:
    """A well-formed 2-node tree (root rollup → one leaf) whose leaf [lit:] resolves to a stored
    paper-claim and whose root pins the child summary correctly. GROUNDED."""
    _paperclaim(home, "silvasantos2015", "cord-gradient")
    d = _review_dir(home)
    leaf_summary = "Cord exceeds cortex at low dose; Lee 2019 outlier."
    _write_node(d, "cns-distribution",
                "Cord exceeds cortex [lit:silvasantos2015::cord-gradient].",
                summary=leaf_summary)
    pin = T._summary_sha(leaf_summary)[:12]
    _write_root(d, "# IT ASO\nOverview [litreview:cns-distribution].\n\n"
                   "## Gaps / open questions\nDose-response below 25% unmeasured.",
                rolled_against={"cns-distribution": pin})
    return d / "review.md"


# --------------------------------------------------------------------------- #
# is_tree
# --------------------------------------------------------------------------- #
def test_flat_review_is_not_a_tree(tmp_path):
    d = tmp_path / "program" / "litreviews" / "flat"
    d.mkdir(parents=True)
    (d / "review.md").write_text("---\ntitle: Flat\n---\n\n## Gaps\nnone\n", encoding="utf-8")
    assert T.is_tree(d / "review.md") is False


def test_tree_detected_by_nodes_dir_or_edge(tmp_path):
    rp = _clean_tree(tmp_path)
    assert T.is_tree(rp) is True


def test_litreview_audit_dispatches_to_tree(tmp_path):
    rp = _clean_tree(tmp_path)
    res = LITREVIEW.audit(rp, home=tmp_path)
    assert res.get("tree") is True and res["kind"] == "litreview"


# --------------------------------------------------------------------------- #
# clean tree
# --------------------------------------------------------------------------- #
def test_clean_tree_is_grounded(tmp_path):
    rp = _clean_tree(tmp_path)
    res = T.audit(rp, home=tmp_path)
    assert res["status"] == "GROUNDED", res["findings"]
    assert res["node_count"] == 2 and res["root"] == "root"


def test_leaf_lit_resolves_to_paper_claim(tmp_path):
    rp = _clean_tree(tmp_path)
    res = T.audit(rp, home=tmp_path)
    # the [litreview:child] edge must NOT surface as missing-litreview (tree owns node edges)
    assert not any(f["kind"] == "missing-litreview" for f in res["findings"])
    assert not any(f["kind"] == "missing-lit" for f in res["findings"])


# --------------------------------------------------------------------------- #
# tree well-formedness
# --------------------------------------------------------------------------- #
def test_unknown_node_edge(tmp_path):
    d = _review_dir(tmp_path)
    _write_root(d, "Overview [litreview:ghost].\n\n## Gaps\nx")
    res = T.audit(d / "review.md", home=tmp_path)
    assert "unknown-node-edge" in {f["kind"] for f in res["findings"]}


def test_orphan_node(tmp_path):
    _paperclaim(tmp_path, "silvasantos2015", "cord-gradient")
    d = _review_dir(tmp_path)
    _write_node(d, "lonely", "Text [lit:silvasantos2015::cord-gradient].")
    _write_root(d, "# Root\nNo edges here.\n\n## Gaps\nx")  # root cites no child → lonely orphaned
    res = T.audit(d / "review.md", home=tmp_path)
    assert "orphan-node" in {f["kind"] for f in res["findings"]}


def test_multiple_parents(tmp_path):
    _paperclaim(tmp_path, "silvasantos2015", "cord-gradient")
    d = _review_dir(tmp_path)
    _write_node(d, "shared", "Text [lit:silvasantos2015::cord-gradient].")
    _write_node(d, "mid", "Mid [litreview:shared].", summary="mid")
    _write_root(d, "# Root\nA [litreview:mid] and B [litreview:shared].\n\n## Gaps\nx")
    res = T.audit(d / "review.md", home=tmp_path)
    assert "multiple-parents" in {f["kind"] for f in res["findings"]}


def test_cycle(tmp_path):
    d = _review_dir(tmp_path)
    _write_node(d, "a", "A [litreview:b].", summary="a")
    _write_node(d, "b", "B [litreview:a].", summary="b")  # a↔b cycle
    _write_root(d, "# Root\nStart [litreview:a].\n\n## Gaps\nx")
    res = T.audit(d / "review.md", home=tmp_path)
    assert "cycle" in {f["kind"] for f in res["findings"]}


def test_duplicate_node_id(tmp_path):
    d = _review_dir(tmp_path)
    _write_node(d, "dup", "one")
    # a second file declaring the same frontmatter id
    (d / "nodes" / "dup2.md").write_text(
        "---\nid: dup\nparent: root\nsummary: >\n  two\n---\n\nbody\n", encoding="utf-8")
    _write_root(d, "# Root\n[litreview:dup]\n\n## Gaps\nx")
    res = T.audit(d / "review.md", home=tmp_path)
    assert "duplicate-node-id" in {f["kind"] for f in res["findings"]}


# --------------------------------------------------------------------------- #
# node-over-B
# --------------------------------------------------------------------------- #
def test_node_over_b(tmp_path, monkeypatch):
    monkeypatch.setenv("SCIENTIST_REVIEW_B_WORDS", "5")
    rp = _clean_tree(tmp_path)  # root overview + gaps is > 5 words
    res = T.audit(rp, home=tmp_path)
    assert "node-over-B" in {f["kind"] for f in res["findings"]}


# --------------------------------------------------------------------------- #
# reference-don't-contain
# --------------------------------------------------------------------------- #
def test_rollup_recites_primary(tmp_path):
    _paperclaim(tmp_path, "silvasantos2015", "cord-gradient")
    d = _review_dir(tmp_path)
    _write_node(d, "cns-distribution",
                "Cord [lit:silvasantos2015::cord-gradient].", summary="leaf")
    # root re-cites the SAME primary claim its descendant owns → reference-don't-contain violation
    _write_root(d, "# Root\nOverview [litreview:cns-distribution] and also "
                   "[lit:silvasantos2015::cord-gradient].\n\n## Gaps\nx")
    res = T.audit(d / "review.md", home=tmp_path)
    assert "rollup-recites-primary" in {f["kind"] for f in res["findings"]}


# --------------------------------------------------------------------------- #
# stale-rollup + write_rollup_pins
# --------------------------------------------------------------------------- #
def test_stale_rollup_on_summary_drift(tmp_path):
    _paperclaim(tmp_path, "silvasantos2015", "cord-gradient")
    d = _review_dir(tmp_path)
    _write_node(d, "cns-distribution",
                "Cord [lit:silvasantos2015::cord-gradient].", summary="NEW summary text")
    _write_root(d, "# Root\nOverview [litreview:cns-distribution].\n\n## Gaps\nx",
                rolled_against={"cns-distribution": "staleeeeeeee0"})  # wrong pin
    res = T.audit(d / "review.md", home=tmp_path)
    assert "stale-rollup" in {f["kind"] for f in res["findings"]}


def test_write_rollup_pins_makes_it_grounded(tmp_path):
    _paperclaim(tmp_path, "silvasantos2015", "cord-gradient")
    d = _review_dir(tmp_path)
    _write_node(d, "cns-distribution",
                "Cord [lit:silvasantos2015::cord-gradient].", summary="leaf rollup summary")
    _write_root(d, "# Root\nOverview [litreview:cns-distribution].\n\n## Gaps\nx")  # no pin yet
    before = T.audit(d / "review.md", home=tmp_path)
    assert "rollup-pin-unrecorded" in {a["kind"] for a in before["advisories"]}
    touched = T.write_rollup_pins(d / "review.md", home=tmp_path)
    assert "root" in touched and "cns-distribution" in touched["root"]
    after = T.audit(d / "review.md", home=tmp_path)
    assert after["status"] == "GROUNDED", after["findings"]
    assert not any(a["kind"] == "rollup-pin-unrecorded" for a in after["advisories"])


# --------------------------------------------------------------------------- #
# literature-only contract in a node
# --------------------------------------------------------------------------- #
def test_claim_cite_in_node_blocks(tmp_path):
    d = _review_dir(tmp_path)
    _write_node(d, "cns-distribution", "Data [claim:program::test_x::test_y].", summary="leaf")
    _write_root(d, "# Root\nOverview [litreview:cns-distribution].\n\n## Gaps\nx")
    res = T.audit(d / "review.md", home=tmp_path)
    assert "kicho-data-in-litreview" in {f["kind"] for f in res["findings"]}


# --------------------------------------------------------------------------- #
# add-node scaffold
# --------------------------------------------------------------------------- #
def test_add_node_scaffolds_file(tmp_path):
    _review_dir(tmp_path)
    res = T.add_node(tmp_path, "it-aso", "cns-distribution", "root")
    assert res["created"] is True
    p = tmp_path / "program" / "litreviews" / "it-aso" / "nodes" / "cns-distribution.md"
    assert p.is_file()
    assert "id: cns-distribution" in p.read_text() and "parent: root" in p.read_text()
    # idempotent: never overwrites
    assert T.add_node(tmp_path, "it-aso", "cns-distribution", "root")["created"] is False


# --------------------------------------------------------------------------- #
# render — depth-first linearization
# --------------------------------------------------------------------------- #
def test_linearize_depth_first(tmp_path):
    rp = _clean_tree(tmp_path)
    md = T.linearize(rp, home=tmp_path)
    # root H1, child nested as H2, [litreview:] edge token removed, [lit:] preserved for footnoting
    assert md.splitlines()[0].startswith("# ")
    assert "## Cns Distribution" in md
    assert "[litreview:" not in md
    assert "[lit:silvasantos2015::cord-gradient]" in md


def test_render_markdown_resolves_facts(tmp_path):
    from reportkit import report as REPORT
    rp = _clean_tree(tmp_path)
    md = T.linearize(rp, home=tmp_path)
    tmp_md = rp.parent / "_lin.md"
    tmp_md.write_text(md, encoding="utf-8")
    rendered = REPORT.render_markdown(tmp_md, home=tmp_path)
    # the leaf paper-claim is footnoted as an attributed reference
    assert "Silvasantos 2015 report:" in rendered
