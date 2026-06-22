"""The generic report engine — :mod:`reportkit.report` (parse / audit / render).

reportkit is domain-generic: it natively resolves ``[claim:]`` / ``[report:]`` / embeds and
knows NO literature/store/library code. These tests build synthetic experiment folders
(experiment.yml ledger + grounding_report.json + report Markdown) in tmp dirs and assert the
GROUNDED/BROKEN verdict + finding kinds — and that the citation-resolver registry is empty by
default and that a host-registered scheme plugs into every phase (parse, audit, render).
"""

import json
from pathlib import Path

import pytest
import yaml

from reportkit import report as R
from reportkit._ledger import sha256_file


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def _exp(tmp_path: Path, name: str = "K1-230101 - kd study") -> Path:
    """A clean raw -> data -> analysis chain + experiment.yml ledger."""
    exp = tmp_path / name
    for sub in ("raw", "data", "analysis/tables"):
        (exp / sub).mkdir(parents=True, exist_ok=True)
    raw = exp / "raw" / "measure.csv"
    raw.write_text("sample,cp\nA,20.1\nB,25.3\n", encoding="utf-8")
    data = exp / "data" / "table.csv"
    data.write_text("sample,dcp\nA,1.0\nB,5.2\n", encoding="utf-8")
    ana = exp / "analysis" / "tables" / "kd.csv"
    ana.write_text("metric,value\nkd_pct,53\n", encoding="utf-8")

    def rel(p: Path) -> str:
        return p.resolve().relative_to(tmp_path.resolve()).as_posix()

    sidecar = {
        "exp_id": "K1-230101",
        "name": "kd study",
        "provenance": [
            {"artifact": "analysis/tables/kd.csv", "artifact_sha256": sha256_file(ana),
             "reviewed_at": "2026-06-08",
             "inputs": [{"path": rel(data), "sha256": sha256_file(data)}]},
        ],
    }
    (exp / "experiment.yml").write_text(yaml.safe_dump(sidecar, sort_keys=False), encoding="utf-8")
    return exp


