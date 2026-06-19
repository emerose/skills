"""Phase 2 — the paper-claims layer (attributed external claims).

Exercises the per-paper JSONL store + its offline checks and the ``[lit:]`` resolution to a
stored paper-claim, with NO bibliographer library: the store is plain files, and ``verify``'s
paper-text read is injected as a fake loader (mirroring how the literature tests seed
``_PAPER_CACHE``). Run::

    uv run --with-editable skills/scientist pytest skills/scientist/tests/test_paperclaims.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scientist.provenance import paperclaims as PC
from scientist.provenance import report as REPORT


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _claim(slug: str, citekey: str = "silvasantos2015", **over) -> dict:
    """A schema-valid attributed claim; override any field via kwargs."""
    base = {
        "id": f"{citekey}::{slug}",
        "paper": "doi:10.1234/abc",
        "citekey": citekey,
        "kind": "attributed",
        "paraphrase": "≈50% prenatal loss is tolerated in the model",
        "quote": "we observed loss of roughly half of the litters",
        "evidence_sha": "a" * 64,
        "strength": "moderate",
        "methods_qualifier": "in vivo, mouse, n=12",
        "precis": False,
        "borrowed": False,
        "null_result": False,
    }
    base.update(over)
    return base


def _write(home: Path, citekey: str, claims: list[dict]) -> Path:
    p = PC.claims_path(home, citekey)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in claims) + "\n",
                 encoding="utf-8")
    return p


class _FakePaper:
    """Stand-in for a grounding ``PaperRef`` — verbatim-match against fixed text."""

    def __init__(self, text: str, *, title: str = "T", year: str = "2015",
                 doi: str = "10.1234/abc", mode: str = "fulltext"):
        from scientist.grounding._text import _match_phrase
        self._text = text
        self._match = _match_phrase
        self.title, self.year, self.doi, self.mode = title, year, doi, mode

    def contains(self, phrase: str) -> bool:
        return self._match(phrase, self._text)


# --------------------------------------------------------------------------- #
# load — per-paper sharding, glob, normalization
# --------------------------------------------------------------------------- #
def test_load_globs_per_paper_files(tmp_path):
    _write(tmp_path, "silvasantos2015", [_claim("a", precis=True), _claim("b")])
    _write(tmp_path, "daily2011", [_claim("x", citekey="daily2011", precis=True)])
    idx = PC.load_paper_claims(tmp_path)
    assert set(idx) == {"silvasantos2015::a", "silvasantos2015::b", "daily2011::x"}
    # scoped load reads a single file
    one = PC.load_paper_claims(tmp_path, paper="daily2011")
    assert set(one) == {"daily2011::x"}
    # citekey attached even if a row omitted it (filename stem fallback)
    assert idx["daily2011::x"]["citekey"] == "daily2011"


def test_load_normalizes_flags(tmp_path):
    _write(tmp_path, "silvasantos2015", [{"id": "silvasantos2015::a", "paraphrase": "x"}])
    c = PC.load_paper_claims(tmp_path)["silvasantos2015::a"]
    assert c["precis"] is False and c["borrowed"] is False and c["null_result"] is False


def test_malformed_line_is_a_finding(tmp_path):
    p = PC.claims_path(tmp_path, "silvasantos2015")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"id": "silvasantos2015::a"}\nnot json\n[1,2,3]\n', encoding="utf-8")
    rows, findings = PC.load_file(p)
    assert len(rows) == 1
    kinds = {f["kind"] for f in findings}
    assert kinds == {"malformed-paper-claim-row"}
    assert len(findings) == 2  # the bare text and the JSON array


# --------------------------------------------------------------------------- #
# validate — schema
# --------------------------------------------------------------------------- #
def test_validate_clean_set(tmp_path):
    _write(tmp_path, "silvasantos2015",
           [_claim("prenatal-loss-50pct", precis=True),
            _claim("dosing-window"),
            _claim("no-survival-effect", null_result=True)])
    res = PC.validate(tmp_path, "silvasantos2015")
    assert res["status"] == "VALID", res["findings"]
    assert res["count"] == 3


def test_validate_missing_file(tmp_path):
    res = PC.validate(tmp_path, "ghost2020")
    assert res["status"] == "BROKEN"
    assert res["findings"][0]["kind"] == "missing-paper-claims"


@pytest.mark.parametrize("over,kind", [
    ({"kind": "grounded"}, "wrong-kind"),
    ({"paraphrase": ""}, "missing-field"),
    ({"methods_qualifier": ""}, "missing-field"),
    ({"strength": "definitive"}, "bad-strength"),
    ({"id": "silvasantos2015::Bad_Slug"}, "malformed-id"),
    ({"id": "other2020::x"}, "malformed-id"),          # citekey-half disagrees / wrong file
    ({"evidence_sha": "xyz"}, "malformed-evidence-sha"),
    ({"precis": "yes"}, "malformed-flag"),
    ({"conditioned_on": "nope"}, "malformed-conditioned-on"),
])
def test_validate_row_findings(tmp_path, over, kind):
    # keep exactly one precis on a *separate* clean row so only `over` trips a finding
    bad = _claim("target", **over)
    good = _claim("precis-row", precis=True)
    _write(tmp_path, "silvasantos2015", [good, bad])
    res = PC.validate(tmp_path, "silvasantos2015")
    assert res["status"] == "BROKEN"
    assert kind in {f["kind"] for f in res["findings"]}, res["findings"]


def test_validate_precis_cardinality(tmp_path):
    _write(tmp_path, "silvasantos2015", [_claim("a"), _claim("b")])
    assert "missing-precis" in {f["kind"] for f in PC.validate(tmp_path, "silvasantos2015")["findings"]}
    _write(tmp_path, "silvasantos2015", [_claim("a", precis=True), _claim("b", precis=True)])
    assert "multiple-precis" in {f["kind"] for f in PC.validate(tmp_path, "silvasantos2015")["findings"]}


def test_validate_duplicate_id(tmp_path):
    _write(tmp_path, "silvasantos2015", [_claim("a", precis=True), _claim("a")])
    res = PC.validate(tmp_path, "silvasantos2015")
    assert "duplicate-id" in {f["kind"] for f in res["findings"]}


def test_validate_conditioned_on_resolution(tmp_path):
    # same-paper link to a missing sibling → finding
    _write(tmp_path, "silvasantos2015",
           [_claim("a", precis=True, conditioned_on=["silvasantos2015::ghost"])])
    res = PC.validate(tmp_path, "silvasantos2015")
    assert "unresolved-conditioned-on" in {f["kind"] for f in res["findings"]}
    # resolving sibling present → clean; a cross-paper link is allowed, not resolved
    _write(tmp_path, "silvasantos2015",
           [_claim("a", precis=True, conditioned_on=["silvasantos2015::b", "daily2011::x"]),
            _claim("b")])
    assert PC.validate(tmp_path, "silvasantos2015")["status"] == "VALID"


# --------------------------------------------------------------------------- #
# verify — quote-integrity drift
# --------------------------------------------------------------------------- #
def test_verify_intact(tmp_path):
    from scientist.grounding.judgments import evidence_sha
    quote = "we observed loss of roughly half of the litters"
    text = "Methods … Results: we observed loss of roughly half of the litters with no change."
    _write(tmp_path, "silvasantos2015",
           [_claim("a", precis=True, quote=quote, evidence_sha=evidence_sha(quote))])
    res = PC.verify(tmp_path, "silvasantos2015", paper_loader=lambda ck: _FakePaper(text))
    assert res["status"] == "VERIFIED"
    assert res["ok"] == res["checked"] == 1


def test_verify_quote_drift(tmp_path):
    from scientist.grounding.judgments import evidence_sha
    quote = "a sentence the paper no longer contains"
    _write(tmp_path, "silvasantos2015",
           [_claim("a", precis=True, quote=quote, evidence_sha=evidence_sha(quote))])
    res = PC.verify(tmp_path, "silvasantos2015",
                    paper_loader=lambda ck: _FakePaper("entirely different text"))
    assert res["status"] == "BROKEN"
    assert {f["kind"] for f in res["findings"]} == {"quote-drift"}


def test_verify_sha_mismatch(tmp_path):
    # quote IS in the text, but the stored sha is stale (quote edited w/o re-extraction)
    quote = "we observed loss of roughly half of the litters"
    text = f"Results: {quote}."
    _write(tmp_path, "silvasantos2015",
           [_claim("a", precis=True, quote=quote, evidence_sha="b" * 64)])
    res = PC.verify(tmp_path, "silvasantos2015", paper_loader=lambda ck: _FakePaper(text))
    assert res["status"] == "BROKEN"
    assert {f["kind"] for f in res["findings"]} == {"evidence-sha-mismatch"}


# --------------------------------------------------------------------------- #
# scaffold
# --------------------------------------------------------------------------- #
def test_scaffold_creates_file_and_brief(tmp_path):
    res = PC.scaffold(tmp_path, "silvasantos2015",
                      paper_loader=lambda ck: _FakePaper("body", title="A Study", year="2015"))
    assert res["created"] is True
    assert PC.claims_path(tmp_path, "silvasantos2015").is_file()
    assert "references/paper-claims.md" in res["brief"]
    assert res["paper"]["title"] == "A Study"
    # idempotent: re-scaffold does not clobber, reports exists
    again = PC.scaffold(tmp_path, "silvasantos2015", paper_loader=lambda ck: _FakePaper("body"))
    assert again["created"] is False and again["exists"] is True


def test_scaffold_abstract_only_warns(tmp_path):
    res = PC.scaffold(tmp_path, "silvasantos2015",
                      paper_loader=lambda ck: _FakePaper("abs", mode="abstract"))
    assert "ABSTRACT-ONLY" in res["brief"]


# --------------------------------------------------------------------------- #
# query — substring/regex over paraphrase
# --------------------------------------------------------------------------- #
def test_query_substring_filter(tmp_path):
    _write(tmp_path, "silvasantos2015",
           [_claim("a", precis=True, paraphrase="≈50% prenatal loss is tolerated"),
            _claim("b", paraphrase="dosing window is days 3–7")])
    assert {c["id"] for c in PC.query(tmp_path)} == {"silvasantos2015::a", "silvasantos2015::b"}
    hit = PC.query(tmp_path, query="prenatal")
    assert [c["id"] for c in hit] == ["silvasantos2015::a"]
    # private keys are stripped from the emitted records
    assert not any(k.startswith("_") for k in hit[0])


# --------------------------------------------------------------------------- #
# [lit:] resolution — a report cites a stored paper-claim
# --------------------------------------------------------------------------- #
def _report(home: Path, body: str) -> Path:
    rp = home / "program" / "reports" / "demo" / "report.md"
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(body, encoding="utf-8")
    return rp


def test_lit_resolves_to_paper_claim(tmp_path):
    _write(tmp_path, "silvasantos2015", [_claim("prenatal-loss-50pct", precis=True)])
    rp = _report(tmp_path,
                 "# Demo\n\nThe model tolerates substantial loss "
                 "[lit:silvasantos2015::prenatal-loss-50pct].\n")
    res = REPORT.audit(rp, home=tmp_path)
    lc = res["lit_cites"][0]
    assert lc["verdict"] == "attributed"
    assert lc["attributed"] is True and lc["citekey"] == "silvasantos2015"
    # attributed cites are NOT a blocking finding
    assert not any(f["kind"].endswith("-lit") for f in res["findings"])


def test_lit_missing_paper_claim_is_finding(tmp_path):
    rp = _report(tmp_path, "# Demo\n\nClaim [lit:ghost2020::nope].\n")
    res = REPORT.audit(rp, home=tmp_path)
    assert res["lit_cites"][0]["verdict"] == "missing"
    assert any(f["kind"] == "missing-lit" for f in res["findings"])


def test_lit_paper_claim_wrong_kind_blocks(tmp_path):
    _write(tmp_path, "silvasantos2015",
           [_claim("a", precis=True, kind="grounded")])  # not attributed
    rp = _report(tmp_path, "# Demo\n\nClaim [lit:silvasantos2015::a].\n")
    res = REPORT.audit(rp, home=tmp_path)
    assert res["lit_cites"][0]["verdict"] == "not-attributed"
    assert res["status"] == "BROKEN"


def test_render_markdown_attributes_paper_claim(tmp_path):
    _write(tmp_path, "silvasantos2015",
           [_claim("prenatal-loss-50pct", precis=True,
                   paraphrase="≈50% prenatal loss is tolerated")])
    rp = _report(tmp_path,
                 "# Demo\n\nText [lit:silvasantos2015::prenatal-loss-50pct].\n")
    md = REPORT.render_markdown(rp, home=tmp_path)
    # attributed footnote: "Silvasantos 2015 report: …"; not laundered as a program fact
    assert "Silvasantos 2015 report:" in md
    # an auto-generated works-cited entry for the paper
    assert "# References" in md
