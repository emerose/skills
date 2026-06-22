"""Tests for the literature support judge: cache mechanics, the machine-judged ``source()`` path,
the worklist + record steps (``sci judge --list`` / ``--record``), and the audit verdicts — all
with a FAKE paper and no model anywhere. The judge is the orchestrating agent; the tool only lists
the work and records the verdict it is handed, so nothing here touches a model API.
"""
import json
from pathlib import Path

import pytest

from grounding import Capture
from grounding._capture import _CURRENT
import research as grounding
from research import judgments as J
from research import refresh as REFRESH
from reportkit import report as R


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
        self.authors_text = "Noor, Adila; Smith, Jane"
        self.venue = "Nature Neuroscience"
        self.credibility = {}
        self.is_retracted = False
        self.document_id = document_id
        self.text = text
        self._chunks = chunks or {}

    def contains(self, phrase, *, normalize_ws=True):
        # mirror the real PaperRef.contains: fold both sides so a markdown / whitespace /
        # dash variant of a quote matches the stored (Markdown) text.
        if normalize_ws:
            from grounding import fold_match
            return fold_match(phrase) in fold_match(self.text)
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
    cap = Capture(claim_id="test::lit")
    tok = _CURRENT.set(cap)
    try:
        yield cap
    finally:
        _CURRENT.reset(tok)
        grounding.set_judgment_cache(None)


# --------------------------------------------------------------------------- #
# JudgmentCache mechanics — the pin is (evidence_sha, paraphrase); judge_id is metadata
# --------------------------------------------------------------------------- #
def test_cache_lookup_fresh_stale_miss(tmp_path):
    c = J.JudgmentCache(path=tmp_path / "lit_judgments.json")
    esha = J.evidence_sha("Q")
    c.put(citekey="k", evidence_sha_=esha, paraphrase="P", judge_id="agent",
          supported=True, rationale="ok", timestamp="2026-06-17T00:00:00+00:00", tier=1)
    # exact (evidence_sha, paraphrase) → fresh
    assert c.lookup("k", esha, "P")[0] == "fresh"
    # same (citekey, paraphrase), drifted span → stale
    assert c.lookup("k", J.evidence_sha("Q2"), "P")[0] == "stale"
    # different paraphrase → miss (a new question)
    assert c.lookup("k", esha, "P2")[0] == "miss"


def test_cache_lookup_ignores_judge_id(tmp_path):
    # a verdict by a different judge is still valid — judge_id is metadata, not part of the key
    c = J.JudgmentCache(path=tmp_path / "x.json")
    esha = J.evidence_sha("Q")
    c.put(citekey="k", evidence_sha_=esha, paraphrase="P", judge_id="subagent-alice",
          supported=True, rationale="ok", timestamp="t", tier=1)
    assert c.lookup("k", esha, "P")[0] == "fresh"


def test_cache_roundtrip(tmp_path):
    p = tmp_path / "lit_judgments.json"
    c = J.JudgmentCache(path=p)
    c.put(citekey="k", evidence_sha_=J.evidence_sha("Q"), paraphrase="P", judge_id="agent",
          supported=True, rationale="ok", timestamp="t", tier=1)
    c.save()
    again = J.JudgmentCache.load(p)
    assert again.lookup("k", J.evidence_sha("Q"), "P")[0] == "fresh"
    assert again.entries[next(iter(again.entries))]["judge_id"] == "agent"


def test_cache_put_prunes_orphaned_drift(tmp_path):
    c = J.JudgmentCache(path=tmp_path / "x.json")
    c.put(citekey="k", evidence_sha_=J.evidence_sha("Q"), paraphrase="P", judge_id="agent",
          supported=True, rationale="", timestamp="t", tier=1)
    # re-judge the same question under a new span → old entry pruned, only the new one survives
    c.put(citekey="k", evidence_sha_=J.evidence_sha("Q2"), paraphrase="P", judge_id="agent",
          supported=False, rationale="", timestamp="t", tier=1)
    assert len(c.entries) == 1
    assert c.lookup("k", J.evidence_sha("Q2"), "P")[0] == "fresh"


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
    # the bibliographic display fields are snapshotted for the report bibliography
    assert rec["authors_text"] == "Noor, Adila; Smith, Jane" and rec["venue"] == "Nature Neuroscience"


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
              paraphrase=para, judge_id="subagent", supported=True,
              rationale="states 53%", timestamp="t", tier=1)
    grounding.set_judgment_cache(cache)
    rec = grounding.source("noor2015q", quote="53% knockdown", paraphrase=para)
    assert rec["tier"] == 1
    assert rec["judge_status"] == "fresh" and rec["supported"] is True
    assert rec["judged_by"] == "subagent"
    assert capture.evidence["lit_sources"][0]["supported"] is True


