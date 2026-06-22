"""The citation-resolver registry — the seam a host skill's citation layer plugs into.

reportkit is domain-generic: by default NO citation scheme is registered (it is literature-free),
and a host registers `[lit:]`-style schemes via `register_citation`. These tests assert the
empty default and that a registered scheme plugs into every phase: parse, audit, render
(footnote family + works-cited entries), and the audit-output. They also cover the generic
content-keyed footnote dedup the engine does for the built-in `[claim:]` family.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

from reportkit import report as R


def _exp(tmp_path: Path) -> Path:
    exp = tmp_path / "K1-230101 - kd study"
    (exp / "analysis").mkdir(parents=True, exist_ok=True)
    report = {"claims": [
        {"id": "analysis/claims/test_kd.py::test_a", "statement": "A shared fact.",
         "outcome": "passed", "kind": "result", "strength": "strong", "evidence": {}, "inputs": []},
        {"id": "analysis/claims/test_kd.py::test_b", "statement": "A shared fact.",
         "outcome": "passed", "kind": "result", "strength": "strong", "evidence": {}, "inputs": []},
        {"id": "analysis/claims/test_kd.py::test_c", "statement": "A different fact.",
         "outcome": "passed", "kind": "result", "strength": "strong", "evidence": {}, "inputs": []},
    ]}
    (exp / "analysis" / "grounding_report.json").write_text(json.dumps(report), encoding="utf-8")
    # a minimal ledger so audit's artifact index has a home to walk
    (exp / "experiment.yml").write_text(yaml.safe_dump({"exp_id": "K1-230101", "provenance": []}),
                                        encoding="utf-8")
    return exp


def _md(exp: Path, body: str, slug: str = "summary") -> Path:
    d = exp / "reports" / slug
    d.mkdir(parents=True, exist_ok=True)
    md = d / "report.md"
    md.write_text(body, encoding="utf-8")
    return md


@pytest.fixture
def clean_registry():
    """Snapshot + restore the module-level registry so a test's registration doesn't leak."""
    saved = dict(R._CITATION_RESOLVERS)
    try:
        yield
    finally:
        R._CITATION_RESOLVERS.clear()
        R._CITATION_RESOLVERS.update(saved)


def test_no_schemes_registered_by_default():
    # reportkit standalone is literature-free: nothing is registered until a host opts in.
    assert R._CITATION_RESOLVERS == {}


def test_custom_scheme_plugs_into_every_phase(tmp_path, clean_registry):
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
    md = _md(exp, "# x\n\nbacked by [ext:doi-1].\n")
    res = R.audit(md, home=tmp_path)
    assert res["status"] == "GROUNDED"
    assert res["ext_cites"][0]["verdict"] == "backed"            # resolve hook dispatched

    out = R.render_markdown(md, home=tmp_path)
    assert "[^ext-1]" in out and "external source doi-1" in out  # note_text family
    assert "# References" in out and "External, doi-1." in out   # bib_entries hook

    rendered = R.render_audit(res)
    assert "[ext] doi-1: backed" in rendered                     # render_lines hook


def test_registration_is_idempotent_by_name(tmp_path, clean_registry):
    ext_re = re.compile(r"\[ext:\s*([^\[\]]+?)\s*\]")
    noop = lambda cites, ctx: ([], [], [])
    R.register_citation("ext", regex=ext_re, parse_key="ext_cites", resolve=noop)
    R.register_citation("ext", regex=ext_re, parse_key="ext_cites", resolve=noop)
    assert list(R._CITATION_RESOLVERS) == ["ext"]


def test_unregistered_scheme_warns_non_blocking(tmp_path, clean_registry):
    # A [lit:…] citation with NO resolver registered (the research skill isn't installed) is not
    # silently dropped: the audit stays GROUNDED (a setup gap, not a broken cite) but surfaces a
    # non-blocking warning naming the scheme + the install hint.
    R._CITATION_RESOLVERS.clear()
    exp = _exp(tmp_path)
    md = _md(exp, "# x\n\nA fact [claim:test_a] and a paper [lit:smith2020foo].\n")
    res = R.audit(md, home=tmp_path)
    assert res["status"] == "GROUNDED"                                    # never blocks
    warns = [w for w in res["warnings"] if w.get("kind") == "unregistered-scheme"]
    assert len(warns) == 1
    assert warns[0]["scheme"] == "lit"
    assert "research skill" in warns[0]["detail"]
    assert "⚠ unregistered-scheme" in R.render_audit(res)                 # rendered, labelled


def test_unregistered_scheme_silent_once_registered(tmp_path, clean_registry):
    # Registering a resolver for the scheme removes the warning — the citation is now audited.
    lit_re = re.compile(r"\[lit:\s*([^\[\]]+?)\s*\]")
    R.register_citation("lit", regex=lit_re, parse_key="lit_cites",
                        resolve=lambda cites, ctx: ([], [], []))
    exp = _exp(tmp_path)
    md = _md(exp, "# x\n\nA paper [lit:smith2020foo].\n")
    res = R.audit(md, home=tmp_path)
    assert not [w for w in res["warnings"] if w.get("kind") == "unregistered-scheme"]


def test_unregistered_scheme_ignores_urls_and_code_fences(tmp_path, clean_registry):
    # A bracketed URL is not a citation, and a [lit:…] inside a code fence is an example — neither warns.
    R._CITATION_RESOLVERS.clear()
    exp = _exp(tmp_path)
    md = _md(exp, "# x\n\nSee [https://example.com] and:\n\n```\n[lit:example2020]\n```\n")
    res = R.audit(md, home=tmp_path)
    assert not [w for w in res["warnings"] if w.get("kind") == "unregistered-scheme"]


def test_claim_footnotes_are_content_keyed_deduped(tmp_path):
    # The generic [claim:] family numbers footnotes by rendered *text*: re-citing the SAME claim id
    # reuses ONE numbered note (cited twice), while a distinct claim gets its own note.
    exp = _exp(tmp_path)
    md = _md(exp, "# x\n\nOne [claim:test_a] and again [claim:test_a]; also [claim:test_c].\n")
    out = R.render_markdown(md, home=tmp_path)
    assert out.count("[^claim-1]") == 3                 # two in-text markers + one definition
    assert out.count("[^claim-1]: A shared fact.") == 1
    assert "[^claim-2]: A different fact." in out       # the distinct claim is its own note