def _report_json(exp: Path, *, outcome="passed", strength="strong",
                 node="test_knockdown", table="analysis/tables/kd.csv") -> Path:
    art = exp / table
    sha = sha256_file(art) if art.is_file() else "0" * 64
    report = {"claims": [{
        "id": f"analysis/claims/test_kd.py::{node}",
        "statement": "knockdown is 53% at the top dose",
        "outcome": outcome, "kind": "result", "strength": strength,
        "evidence": {"kd_pct": 53},
        "inputs": [{"kind": "data", "path": str(art), "sha256": sha, "via": "tracked"}],
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


_GOOD_BODY = """\
# Knockdown summary

We observed sustained knockdown of 53% at the top dose [claim:test_knockdown].

![knockdown table](../../analysis/tables/kd.csv)
"""


# --------------------------------------------------------------------------- #
# citation + embed audit
# --------------------------------------------------------------------------- #
def test_grounded_citation_and_embed(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    md = _report_md(exp, _GOOD_BODY)
    res = R.audit(md, home=tmp_path)
    assert res["status"] == "GROUNDED"
    assert res["citations"][0]["verdict"] == "backed"
    assert res["embeds"][0]["verdict"] == "current"


def test_weak_strength_claim_is_blocking(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp, strength="weak")
    md = _report_md(exp, _GOOD_BODY)
    res = R.audit(md, home=tmp_path)
    assert res["status"] == "BROKEN"
    assert res["citations"][0]["verdict"] == "weak-backing"


def test_missing_claim_id_is_blocking(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    md = _report_md(exp, "# x\n\nA fact [claim:test_ghost].\n")
    res = R.audit(md, home=tmp_path)
    assert res["status"] == "BROKEN"
    assert res["citations"][0]["verdict"] == "missing"


def test_drifted_embed_is_blocking(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    # change the artifact bytes after it was sha-pinned in the ledger
    (exp / "analysis" / "tables" / "kd.csv").write_text("metric,value\nkd_pct,99\n", encoding="utf-8")
    md = _report_md(exp, _GOOD_BODY)
    res = R.audit(md, home=tmp_path)
    assert res["status"] == "BROKEN"
    assert res["embeds"][0]["verdict"] == "drifted"


def test_untracked_embed_is_blocking(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    (exp / "reports" / "summary").mkdir(parents=True, exist_ok=True)
    (exp / "reports" / "summary" / "adhoc.png").write_text("x", encoding="utf-8")
    md = _report_md(exp, "# x\n\n[claim:test_knockdown]\n\n![adhoc](adhoc.png)\n")
    res = R.audit(md, home=tmp_path)
    assert res["status"] == "BROKEN"
    assert res["embeds"][0]["verdict"] == "untracked"


def test_remote_embed_is_blocking(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    md = _report_md(exp, "# x\n\n[claim:test_knockdown]\n\n![remote](https://ex.com/a.png)\n")
    res = R.audit(md, home=tmp_path)
    assert res["status"] == "BROKEN"
    assert res["embeds"][0]["verdict"] == "untracked"


def test_code_fence_citations_ignored():
    text = "# x\n\n```\n[claim:not_a_cite]\n```\n\nreal [claim:test_x].\n"
    parsed = R.parse_report(text)
    ids = [c["id"] for c in parsed["citations"]]
    assert ids == ["test_x"]


def test_parametrized_citation_parses():
    parsed = R.parse_report("a [claim:test_dose[100]] b\n")
    assert parsed["citations"][0]["id"] == "test_dose[100]"


# --------------------------------------------------------------------------- #
# report-citation (lemma) audit
# --------------------------------------------------------------------------- #
def test_report_citation_grounds_on_lemma(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    _report_md(exp, _GOOD_BODY, slug="lemma")
    citing = _report_md(exp, "# top\n\nrests on [report:K1-230101::lemma].\n", slug="top")
    res = R.audit(citing, home=tmp_path)
    assert res["status"] == "GROUNDED"
    assert res["report_cites"][0]["verdict"] == "backed"


def test_report_citation_missing_is_blocking(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    citing = _report_md(exp, "# top\n\n[report:K1-230101::ghost].\n", slug="top")
    res = R.audit(citing, home=tmp_path)
    assert res["status"] == "BROKEN"
    assert res["report_cites"][0]["verdict"] == "missing"


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
def test_render_markdown_assembles(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    md = _report_md(exp, _GOOD_BODY)
    out = R.render_markdown(md, home=tmp_path)
    assert "[^claim-1]" in out                       # the claim became a footnote marker
    assert "[^claim-1]: knockdown is 53%" in out     # ...with a definition carrying the statement
    assert "| metric | value |" in out               # the .csv embed inlined as a Markdown table


def test_parse_sections_title_and_abstract():
    text = "# Title\n\nLead paragraph here.\n\n## Method\n\nWe did things.\n"
    sec = R.parse_sections(text)
    assert sec["title"] == "Title"
    assert sec["abstract"] == "Lead paragraph here."
    assert sec["sections"][0]["heading"] == "Method"


def test_report_scope_program_vs_experiment(tmp_path):
    prog = tmp_path / "program" / "reports" / "dosing"
    prog.mkdir(parents=True)
    sc = R.report_scope(prog / "report.md", tmp_path)
    assert sc["scope"] == "program" and sc["slug"] == "dosing"
    exp = tmp_path / "K1-230101 - kd" / "reports" / "summary"
    exp.mkdir(parents=True)
    sc = R.report_scope(exp / "report.md", tmp_path)
    assert sc["scope"] == "experiment" and sc["exp_id"] == "K1-230101" and sc["slug"] == "summary"


# --------------------------------------------------------------------------- #
# the citation-resolver registry — the seam a host's literature layer plugs into
# --------------------------------------------------------------------------- #
def test_no_schemes_registered_by_default():
    # reportkit standalone is literature-free: nothing is registered until a host opts in.
    assert R._CITATION_RESOLVERS == {}


@pytest.fixture
def clean_registry():
    """Snapshot + restore the module-level registry so a test's registration doesn't leak."""
    saved = dict(R._CITATION_RESOLVERS)
    try:
        yield
    finally:
        R._CITATION_RESOLVERS.clear()
        R._CITATION_RESOLVERS.update(saved)


def test_custom_scheme_plugs_into_every_phase(tmp_path, clean_registry):
    import re

    # A toy "[ext:<id>]" scheme: any id resolves "backed", with a footnote, a bib entry, an
    # audit line, and participation in the quantity pool — exercising all the registry hooks.
    ext_re = re.compile(r"\[ext:\s*([^\[\]]+?)\s*\]")

    def resolve(cites, ctx):
        recs = [{"id": c["id"], "line": c["line"], "verdict": "backed"} for c in cites]
        return recs, [], []

    def note_text(cid, rctx):
        return f"external source {cid}"

    def bib_entries(cids, rctx):
        return [((cid,), f"External, {cid}.") for cid in cids]

    def render_lines(result):
        return [f"  [ext] {r['id']}: {r['verdict']}" for r in result.get("ext_cites", [])]

    R.register_citation("ext", regex=ext_re, parse_key="ext_cites", resolve=resolve,
                        note_text=note_text, bib_entries=bib_entries,
                        render_lines=render_lines, quantity_cites=True)

    # parse discovers the scheme's citations under its parse_key
    parsed = R.parse_report("see [ext:doi-1].\n")
    assert parsed["ext_cites"][0]["id"] == "doi-1"

    exp = _exp(tmp_path)
    _report_json(exp)
    md = _report_md(exp, "# x\n\nbacked by [ext:doi-1].\n")
    res = R.audit(md, home=tmp_path)
    assert res["status"] == "GROUNDED"
    assert res["ext_cites"][0]["verdict"] == "backed"        # resolve hook dispatched

    out = R.render_markdown(md, home=tmp_path)
    assert "[^ext-1]" in out and "external source doi-1" in out   # note_text family
    assert "# References" in out and "External, doi-1." in out    # bib_entries hook

    rendered = R.render_audit(res)
    assert "[ext] doi-1: backed" in rendered                 # render_lines hook
