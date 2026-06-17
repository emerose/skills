"""Tests for the literature support-judge: cache mechanics, the machine-judged ``source()`` path,
the refresh step, and the audit verdicts — all with a STUBBED judge and a FAKE paper, so nothing
here touches a real model API or the bibliographer library.
"""
import json
from pathlib import Path

import pytest

from scientist import grounding
from scientist.grounding import judgments as J
from scientist.grounding import refresh as REFRESH
from scientist.grounding.judge import JudgeUnavailable
from scientist.provenance import report as R


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
class FakePaper:
    """A stand-in for PaperRef: no library, no network."""

    def __init__(self, text="ASO 7 produced 53% knockdown at the top dose.",
                 mode="fulltext", chunks=None, document_id="doc1"):
        self.citekey = "noor2015q"
        self.sha256 = "a" * 64
        self.mode = mode
        self.title = "A paper"
        self.year = "2015"
        self.doi = "10.1/x"
        self.credibility = {}
        self.is_retracted = False
        self.document_id = document_id
        self.text = text
        self._chunks = chunks or {}

    def contains(self, phrase, *, normalize_ws=True):
        return phrase in self.text

    def chunk_text(self, chunk):
        if isinstance(chunk, int):
            return self._chunks.get(chunk, "")
        return " ".join(self._chunks.get(int(i), "") for i in chunk).strip()


@pytest.fixture
def fake_paper(monkeypatch):
    paper = FakePaper()
    monkeypatch.setattr(grounding, "paper", lambda ck, **kw: paper)
    return paper


@pytest.fixture
def capture():
    cap = grounding.Capture(claim_id="test::lit")
    tok = grounding._CURRENT.set(cap)
    try:
        yield cap
    finally:
        grounding._CURRENT.reset(tok)
        grounding.set_judgment_cache(None)


def _supported_judge(span, paraphrase, *, model_id):
    return {"supported": True, "rationale": "the span states the paraphrase"}


# --------------------------------------------------------------------------- #
# JudgmentCache mechanics
# --------------------------------------------------------------------------- #
def test_cache_lookup_fresh_stale_miss(tmp_path):
    c = J.JudgmentCache(path=tmp_path / "lit_judgments.json")
    esha = J.evidence_sha("Q")
    c.put(citekey="k", evidence_sha_=esha, paraphrase="P", model_id="m1",
          supported=True, rationale="ok", timestamp="2026-06-17T00:00:00+00:00", tier=1)
    # exact triple → fresh
    assert c.lookup("k", esha, "P", "m1")[0] == "fresh"
    # same (citekey, paraphrase), drifted quote → stale
    assert c.lookup("k", J.evidence_sha("Q2"), "P", "m1")[0] == "stale"
    # same (citekey, paraphrase), drifted model → stale
    assert c.lookup("k", esha, "P", "m2")[0] == "stale"
    # different paraphrase → miss (a new question)
    assert c.lookup("k", esha, "P2", "m1")[0] == "miss"


def test_cache_roundtrip(tmp_path):
    p = tmp_path / "lit_judgments.json"
    c = J.JudgmentCache(path=p)
    c.put(citekey="k", evidence_sha_=J.evidence_sha("Q"), paraphrase="P", model_id="m1",
          supported=True, rationale="ok", timestamp="t", tier=1)
    c.save()
    again = J.JudgmentCache.load(p)
    assert again.lookup("k", J.evidence_sha("Q"), "P", "m1")[0] == "fresh"


def test_cache_put_prunes_orphaned_drift(tmp_path):
    c = J.JudgmentCache(path=tmp_path / "x.json")
    c.put(citekey="k", evidence_sha_=J.evidence_sha("Q"), paraphrase="P", model_id="m1",
          supported=True, rationale="", timestamp="t", tier=1)
    # re-judge same question under a new model → old entry pruned, only the new one survives
    c.put(citekey="k", evidence_sha_=J.evidence_sha("Q"), paraphrase="P", model_id="m2",
          supported=False, rationale="", timestamp="t", tier=1)
    assert len(c.entries) == 1
    assert c.lookup("k", J.evidence_sha("Q"), "P", "m2")[0] == "fresh"