def test_cached_unsupported_verdict_fails_the_claim(fake_paper, capture):
    cache = J.JudgmentCache()
    para = "ASO 7 cures the disease"
    cache.put(citekey="noor2015q", evidence_sha_=J.evidence_sha("53% knockdown"),
              paraphrase=para, judge_id="subagent", supported=False,
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


def test_span_drift_is_stale(fake_paper, capture):
    cache = J.JudgmentCache()
    para = "a fair reading"
    cache.put(citekey="noor2015q", evidence_sha_=J.evidence_sha("OLD QUOTE"),
              paraphrase=para, judge_id="agent", supported=True,
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
# worklist + record (sci judge --list / --record) — no model, caller supplies verdicts
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
            "judge_status": status}


def test_worklist_surfaces_missing(tmp_path):
    rp = _report_json(tmp_path, [_tier1_src()])
    res = REFRESH.worklist(rp)
    assert res["missing"] == 1 and res["fresh"] == 0
    item = res["items"][0]
    assert item["citekey"] == "noor2015q" and item["tier"] == 1
    assert item["span_text"] == "53% knockdown"
    assert item["paraphrase"] == "about half knockdown"
    assert item["evidence_sha"] == J.evidence_sha("53% knockdown")
    assert item["claim_id"].endswith("::test_lit")


def test_worklist_skips_fresh(tmp_path):
    rp = _report_json(tmp_path, [_tier1_src()])
    REFRESH.record_verdicts(rp, [{"citekey": "noor2015q", "paraphrase": "about half knockdown",
                                  "supported": True, "rationale": "ok"}])
    res = REFRESH.worklist(rp)
    assert res["fresh"] == 1 and res["items"] == []
    # --force re-surfaces fresh sources
    forced = REFRESH.worklist(rp, force=True)
    assert len(forced["items"]) == 1 and forced["items"][0]["status"] == "fresh"


def test_worklist_tier3_has_empty_span_with_note(tmp_path):
    src = {"citekey": "noor2015q", "paraphrase": "whole-doc reading", "tier": 3,
           "span": "", "evidence_sha": "f" * 64, "judge_status": "miss"}
    rp = _report_json(tmp_path, [src])
    res = REFRESH.worklist(rp)
    item = res["items"][0]
    assert item["span_text"] == "" and "note" in item
    assert item["evidence_sha"] == "f" * 64


def test_record_writes_pinned_verdict(tmp_path):
    rp = _report_json(tmp_path, [_tier1_src()])
    res = REFRESH.record_verdicts(
        rp, [{"citekey": "noor2015q", "paraphrase": "about half knockdown",
              "supported": True, "rationale": "states 53%"}], judge_id="subagent-bob")
    assert res["recorded"] == 1 and res["rejected"] == 0
    cache = J.JudgmentCache.load(tmp_path / "program" / "analysis" / J.JUDGMENT_CACHE_NAME)
    status, entry = cache.lookup("noor2015q", J.evidence_sha("53% knockdown"),
                                 "about half knockdown")
    assert status == "fresh"
    assert entry["supported"] is True and entry["judge_id"] == "subagent-bob"
    assert entry["timestamp"]                      # stamped


def test_record_rejects_unknown_paraphrase(tmp_path):
    rp = _report_json(tmp_path, [_tier1_src()])
    res = REFRESH.record_verdicts(
        rp, [{"citekey": "noor2015q", "paraphrase": "a DIFFERENT paraphrase",
              "supported": True, "rationale": "x"}])
    assert res["recorded"] == 0 and res["rejected"] == 1
    assert "no machine source" in res["details"][0]["reason"]
    assert not (tmp_path / "program" / "analysis" / J.JUDGMENT_CACHE_NAME).exists()


def test_record_recomputes_pin_ignoring_caller_esha(tmp_path):
    # the caller cannot pin a wrong span: the tool recomputes evidence_sha from the report's span,
    # so a bogus evidence_sha in the record is overridden (not trusted) — here it also MATCHES the
    # current span (echoed correctly), so it records fine.
    rp = _report_json(tmp_path, [_tier1_src()])
    res = REFRESH.record_verdicts(
        rp, [{"citekey": "noor2015q", "paraphrase": "about half knockdown",
              "evidence_sha": J.evidence_sha("53% knockdown"),
              "supported": True, "rationale": "ok"}])
    assert res["recorded"] == 1
    cache = J.JudgmentCache.load(tmp_path / "program" / "analysis" / J.JUDGMENT_CACHE_NAME)
    assert cache.lookup("noor2015q", J.evidence_sha("53% knockdown"),
                        "about half knockdown")[0] == "fresh"


def test_record_rejects_stale_echoed_esha(tmp_path):
    # caller echoes an evidence_sha from an OLD worklist; the report's span has since changed →
    # the recomputed pin disagrees → rejected (cannot record against a stale span)
    rp = _report_json(tmp_path, [_tier1_src(quote="53% knockdown")])
    res = REFRESH.record_verdicts(
        rp, [{"citekey": "noor2015q", "paraphrase": "about half knockdown",
              "evidence_sha": J.evidence_sha("STALE OLD QUOTE"),
              "supported": True, "rationale": "ok"}])
    assert res["recorded"] == 0 and res["rejected"] == 1
    assert "stale" in res["details"][0]["reason"]


def test_record_missing_supported_is_rejected(tmp_path):
    rp = _report_json(tmp_path, [_tier1_src()])
    res = REFRESH.record_verdicts(
        rp, [{"citekey": "noor2015q", "paraphrase": "about half knockdown", "rationale": "x"}])
    assert res["recorded"] == 0 and res["rejected"] == 1
    assert "supported" in res["details"][0]["reason"]


# --------------------------------------------------------------------------- #
# audit (lit_verdict) — the report-level consumption of the recorded verdict
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
# end-to-end: list → judge (caller) → record → source() asserts on the fresh verdict
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# folded cache identity — markdown / whitespace / dash variants share one verdict
# --------------------------------------------------------------------------- #
def test_evidence_sha_folds_markdown_whitespace_dash():
    # the cache identity hashes the FOLDED span (same normalization as quote-matching), so
    # markdown / whitespace / Unicode-dash variants of one sentence map to ONE identity.
    base = J.evidence_sha("Ube3a gene dosage drives the phenotype")
    assert J.evidence_sha("*Ube3a* gene dosage drives the phenotype") == base   # markdown
    assert J.evidence_sha("Ube3a   gene   dosage drives the phenotype") == base  # whitespace
    assert J.evidence_sha("Ube3a gene dosage\ndrives the phenotype") == base     # newline
    assert J.evidence_sha("Ube3a gene dosage–drives the phenotype") != base  # en-dash IS content here
    # ...but the fold is deterministic and still distinguishes genuinely different text
    assert J.evidence_sha("a totally different sentence") != base
    assert J.evidence_sha("x") == J.evidence_sha("x")


def test_markdown_edit_does_not_stale_a_verdict(tmp_path):
    # a markdown- or whitespace-only edit to the quote must NOT stale a good verdict: the
    # folded form is unchanged, so the cache key is unchanged → still fresh.
    c = J.JudgmentCache(path=tmp_path / "x.json")
    para = "the gene dosage drives it"
    c.put(citekey="k", evidence_sha_=J.evidence_sha("*Ube3a* gene dosage"),
          paraphrase=para, judge_id="agent", supported=True, rationale="", timestamp="t", tier=1)
    # re-cite the same sentence without markdown / with extra whitespace → still fresh
    assert c.lookup("k", J.evidence_sha("Ube3a gene dosage"), para)[0] == "fresh"
    assert c.lookup("k", J.evidence_sha("Ube3a   gene  dosage"), para)[0] == "fresh"


def test_two_modules_same_sentence_share_one_verdict(tmp_path, fake_paper, capture):
    # the real bug: module A cites `*Ube3a* gene dosage`, module B cites `Ube3a gene dosage`
    # for the SAME paraphrase. They must resolve to ONE shared, fresh verdict — no stale
    # ping-pong where recording B stales A.
    fake_paper.text = "Background: *Ube3a* gene dosage is tightly controlled in neurons."
    para = "Ube3a dosage is tightly regulated"
    cache = J.JudgmentCache()
    cache.put(citekey="noor2015q", evidence_sha_=J.evidence_sha("*Ube3a* gene dosage"),
              paraphrase=para, judge_id="subagent", supported=True,
              rationale="states tight control", timestamp="t", tier=1)
    grounding.set_judgment_cache(cache)
    # module A (markdown quote) — fresh
    recA = grounding.source("noor2015q", quote="*Ube3a* gene dosage", paraphrase=para)
    assert recA["judge_status"] == "fresh" and recA["supported"] is True
    # module B (plain quote, same sentence) — ALSO fresh off the same cached verdict
    recB = grounding.source("noor2015q", quote="Ube3a gene dosage", paraphrase=para)
    assert recB["judge_status"] == "fresh" and recB["supported"] is True
    assert recA["evidence_sha"] == recB["evidence_sha"]            # one identity


# --------------------------------------------------------------------------- #
# divergence lint — genuinely different spans for one (citekey, paraphrase) are flagged
# --------------------------------------------------------------------------- #
def _report_at(dirpath: Path, sources, *, node="test_lit") -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    claim = {"id": f"claims/test_literature.py::{node}", "statement": "A fact.",
             "outcome": "passed", "kind": "literature", "strength": "moderate",
             "caveats": None, "reviewed": None,
             "evidence": {"lit_sources": sources}, "inputs": [], "reconcile": []}
    p = dirpath / "grounding_report.json"
    p.write_text(json.dumps({"claims": [claim]}), encoding="utf-8")
    return p


def test_divergence_lint_flags_different_spans(tmp_path):
    # two modules, same (citekey, paraphrase), GENUINELY different sentences → flagged
    para = "Ube3a dosage matters"
    a = _report_at(tmp_path / "K1-A" / "analysis",
                   [{"citekey": "noor2015q", "paraphrase": para, "tier": 1,
                     "quote": "Ube3a dosage is tightly controlled.",
                     "span": "Ube3a dosage is tightly controlled."}], node="claim_a")
    b = _report_at(tmp_path / "K1-B" / "analysis",
                   [{"citekey": "noor2015q", "paraphrase": para, "tier": 1,
                     "quote": "Overexpression of Ube3a is deleterious.",
                     "span": "Overexpression of Ube3a is deleterious."}], node="claim_b")
    warnings = REFRESH.divergence_lint([a, b])
    assert len(warnings) == 1
    w = warnings[0]
    assert w["citekey"] == "noor2015q" and w["paraphrase"] == para
    assert len(w["spans"]) == 2
    assert len(w["where"]) == 2


def test_divergence_lint_ignores_markdown_only_variants(tmp_path):
    # the SAME sentence cited with/without markdown is NOT a divergence (folds equal)
    para = "Ube3a dosage matters"
    a = _report_at(tmp_path / "K1-A" / "analysis",
                   [{"citekey": "noor2015q", "paraphrase": para, "tier": 1,
                     "quote": "*Ube3a* dosage is tightly controlled.",
                     "span": "*Ube3a* dosage is tightly controlled."}], node="claim_a")
    b = _report_at(tmp_path / "K1-B" / "analysis",
                   [{"citekey": "noor2015q", "paraphrase": para, "tier": 1,
                     "quote": "Ube3a dosage is tightly controlled.",
                     "span": "Ube3a dosage is tightly controlled."}], node="claim_b")
    assert REFRESH.divergence_lint([a, b]) == []


def test_divergence_lint_clean_when_single_span(tmp_path):
    a = _report_at(tmp_path / "K1-A" / "analysis", [_tier1_src()])
    assert REFRESH.divergence_lint([a]) == []
    assert "no divergent" in REFRESH.render_divergence([])


def test_end_to_end_list_record_then_source(tmp_path, fake_paper, capture):
    rp = _report_json(tmp_path, [_tier1_src()])
    work = REFRESH.worklist(rp)
    assert len(work["items"]) == 1
    # the caller (a fresh-context judge subagent) decides; echo the worklist back as a verdict
    verdicts = [{"citekey": it["citekey"], "paraphrase": it["paraphrase"],
                 "evidence_sha": it["evidence_sha"], "supported": True,
                 "rationale": "the span states the paraphrase"} for it in work["items"]]
    REFRESH.record_verdicts(rp, verdicts, judge_id="judge-subagent")
    cache = J.JudgmentCache.load(tmp_path / "program" / "analysis" / J.JUDGMENT_CACHE_NAME)
    grounding.set_judgment_cache(cache)
    rec = grounding.source("noor2015q", quote="53% knockdown", paraphrase="about half knockdown")
    assert rec["judge_status"] == "fresh" and rec["supported"] is True
    assert rec["judged_by"] == "judge-subagent"
