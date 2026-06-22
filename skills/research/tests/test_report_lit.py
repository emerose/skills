"""The ``[lit:]`` citation layer driven through the report engine.

The GENERIC report engine (parse / audit / render / scope) lives in ``reportkit`` and is tested
there. What this covers is research's own literature surface — activated by importing
``research.literature_cites`` (which registers the ``[lit:]`` / ``[litreview:]`` schemes): the
``[lit:]`` resolvers / verdicts and the auto-generated works-cited bibliography + content-keyed
footnote dedup. (Split out of scientist's old test_report.py when the literature layer moved to
research.)

Pure: synthetic ``program/analysis`` grounding reports in tmp dirs — no keys, no library.
"""

import json
from pathlib import Path

from reportkit import report as R
from research import literature_cites  # noqa: F401  (registers [lit:])


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


_BIB_SOURCES = [
    # rich: full authors + venue snapshotted (the post-snapshot grounding path)
    {"citekey": "noor2015q", "title": "Allele-specific silencing", "year": "2015",
     "doi": "10.1/x", "authors_text": "Noor, Adila; Smith, Jane; Lee, Kim",
     "venue": "Nature Neuroscience", "system": "human", "test": "direct", "primary": True,
     "group": "noor"},
    # legacy: no authors_text/venue — the entry falls back to the citekey-derived surname+year
    {"citekey": "abbott2020z", "title": "Knockdown durability", "year": "2020",
     "doi": "https://doi.org/10.2/y", "system": "human", "test": "direct", "group": "abbott"},
]


def test_render_markdown_auto_bibliography(tmp_path):
    # each distinct [lit:]-cited paper gets one entry (authors · year · title · venue · DOI),
    # sorted by author — built purely from the fields the source snapshotted, no live library.
    _lit_json(tmp_path, sources=_BIB_SOURCES)
    out = R.render_markdown(_lit_report(tmp_path), home=tmp_path)

    assert "\n# References\n" in out
    # rich entry: ≥3 authors compact to "Noor et al.", venue included
    assert ("Noor et al. (2015). *Allele-specific silencing*. Nature Neuroscience. "
            "<https://doi.org/10.1/x>") in out
    # legacy entry: surname+year recovered from the citekey, no venue
    assert "Abbott (2020). *Knockdown durability*. <https://doi.org/10.2/y>" in out
    # alphabetical by author: Abbott precedes Noor
    assert out.index("Abbott (2020)") < out.index("Noor et al.")
    # the inline citation is still a per-page footnote (the bibliography complements it)
    assert "[^lit-1]" in out


def test_render_markdown_bibliography_defers_to_authored(tmp_path):
    # a report that manages its own References list suppresses the auto-generated one
    _lit_json(tmp_path, sources=_BIB_SOURCES)
    d = tmp_path / "program" / "reports" / "lit"
    d.mkdir(parents=True, exist_ok=True)
    md = d / "report.md"
    md.write_text("# Lit\n\nA fact [lit:program::test_literature.py::test_lit].\n\n"
                  "## References\n\n1. Hand-authored entry.\n", encoding="utf-8")
    out = R.render_markdown(md, home=tmp_path)

    assert out.count("References") == 1               # no second, auto-generated heading
    assert "Hand-authored entry." in out
    assert "Allele-specific silencing" not in out


def _two_lit_claims(tmp_path: Path, stmt_a: str, stmt_b: str) -> None:
    """A grounding report with two distinct literature claims (same single source)."""
    prog = tmp_path / "program" / "analysis"
    prog.mkdir(parents=True, exist_ok=True)
    src = [{"citekey": "noor2015q", "system": "human", "test": "direct", "primary": True,
            "group": "noor"}]
    claims = [
        {"id": f"claims/test_literature.py::test_{n}", "statement": s, "outcome": "passed",
         "kind": "lit", "strength": "strong", "caveats": None,
         "reviewed": {"date": "2026-06-16", "support": True},
         "evidence": {"lit_sources": src}, "inputs": [], "reconcile": []}
        for n, s in (("a", stmt_a), ("b", stmt_b))
    ]
    (prog / "grounding_report.json").write_text(json.dumps({"claims": claims}), encoding="utf-8")


def _two_cite_report(tmp_path: Path) -> Path:
    d = tmp_path / "program" / "reports" / "lit"
    d.mkdir(parents=True, exist_ok=True)
    md = d / "report.md"
    md.write_text("# Lit\n\nOne [lit:program::test_literature.py::test_a] and "
                  "two [lit:program::test_literature.py::test_b].\n", encoding="utf-8")
    return md


def test_render_markdown_dedupes_identical_footnotes(tmp_path):
    # Two distinct lit claims that render to byte-identical note text (same statement, same
    # source) collapse to ONE numbered footnote, cited twice — not two duplicate notes.
    _two_lit_claims(tmp_path, "A shared fact.", "A shared fact.")
    out = R.render_markdown(_two_cite_report(tmp_path), home=tmp_path)
    assert "[^lit-2]" not in out                       # no second, identical note
    assert out.count("[^lit-1]") == 3                  # two in-text markers + one definition
    assert out.count("[^lit-1]: A shared fact.") == 1


def test_render_markdown_keeps_distinct_footnotes(tmp_path):
    # The dedup is content-keyed, not blunt: two claims with different text stay two notes.
    _two_lit_claims(tmp_path, "First fact.", "Second fact.")
    out = R.render_markdown(_two_cite_report(tmp_path), home=tmp_path)
    assert "[^lit-1]: First fact." in out
    assert "[^lit-2]: Second fact." in out