# --------------------------------------------------------------------------- #
# source(): the quote tripwire stays deterministic (no model involved)
# --------------------------------------------------------------------------- #
def test_quote_absent_is_deterministic_assertion(fake_paper, capture):
    # machine mode (paraphrase given) does NOT relax the verbatim-quote tripwire
    grounding.set_judgment_cache(J.JudgmentCache())
    with pytest.raises(AssertionError, match="literature quote not found"):
        grounding.source("noor2015q", quote="NOT IN THE PAPER", paraphrase="some reading")


def test_legacy_quote_only_unchanged(fake_paper, capture):
    # no paraphrase → legacy path: records quote, no tier / judge_status, no cache consulted
    rec = grounding.source("noor2015q", quote="53% knockdown")
    assert rec["quote"] == "53% knockdown"
    assert "paraphrase" not in rec and "tier" not in rec and "judge_status" not in rec


def test_machine_mode_needs_both_or_quote(fake_paper, capture):
    with pytest.raises(grounding.LiteratureError, match="quote= .* or paraphrase="):
        grounding.source("noor2015q")          # neither quote nor paraphrase


# --------------------------------------------------------------------------- #
# source(): machine-judged path consumes the cached verdict
# --------------------------------------------------------------------------- #
def test_cached_supported_verdict_backs(fake_paper, capture):
    cache = J.JudgmentCache()
    para = "ASO 7 knocks the target down by about half"
    cache.put(citekey="noor2015q", evidence_sha_=J.evidence_sha("53% knockdown"),
              paraphrase=para, model_id=J.judge_model_id(), supported=True,
              rationale="states 53%", timestamp="t", tier=1)
    grounding.set_judgment_cache(cache)
    rec = grounding.source("noor2015q", quote="53% knockdown", paraphrase=para)
    assert rec["tier"] == 1
    assert rec["judge_status"] == "fresh" and rec["supported"] is True
    assert capture.evidence["lit_sources"][0]["supported"] is True


def test_cached_unsupported_verdict_fails_the_claim(fake_paper, capture):
    cache = J.JudgmentCache()
    para = "ASO 7 cures the disease"
    cache.put(citekey="noor2015q", evidence_sha_=J.evidence_sha("53% knockdown"),
              paraphrase=para, model_id=J.judge_model_id(), supported=False,
              rationale="overreach", timestamp="t", tier=1)
    grounding.set_judgment_cache(cache)
    # the support judgment is EXECUTABLE: a cached unsupported verdict fails the assert
    with pytest.raises(AssertionError, match="NOT supported"):
        grounding.source("noor2015q", quote="53% knockdown", paraphrase=para)
    # ...but it recorded the source first (so the audit can see it)
    assert capture.evidence["lit_sources"][0]["supported"] is False


def test_cache_miss_is_non_blocking(fake_paper, capture):
    grounding.set_judgment_cache(J.JudgmentCache())     # empty
    rec = grounding.source("noor2015q", quote="53% knockdown", paraphrase="a fair reading")
    assert rec["judge_status"] == "miss" and "supported" not in rec   # no assert raised


def test_key_drift_is_stale(fake_paper, capture):
    cache = J.JudgmentCache()
    para = "a fair reading"
    cache.put(citekey="noor2015q", evidence_sha_=J.evidence_sha("OLD QUOTE"),
              paraphrase=para, model_id=J.judge_model_id(), supported=True,
              rationale="", timestamp="t", tier=1)
    grounding.set_judgment_cache(cache)
    rec = grounding.source("noor2015q", quote="53% knockdown", paraphrase=para)  # quote changed
    assert rec["judge_status"] == "stale" and "supported" not in rec   # non-blocking


def test_tier2_chunk_locator(monkeypatch, capture):
    paper = FakePaper(chunks={3: "A paragraph that, taken together, supports the fact."})
    monkeypatch.setattr(grounding, "paper", lambda ck, **kw: paper)
    grounding.set_judgment_cache(J.JudgmentCache())
    rec = grounding.source("noor2015q", chunk=3, paraphrase="the chunk supports this")
    assert rec["tier"] == 2 and rec["span"].startswith("A paragraph")
    assert rec["judge_status"] == "miss"


def test_tier3_whole_doc(fake_paper, capture):
    grounding.set_judgment_cache(J.JudgmentCache())
    rec = grounding.source("noor2015q", paraphrase="the paper as a whole supports this")
    assert rec["tier"] == 3 and rec["span"] == ""          # whole-doc span not carried
    assert rec["evidence_sha"] == J.evidence_sha(fake_paper.text)


