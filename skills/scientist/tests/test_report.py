"""The report phase — ``provenance.report`` (``sci report``) + the report-rooted
``sci trace``.

A report is the terminal ``claims → report`` phase: human-facing Markdown carrying
``[claim:<id>]`` citations and ``![..](..)`` figure/table embeds. ``sci report`` mechanically
validates that every citation resolves to a *live, grounded* claim and every embed to a
*current* sha-pinned analysis artifact; the semantic "is each result cited / on-topic"
judgment stays the §3 audit pass.

Pure: synthetic experiment folders in tmp dirs — no keys, no libkit store, no
``$SCIENTIST_HOME``. Each test builds an experiment.yml ledger + a grounding_report.json
+ a report Markdown by hand and asserts the GROUNDED/BROKEN verdict and finding kinds.
"""

import json
import shutil
from pathlib import Path

import pytest
import yaml

import scientist.provenance as P
from scientist.provenance import report as R
from scientist.provenance import trace as T


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def _exp(tmp_path: Path, name: str = "K1-230101 - kd study") -> Path:
    """A clean raw -> data -> analysis chain + ledger (mirrors test_trace._exp)."""
    exp = tmp_path / name
    for sub in ("raw", "data", "analysis/tables"):
        (exp / sub).mkdir(parents=True, exist_ok=True)
    raw = exp / "raw" / "measure.csv"
    raw.write_text("sample,cp\nA,20.1\nB,25.3\n", encoding="utf-8")
    erec = exp / "data" / "extract.py"
    erec.write_text("def build(x):\n    return x\n", encoding="utf-8")
    data = exp / "data" / "table.csv"
    data.write_text("sample,dcp\nA,1.0\nB,5.2\n", encoding="utf-8")
    drec = exp / "analysis" / "derive.py"
    drec.write_text("# derive\n", encoding="utf-8")
    ana = exp / "analysis" / "tables" / "kd.csv"
    ana.write_text("metric,value\nkd_pct,53\n", encoding="utf-8")

    def rel(p: Path) -> str:
        return p.resolve().relative_to(tmp_path.resolve()).as_posix()

    def inp(p: Path) -> dict:
        return {"path": rel(p), "sha256": P.sha256_file(p)}

    sidecar = {
        "exp_id": "K1-230101",
        "name": "kd study",
        "provenance": [
            {"artifact": "data/table.csv", "artifact_sha256": P.sha256_file(data),
             "reviewed_at": "2026-06-08", "inputs": [inp(raw), inp(erec)]},
            {"artifact": "analysis/tables/kd.csv", "artifact_sha256": P.sha256_file(ana),
             "reviewed_at": "2026-06-08", "inputs": [inp(data), inp(drec)]},
        ],
    }
    (exp / "experiment.yml").write_text(yaml.safe_dump(sidecar, sort_keys=False), encoding="utf-8")
    return exp


def _report_json(exp: Path, *, outcome="passed", strength="strong",
                 node="test_knockdown", table="analysis/tables/kd.csv") -> Path:
    art = exp / table
    sha = P.sha256_file(art) if art.is_file() else "0" * 64
    report = {"claims": [{
        "id": f"analysis/claims/test_kd.py::{node}",
        "statement": "knockdown is 53% at the top dose",
        "outcome": outcome, "kind": "result", "strength": strength, "caveats": None,
        "evidence": {"kd_pct": 53},
        "inputs": [{"kind": "data", "path": str(art), "sha256": sha, "via": "tracked"}],
        "reconcile": [],
    }]}
    out = exp / "analysis" / "grounding_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return out


def _report_md(exp: Path, body: str, slug: str = "summary") -> Path:
    d = exp / "reports" / slug
    d.mkdir(parents=True, exist_ok=True)
    md = d / "report.md"
    md.write_text(body, encoding="utf-8")
    return md


# A report that cites the grounded claim and embeds the recorded analysis table.
_GOOD_BODY = """\
# Knockdown summary

We observed sustained knockdown of 53% at the top dose [claim:test_knockdown].

![knockdown table](../../analysis/tables/kd.csv)
"""