# --------------------------------------------------------------------------- #
# refresh step (sci judge) — stubbed judge, no API
# --------------------------------------------------------------------------- #
def _report_json(tmp_path, sources, *, node="test_lit") -> Path:
    prog = tmp_path / "program" / "analysis"
    prog.mkdir(parents=True, exist_ok=True)
    claim = {"id": f"claims/test_literature.py::{node}", "statement": "A fact.",
             "outcome": "passed", "kind": "literature", "strength": "moderate",
             "caveats": None, "reviewed": None,
             "evidence": {"lit_sources": sources}, "inputs": [], "reconcile": []}
    p = prog / "grounding_report.json"
    p.write_text(json.dumps({"claims": [claim]}), encoding="utf-8")
    return p


def _tier1_src(quote="53% knockdown", paraphrase="about half knockdown", status="miss"):
    return {"citekey": "noor2015q", "paraphrase": paraphrase, "tier": 1,
            "quote": quote, "span": quote, "evidence_sha": J.evidence_sha(quote),
            "judge_status": status, "judge_model_id": J.judge_model_id()}


def test_refresh_populates_cache(tmp_path):
    rp = _report_json(tmp_path, [_tier1_src()])
    res = REFRESH.refresh(rp, judge=_supported_judge, model_id="m1")
    assert res["judged"] == 1 and res["skipped"] == 0
    cache = J.JudgmentCache.load(tmp_path / "program" / "analysis" / J.JUDGMENT_CACHE_NAME)
    assert cache.lookup("noor2015q", J.evidence_sha("53% knockdown"),
                        "about half knockdown", "m1")[0] == "fresh"


def test_refresh_skips_fresh(tmp_path):
    rp = _report_json(tmp_path, [_tier1_src()])
    REFRESH.refresh(rp, judge=_supported_judge, model_id="m1")
    res = REFRESH.refresh(rp, judge=_supported_judge, model_id="m1")
    assert res["fresh"] == 1 and res["judged"] == 0


def test_refresh_degrades_without_key(tmp_path):
    rp = _report_json(tmp_path, [_tier1_src()])

    def _no_key(span, paraphrase, *, model_id):
        raise JudgeUnavailable("ANTHROPIC_API_KEY is not set")

    res = REFRESH.refresh(rp, judge=_no_key, model_id="m1")
    assert res["judged"] == 0 and res["skipped"] == 1     # no crash; claim stays needs-judgment
    assert not (tmp_path / "program" / "analysis" / J.JUDGMENT_CACHE_NAME).exists()


def test_refresh_uses_default_judge_when_none(tmp_path, monkeypatch):
    # judge=None → refresh lazily imports the real client; monkeypatch it so no API is hit.
    # Exercises the default-judge path (the call site, not just an injected stub).
    import scientist.grounding.judge as JUDGE
    monkeypatch.setattr(JUDGE, "judge_entailment", _supported_judge)
    rp = _report_json(tmp_path, [_tier1_src()])
    res = REFRESH.refresh(rp, model_id="m1")               # no judge= passed
    assert res["judged"] == 1 and res["skipped"] == 0


def test_refresh_tier3_skips_without_resolver(tmp_path):
    src = {"citekey": "noor2015q", "paraphrase": "whole-doc reading", "tier": 3,
           "span": "", "evidence_sha": "f" * 64, "judge_status": "miss"}
    rp = _report_json(tmp_path, [src])
    res = REFRESH.refresh(rp, judge=_supported_judge, model_id="m1")
    assert res["skipped"] == 1 and res["judged"] == 0


# --------------------------------------------------------------------------- #
# audit (lit_verdict) — the report-level consumption of the machine verdict
# --------------------------------------------------------------------------- #
def _audit_for(tmp_path, sources, *, outcome="passed", strength="strong", node="test_lit"):
    prog = tmp_path / "program" / "analysis"
    prog.mkdir(parents=True, exist_ok=True)
    claim = {"id": f"claims/test_literature.py::{node}", "statement": "A fact.",
             "outcome": outcome, "kind": "literature", "strength": strength,
             "caveats": None, "reviewed": None,
             "evidence": {"lit_sources": sources}, "inputs": [], "reconcile": []}
    (prog / "grounding_report.json").write_text(json.dumps({"claims": [claim]}), encoding="utf-8")
    d = tmp_path / "program" / "reports" / "lit"
    d.mkdir(parents=True, exist_ok=True)
    md = d / "report.md"
    md.write_text(f"# L\n\nFact [lit:program::test_literature.py::{node}].\n", encoding="utf-8")
    return R.audit(md, home=tmp_path)


def _judged_src(*, supported=True, tier=1, status="fresh"):
    s = _tier1_src(status=status)
    s["tier"] = tier
    s["supported"] = supported
    return s


def test_audit_machine_supported_backs(tmp_path):
    res = _audit_for(tmp_path, [_judged_src()], strength="strong")
    assert res["status"] == "GROUNDED"
    assert res["lit_cites"][0]["verdict"] == "backed"


def test_audit_needs_judgment_blocks(tmp_path):
    res = _audit_for(tmp_path, [_tier1_src(status="miss")])
    assert res["status"] == "BROKEN"
    assert res["lit_cites"][0]["verdict"] == "needs-judgment"


def test_audit_stale_judgment_blocks(tmp_path):
    res = _audit_for(tmp_path, [_tier1_src(status="stale")])
    assert res["status"] == "BROKEN"
    assert res["lit_cites"][0]["verdict"] == "stale-judgment"


def test_audit_unsupported_blocks(tmp_path):
    # a cached-unsupported source fails the claim assert → outcome=failed
    res = _audit_for(tmp_path, [_judged_src(supported=False)], outcome="failed")
    assert res["status"] == "BROKEN"
    assert res["lit_cites"][0]["verdict"] == "unsupported"


def test_audit_quote_absent_still_broken(tmp_path):
    # outcome failed with no unsupported verdict → the quote tripwire, not the judge
    src = _tier1_src(status="miss")
    res = _audit_for(tmp_path, [src], outcome="failed")
    assert res["lit_cites"][0]["verdict"] == "broken"


def test_audit_strength_ladder_tier2_caps_at_moderate(tmp_path):
    # a tier-2 (chunk) locator at @strength=strong exceeds its ceiling → over-strength
    res = _audit_for(tmp_path, [_judged_src(tier=2)], strength="strong")
    assert res["status"] == "BROKEN"
    assert res["lit_cites"][0]["verdict"] == "over-strength"
    # the same source at moderate is fine
    res2 = _audit_for(tmp_path, [_judged_src(tier=2)], strength="moderate")
    assert res2["lit_cites"][0]["verdict"] == "backed"


def test_audit_backward_compat_legacy_reviewed(tmp_path):
    # an OLD claim: quote-only source (no paraphrase) + @reviewed(support=True) → still backs
    prog = tmp_path / "program" / "analysis"
    prog.mkdir(parents=True, exist_ok=True)
    claim = {"id": "claims/test_literature.py::test_legacy", "statement": "A fact.",
             "outcome": "passed", "kind": "literature", "strength": "strong", "caveats": None,
             "reviewed": {"date": "2026-06-16", "support": True},
             "evidence": {"lit_sources": [{"citekey": "noor2015q", "quote": "53% knockdown",
                                           "primary": True, "group": "noor"}]},
             "inputs": [], "reconcile": []}
    (prog / "grounding_report.json").write_text(json.dumps({"claims": [claim]}), encoding="utf-8")
    d = tmp_path / "program" / "reports" / "lit"
    d.mkdir(parents=True, exist_ok=True)
    md = d / "report.md"
    md.write_text("# L\n\nFact [lit:program::test_literature.py::test_legacy].\n", encoding="utf-8")
    res = R.audit(md, home=tmp_path)
    assert res["status"] == "GROUNDED"
    assert res["lit_cites"][0]["verdict"] == "backed"


# --------------------------------------------------------------------------- #
# end-to-end: refresh → cache → source() asserts on the fresh verdict
# --------------------------------------------------------------------------- #
def test_end_to_end_refresh_then_source(tmp_path, fake_paper, capture):
    rp = _report_json(tmp_path, [_tier1_src()])
    REFRESH.refresh(rp, judge=_supported_judge, model_id=J.judge_model_id())
    cache = J.JudgmentCache.load(tmp_path / "program" / "analysis" / J.JUDGMENT_CACHE_NAME)
    grounding.set_judgment_cache(cache)
    rec = grounding.source("noor2015q", quote="53% knockdown", paraphrase="about half knockdown")
    assert rec["judge_status"] == "fresh" and rec["supported"] is True