# --------------------------------------------------------------------------- #
# (a) clean report citing a grounded claim + embedding a current artifact -> GROUNDED
# --------------------------------------------------------------------------- #
def test_grounded_citation_and_embed(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    md = _report_md(exp, _GOOD_BODY)

    result = R.audit(md, home=tmp_path)

    assert result["status"] == "GROUNDED", result
    assert result["findings"] == []
    assert result["scope"] == "experiment" and result["exp_id"] == "K1-230101"
    cite = result["citations"][0]
    assert cite["verdict"] == "backed"
    assert cite["claim_id"] == "K1-230101::test_kd.py::test_knockdown"
    emb = result["embeds"][0]
    assert emb["verdict"] == "current"
    assert emb["rel"] == "K1-230101 - kd study/analysis/tables/kd.csv"


# --------------------------------------------------------------------------- #
# (b) citing a contradicted (xfail) claim -> weak-backing, BLOCKING
# --------------------------------------------------------------------------- #
def test_contradicted_claim_is_blocking(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp, outcome="xfail", strength="strong")
    md = _report_md(exp, _GOOD_BODY)

    result = R.audit(md, home=tmp_path)

    assert result["status"] == "BROKEN", result
    cite = result["citations"][0]
    assert cite["verdict"] == "weak-backing"
    assert cite["outcome"] == "xfail"           # surfaced with its honest outcome
    assert any(f["kind"] == "weak-backing" for f in result["findings"])


def test_weak_strength_claim_is_blocking(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp, outcome="passed", strength="weak")
    md = _report_md(exp, _GOOD_BODY)

    result = R.audit(md, home=tmp_path)
    assert result["status"] == "BROKEN"
    assert result["citations"][0]["verdict"] == "weak-backing"
    assert result["citations"][0]["strength"] == "weak"


# --------------------------------------------------------------------------- #
# (c) citing a missing claim_id -> missing, BLOCKING
# --------------------------------------------------------------------------- #
def test_missing_claim_id_is_blocking(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    md = _report_md(exp, "# X\n\nKnockdown was 53% [claim:test_does_not_exist].\n")

    result = R.audit(md, home=tmp_path)
    assert result["status"] == "BROKEN"
    assert result["citations"][0]["verdict"] == "missing"
    assert any(f["kind"] == "missing-claim" for f in result["findings"])


def test_ambiguous_short_id_is_blocking(tmp_path):
    # two experiments each define a claim by the same trailing node name
    exp1 = _exp(tmp_path, "K1-230101 - kd study")
    _report_json(exp1)
    exp2 = _exp(tmp_path, "K1-230202 - other study")
    # rewrite exp2's grounding report exp_id-side via folder; same node name
    _report_json(exp2)
    md = _report_md(exp1, "# X\n\nResult [claim:test_knockdown].\n")

    result = R.audit(md, home=tmp_path)
    assert result["status"] == "BROKEN"
    assert result["citations"][0]["verdict"] == "ambiguous"
    # the full id disambiguates
    md2 = _report_md(exp1, "# X\n\nResult [claim:K1-230101::test_kd.py::test_knockdown].\n", slug="s2")
    assert R.audit(md2, home=tmp_path)["status"] == "GROUNDED"


# --------------------------------------------------------------------------- #
# (d) embedding a drifted artifact -> drifted, BLOCKING
# --------------------------------------------------------------------------- #
def test_drifted_embed_is_blocking(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    md = _report_md(exp, _GOOD_BODY)
    # mutate the recorded analysis table AFTER the ledger pinned its sha
    (exp / "analysis" / "tables" / "kd.csv").write_text("metric,value\nkd_pct,99\n", encoding="utf-8")

    result = R.audit(md, home=tmp_path)
    assert result["status"] == "BROKEN"
    emb = result["embeds"][0]
    assert emb["verdict"] == "drifted"
    assert any(f["kind"] == "drifted-embed" for f in result["findings"])


def test_untracked_embed_is_blocking(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    # embed an analysis figure that no edge records (ad-hoc graphic on disk)
    fig = exp / "analysis" / "fig" / "adhoc.png"
    fig.parent.mkdir(parents=True, exist_ok=True)
    fig.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    md = _report_md(exp, "# X\n\n![adhoc](../../analysis/fig/adhoc.png)\n")

    result = R.audit(md, home=tmp_path)
    assert result["status"] == "BROKEN"
    assert result["embeds"][0]["verdict"] == "untracked"


def test_remote_embed_is_blocking(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    md = _report_md(exp, "# X\n\n![remote](https://example.com/plot.png)\n")
    result = R.audit(md, home=tmp_path)
    assert result["status"] == "BROKEN"
    assert result["embeds"][0]["verdict"] == "untracked"


# --------------------------------------------------------------------------- #
# parsing: citations/embeds inside fenced code blocks are ignored
# --------------------------------------------------------------------------- #
def test_code_fence_citations_ignored():
    text = (
        "Real cite [claim:test_a].\n\n"
        "```\n"
        "An example: [claim:test_b] and ![x](y.png)\n"
        "```\n"
        "Another ![real](fig.png)\n"
    )
    parsed = R.parse_report(text)
    cite_ids = [c["id"] for c in parsed["citations"]]
    embed_targets = [e["target"] for e in parsed["embeds"]]
    assert cite_ids == ["test_a"]
    assert embed_targets == ["fig.png"]


def test_parametrized_citation_parses():
    parsed = R.parse_report("Dose result [claim:test_pos_ctrl[100]].\n")
    assert parsed["citations"][0]["id"] == "test_pos_ctrl[100]"


# --------------------------------------------------------------------------- #
# render: self-contained Markdown inlines the csv table + footnotes the claim
# --------------------------------------------------------------------------- #
def test_render_markdown_assembles(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    md = _report_md(exp, _GOOD_BODY)

    out = R.render_markdown(md, home=tmp_path)
    # citations are native pandoc footnotes (typeset per-page by the writer on render)
    assert "[^claim-1]" in out
    # the note reads statement-first, then a compact claim-id citation (test-file + the
    # `test_` prefix dropped); no outcome / strength
    assert "knockdown is 53% at the top dose" in out
    assert "`K1-230101::knockdown`" in out
    assert "passed" not in out and "strong" not in out
    # the csv embed was inlined as a Markdown table (header row present, image gone)
    assert "| metric | value |" in out
    assert "kd.csv)" not in out


def test_report_citation_grounds_on_lemma(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    _report_md(exp, _GOOD_BODY, slug="lemma")                       # itself GROUNDED
    main = _report_md(exp, "# Main\n\nBuilds on the lemma [report:K1-230101::lemma].\n",
                      slug="main")
    res = R.audit(main, home=tmp_path)
    assert res["status"] == "GROUNDED", res
    assert res["report_cites"][0]["verdict"] == "backed"


def test_report_citation_missing_is_blocking(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    main = _report_md(exp, "# Main\n\nSee [report:K1-230101::nope].\n", slug="main")
    res = R.audit(main, home=tmp_path)
    assert res["status"] == "BROKEN"
    assert res["report_cites"][0]["verdict"] == "missing"


def test_report_citation_to_broken_lemma_is_weak(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    _report_md(exp, "# L\n\nResult [claim:test_does_not_exist].\n", slug="badlemma")  # BROKEN
    main = _report_md(exp, "# Main\n\nSee [report:K1-230101::badlemma].\n", slug="main")
    res = R.audit(main, home=tmp_path)
    assert res["status"] == "BROKEN"
    assert res["report_cites"][0]["verdict"] == "weak-backing"


# --------------------------------------------------------------------------- #
# literature citations ([lit:]) — quote-pinned, agent-reviewed, second-class
# --------------------------------------------------------------------------- #
def _lit_json(tmp_path: Path, *, node="test_lit", outcome="passed", kind="literature",
              strength="strong", reviewed=None, sources=None, inputs=None) -> Path:
    """A program-scope grounding report carrying one literature claim."""
    prog = tmp_path / "program" / "analysis"
    prog.mkdir(parents=True, exist_ok=True)
    claim = {
        "id": f"claims/test_literature.py::{node}",
        "statement": "A third-party fact.", "outcome": outcome, "kind": kind,
        "strength": strength, "caveats": None,
        "reviewed": reviewed if reviewed is not None else {"date": "2026-06-16", "support": True},
        "evidence": {"lit_sources": sources or [
            {"citekey": "noor2015q", "system": "human", "test": "direct", "primary": True,
             "group": "noor"}]},
        "inputs": inputs if inputs is not None else [], "reconcile": [],
    }
    (prog / "grounding_report.json").write_text(json.dumps({"claims": [claim]}), encoding="utf-8")
    return prog


def _lit_report(tmp_path: Path, node="test_lit", slug="lit") -> Path:
    d = tmp_path / "program" / "reports" / slug
    d.mkdir(parents=True, exist_ok=True)
    md = d / "report.md"
    md.write_text(f"# Lit\n\nA fact [lit:program::test_literature.py::{node}].\n", encoding="utf-8")
    return md


def test_lit_backed_grounds(tmp_path):
    _lit_json(tmp_path)
    res = R.audit(_lit_report(tmp_path), home=tmp_path)
    assert res["status"] == "GROUNDED", res
    assert res["lit_cites"][0]["verdict"] == "backed"
    assert res["lit_cites"][0]["strength"] == "strong"


def test_lit_weak_still_backs(tmp_path):
    # a single, suggestive, but reviewed-and-supported source is legitimately weak — not broken
    _lit_json(tmp_path, strength="weak")
    res = R.audit(_lit_report(tmp_path), home=tmp_path)
    assert res["status"] == "GROUNDED", res
    assert res["lit_cites"][0]["verdict"] == "backed"


def test_lit_unreviewed_blocks(tmp_path):
    _lit_json(tmp_path, reviewed=False)          # quote present, but no agent support-review
    res = R.audit(_lit_report(tmp_path), home=tmp_path)
    assert res["status"] == "BROKEN"
    assert res["lit_cites"][0]["verdict"] == "needs-review"


def test_lit_unsupported_blocks(tmp_path):
    _lit_json(tmp_path, reviewed={"support": False})
    res = R.audit(_lit_report(tmp_path), home=tmp_path)
    assert res["status"] == "BROKEN"
    assert res["lit_cites"][0]["verdict"] == "unsupported"


def test_lit_quote_absent_blocks(tmp_path):
    _lit_json(tmp_path, outcome="failed")        # the verbatim quote check failed
    res = R.audit(_lit_report(tmp_path), home=tmp_path)
    assert res["status"] == "BROKEN"
    assert res["lit_cites"][0]["verdict"] == "broken"


def test_lit_wrong_kind_blocks(tmp_path):
    # a data claim cited via [lit:] instead of [claim:] is a category error
    _lit_json(tmp_path, kind="result")
    res = R.audit(_lit_report(tmp_path), home=tmp_path)
    assert res["status"] == "BROKEN"
    assert res["lit_cites"][0]["verdict"] == "wrong-kind"


def test_lit_missing_blocks(tmp_path):
    (tmp_path / "program" / "analysis").mkdir(parents=True, exist_ok=True)
    res = R.audit(_lit_report(tmp_path, node="test_ghost"), home=tmp_path)
    assert res["status"] == "BROKEN"
    assert res["lit_cites"][0]["verdict"] == "missing"


def test_lit_review_pinned_to_paper_sha(tmp_path):
    paper_in = [{"kind": "paper", "path": "noor2015q", "sha256": "a" * 64, "via": "literature"}]
    _lit_json(tmp_path, inputs=paper_in)            # discover the current combined sha
    res = R.audit(_lit_report(tmp_path), home=tmp_path)
    cur = res["lit_cites"][0]["review_sha"]
    assert cur and res["lit_cites"][0].get("review_unpinned")   # unpinned → advisory, still backed
    assert res["status"] == "GROUNDED"
    # stamp the matching sha → backed, no longer advisory
    _lit_json(tmp_path, inputs=paper_in, reviewed={"support": True, "sha": cur})
    res = R.audit(_lit_report(tmp_path), home=tmp_path)
    assert res["status"] == "GROUNDED"
    assert res["lit_cites"][0]["verdict"] == "backed"
    assert not res["lit_cites"][0].get("review_unpinned")


def test_lit_stale_review_blocks(tmp_path):
    # review pinned to an old sha; the cited paper's text has since changed
    paper_in = [{"kind": "paper", "path": "noor2015q", "sha256": "b" * 64, "via": "literature"}]
    _lit_json(tmp_path, inputs=paper_in, reviewed={"support": True, "sha": "deadbeef" * 8})
    res = R.audit(_lit_report(tmp_path), home=tmp_path)
    assert res["status"] == "BROKEN"
    assert res["lit_cites"][0]["verdict"] == "stale-review"


def test_render_pdf_if_pandoc(tmp_path):
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc not installed; render toolchain unavailable")
    exp = _exp(tmp_path)
    _report_json(exp)
    md = _report_md(exp, _GOOD_BODY)
    out = tmp_path / "out.html"
    # HTML needs no LaTeX engine, so it's the portable render to assert end-to-end
    res = R.render(md, out, home=tmp_path, to="html")
    assert Path(res["output"]).is_file()
    html = out.read_text(encoding="utf-8")
    assert html.strip()
    # the citation renders as a native footnote (no endnotes relocation): pandoc emits a
    # footnotes section + cross-linked in-text marker (#fn1 / #fnref1)
    assert 'id="fn1"' in html and 'href="#fn1"' in html
    assert 'id="fnref1"' in html


def test_strip_front_matter_keys():
    md = ("---\ntitle: A report\nauthor: Kicho Science\ndate: 2026-06-17\n"
          "classification: INTERNAL\n---\n\n# Body\n\ntext\n")
    out = R._strip_front_matter_keys(md, ("author", "date"))
    assert "author:" not in out and "date:" not in out
    # title + other keys + the body survive untouched
    assert "title: A report" in out and "classification: INTERNAL" in out
    assert "# Body" in out and out.endswith("text\n")
    # no front matter → no-op
    assert R._strip_front_matter_keys("# Body\n", ("author", "date")) == "# Body\n"


def test_render_unnumbers_references_and_dates_footer(tmp_path):
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc not installed; render toolchain unavailable")
    exp = _exp(tmp_path)
    _report_json(exp)
    body = ("---\ntitle: KD\nauthor: Kicho Science\ndate: 2026-06-17\n---\n\n"
            "# Knockdown summary\n\n"
            "Sustained knockdown of 53% [claim:test_knockdown].\n\n"
            "## References\n\n"
            "1. Noor et al. *Journal* (2015). doi:10.1/x\n"
            "2. Smith et al. *Journal* (2020). doi:10.2/y\n")
    md = _report_md(exp, body)
    out = tmp_path / "out.html"
    R.render(md, out, home=tmp_path, to="html")
    html = out.read_text(encoding="utf-8")
    # References render as an unnumbered list: references.lua turned the ordered list into a
    # bullet list, so a <ul> appears (the source has no other bullet list; the only other
    # list pandoc emits is the footnotes <ol>).
    assert "Noor et al." in html and "Smith et al." in html
    assert "<ul>" in html
    # author/date are stripped from the title block (no byline in the rendered header)
    assert "Kicho Science" not in html


# --------------------------------------------------------------------------- #
# report-rooted trace: report -> claim -> data -> raw
# --------------------------------------------------------------------------- #
def test_trace_report_grounded(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    md = _report_md(exp, _GOOD_BODY)

    result = T.trace_report(md, repo_root=tmp_path)
    assert result["status"] == "GROUNDED", result
    assert len(result["terminals"]) == 1
    term = result["terminals"][0]
    assert term["claim_id"] == "K1-230101::test_kd.py::test_knockdown"
    assert term["experiment"] == "K1-230101"
    assert any(T._is_raw(p) for p in term["path_to_raw"]), term["path_to_raw"]


def test_trace_report_broken_on_drift(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    md = _report_md(exp, _GOOD_BODY)
    # drift a raw input under the cited claim's chain
    (exp / "raw" / "measure.csv").write_text("sample,cp\nA,99.9\nB,25.3\n", encoding="utf-8")
    result = T.trace_report(md, repo_root=tmp_path)
    assert result["status"] == "BROKEN", result
    assert any(b["kind"] == "drifted" for b in result["breaks"])


def test_trace_report_missing_cite(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    md = _report_md(exp, "# X\n\n[claim:test_ghost]\n")
    result = T.trace_report(md, repo_root=tmp_path)
    assert result["status"] == "BROKEN"
    assert any(b["kind"] == "dangling" for b in result["breaks"])


# --------------------------------------------------------------------------- #
# sections + ids
# --------------------------------------------------------------------------- #
def test_parse_sections():
    text = (
        "# Tox overview\n\n"
        "This study evaluated chronic tolerability.\n\n"
        "## Methods\n\nSix animals per group.\n\n"
        "## Results\n\nNo mortality observed.\n"
    )
    sec = R.parse_sections(text)
    assert sec["title"] == "Tox overview"
    assert sec["abstract"] == "This study evaluated chronic tolerability."
    headings = [s["heading"] for s in sec["sections"]]
    assert headings == ["Methods", "Results"]
    assert sec["sections"][1]["summary"] == "No mortality observed."


def test_report_scope_program_vs_experiment(tmp_path):
    prog = tmp_path / "program" / "reports" / "tox" / "report.md"
    prog.parent.mkdir(parents=True)
    prog.write_text("# T\n", encoding="utf-8")
    sc = R.report_scope(prog, tmp_path)
    assert sc["scope"] == "program" and sc["slug"] == "tox"

    perexp = tmp_path / "K1-230101 - kd study" / "reports" / "summary" / "report.md"
    perexp.parent.mkdir(parents=True)
    perexp.write_text("# S\n", encoding="utf-8")
    sc2 = R.report_scope(perexp, tmp_path)
    assert sc2["scope"] == "experiment" and sc2["exp_id"] == "K1-230101" and sc2["slug"] == "summary"


def test_claim_id_matches_store_meta():
    from scientist.store import _meta as M
    nodeid = "/abs/K1-230101 - x/analysis/claims/test_kd.py::test_knockdown"
    assert R.claim_id_for("K1-230101", nodeid) == M.claim_id_for("K1-230101", nodeid)
