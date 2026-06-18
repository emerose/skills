"""Unit tests for the incommensurate-evidence advisory (non-blocking §3 recall aid).

These pin the heuristic: a load-bearing *bound* (a %/×/fold quantity in a cited paragraph)
backed ONLY by non-robust claim(s) is surfaced with the specific weakness named; a bound also
backed by one strong, independent, in-scope claim is NOT surfaced; and the advisory never flips
GROUNDED. The robustness signals are exactly the fields a ``grounding_report.json`` carries —
``strength`` per claim, and per literature source ``group`` (→ independent_groups), ``test``,
``primary``, ``mode``, ``tier`` — plus the ``interpretive`` / ``external`` claim kinds.
"""

from scientist.provenance import report as R


def _index(*claims):
    """Build a claim_index keyed like a grounding report (``exp::file::node``)."""
    return {f"K1::t.py::{c['node']}": {**c, "id": f"t.py::{c['node']}"} for c in claims}


def _lit(node, statement, *, strength="moderate", sources):
    return {"node": node, "statement": statement, "kind": "literature",
            "strength": strength, "evidence": {"lit_sources": list(sources)}}


# --- per-claim robustness signals ------------------------------------------- #

def test_strong_multigroup_direct_claim_is_robust():
    c = _lit("test_x", "x", strength="strong", sources=[
        {"group": "elgersma", "test": "direct", "primary": True, "mode": "fulltext", "tier": 1},
        {"group": "philpot", "test": "direct", "primary": True, "mode": "fulltext", "tier": 1}])
    assert R.claim_robustness_weaknesses(c) == []


def test_single_group_is_flagged():
    # the motivating failure: a literature claim resting on one lab ("all one lab").
    c = _lit("test_x", "x", strength="strong", sources=[
        {"group": "elgersma", "test": "direct", "primary": True, "mode": "fulltext", "tier": 1},
        {"group": "elgersma", "test": "direct", "primary": True, "mode": "fulltext", "tier": 1}])
    assert "single-group" in R.claim_robustness_weaknesses(c)


def test_independent_groups_default_to_citekey():
    # no explicit `group` → falls back to citekey; two distinct citekeys ⇒ not single-group.
    c = _lit("test_x", "x", strength="strong", sources=[
        {"citekey": "a2020", "test": "direct", "primary": True, "mode": "fulltext", "tier": 1},
        {"citekey": "b2021", "test": "direct", "primary": True, "mode": "fulltext", "tier": 1}])
    assert "single-group" not in R.claim_robustness_weaknesses(c)


def test_legacy_reviewed_independent_groups():
    c = {"node": "test_x", "statement": "x", "kind": "literature", "strength": "strong",
         "evidence": {"lit_sources": []}, "reviewed": {"independent_groups": 1}}
    assert "single-group" in R.claim_robustness_weaknesses(c)


def test_moderate_strength_is_a_weakness():
    c = {"node": "test_x", "statement": "x", "kind": "result", "strength": "moderate",
         "evidence": {"pct": 50.0}}
    assert R.claim_robustness_weaknesses(c) == ["strength=moderate"]


def test_suggestive_secondary_abstract_weak_locator_flagged():
    c = _lit("test_x", "x", strength="strong", sources=[
        {"group": "a", "test": "suggestive", "primary": False, "mode": "abstract", "tier": 3},
        {"group": "b", "test": "direct", "primary": True, "mode": "fulltext", "tier": 1}])
    w = R.claim_robustness_weaknesses(c)
    assert "suggestive-source" in w and "secondary-source" in w
    assert "abstract-only" in w and "weak-locator" in w


def test_interpretive_and_external_kinds_flagged():
    assert "interpretive" in R.claim_robustness_weaknesses(
        {"node": "i", "kind": "interpretive", "strength": "strong", "evidence": {}})
    assert "external" in R.claim_robustness_weaknesses(
        {"node": "e", "kind": "external", "strength": "strong", "evidence": {}})


# --- the advisory pass ------------------------------------------------------ #

def test_bound_on_single_group_claim_is_flagged():
    # the motivating shape: a ceiling/bound built on a single-lab, moderate, suggestive claim.
    idx = _index(_lit("test_floor", "~50% loss tolerated", strength="moderate", sources=[
        {"group": "elgersma", "test": "suggestive", "primary": True, "mode": "fulltext", "tier": 1}]))
    adv = R.incommensurate_evidence_advisories(
        "The safety ceiling sits near 50% knockdown [claim:test_floor].", idx)
    assert len(adv) == 1
    a = adv[0]
    assert a["kind"] == "weak-load-bearing" and 50.0 in a["value"]
    ws = a["weaknesses"]["K1::floor"]      # short id: test-file + `test_` prefix dropped
    assert "strength=moderate" in ws and "single-group" in ws and "suggestive-source" in ws


def test_bound_with_one_robust_backing_not_flagged():
    # the same bound, but ALSO backed by a strong, multi-group, direct claim → cleared.
    idx = _index(
        _lit("test_floor", "~50% loss tolerated", strength="moderate", sources=[
            {"group": "elgersma", "test": "suggestive", "primary": True, "mode": "fulltext", "tier": 1}]),
        _lit("test_robust", "tolerated to ~50%", strength="strong", sources=[
            {"group": "a", "test": "direct", "primary": True, "mode": "fulltext", "tier": 1},
            {"group": "b", "test": "direct", "primary": True, "mode": "fulltext", "tier": 1}]))
    text = "The ceiling sits near 50% [claim:test_floor][claim:test_robust]."
    assert R.incommensurate_evidence_advisories(text, idx) == []


def test_paragraph_without_a_bound_not_flagged():
    # no %/×/fold quantity → no load-bearing bound proxy → nothing to flag, even if weak.
    idx = _index(_lit("test_w", "loss is tolerated", strength="weak", sources=[
        {"group": "a", "test": "suggestive", "primary": True, "mode": "fulltext", "tier": 1}]))
    assert R.incommensurate_evidence_advisories(
        "Substantial loss appears tolerated [claim:test_w].", idx) == []


def test_uncited_and_report_cite_paragraphs_skipped():
    idx = _index(_lit("test_w", "~50%", strength="weak", sources=[
        {"group": "a", "test": "suggestive", "primary": True, "mode": "fulltext", "tier": 1}]))
    assert R.incommensurate_evidence_advisories("A ceiling near 50%.", idx) == []
    assert R.incommensurate_evidence_advisories(
        "Baseline 50% [report:program::gene-dose] is fine.", idx) == []


def test_paragraph_scope_joins_wrapped_lines():
    idx = _index(_lit("test_floor", "~50% tolerated", strength="moderate", sources=[
        {"group": "elgersma", "test": "direct", "primary": True, "mode": "fulltext", "tier": 1}]))
    text = "The ceiling sits near 50% knockdown\nper the floor [claim:test_floor]."
    adv = R.incommensurate_evidence_advisories(text, idx)
    assert len(adv) == 1 and 50.0 in adv[0]["value"]


# --- the payload surfaced to the §3 subagent (strength + review note) ------- #

def test_payload_carries_strength_and_review_note():
    # The key change: the advisory hands the reviewer each flagged claim's strength and its review
    # note (the "all one lab" caveat the author already wrote), not just a bare weakness tag.
    claim = _lit("test_floor", "~50% loss tolerated", strength="moderate", sources=[
        {"group": "elgersma", "test": "suggestive", "primary": True, "mode": "fulltext", "tier": 1}])
    claim["reviewed"] = {"independent_groups": 1, "note": "all one lab; prenatal model"}
    idx = _index(claim)
    adv = R.incommensurate_evidence_advisories(
        "The ceiling sits near 50% [claim:test_floor].", idx)
    assert len(adv) == 1
    rec = adv[0]["claims"][0]
    assert rec["strength"] == "moderate"
    assert rec["note"] == "all one lab; prenatal model"
    assert "single-group" in rec["weaknesses"]


def test_machine_judged_claim_has_no_note_but_carries_strength():
    # Machine-judged claims carry no review note (reviewed is null) — only per-source signals.
    # The payload still surfaces strength + the structural weaknesses; note is None.
    idx = _index(_lit("test_floor", "~50%", strength="moderate", sources=[
        {"group": "elgersma", "test": "suggestive", "primary": True, "mode": "fulltext", "tier": 1}]))
    adv = R.incommensurate_evidence_advisories(
        "The ceiling sits near 50% [claim:test_floor].", idx)
    rec = adv[0]["claims"][0]
    assert rec["note"] is None and rec["strength"] == "moderate"


def test_review_note_falls_back_to_top_level_note_and_caveats():
    c = {"node": "n", "kind": "literature", "strength": "moderate",
         "evidence": {"lit_sources": []}, "caveats": "  contested  result  "}
    assert R._review_note(c) == "contested result"
    c2 = {"node": "n", "kind": "literature", "strength": "moderate",
          "evidence": {"lit_sources": []}, "note": "single study"}
    assert R._review_note(c2) == "single study"
    assert R._review_note({"node": "n", "kind": "result", "strength": "strong",
                           "evidence": {}}) is None


def test_advisory_present_in_audit_and_never_flips_grounded(tmp_path):
    # End-to-end: a real grounding report + a report citing a single-group moderate claim with a
    # bound. The audit must surface a weak-load-bearing advisory but stay GROUNDED.
    import json

    exp = tmp_path / "K1-000001 - Demo"
    (exp / "analysis").mkdir(parents=True)
    grounding = [
        {"id": "claims/test_lit.py::test_floor",
         "kind": "literature", "outcome": "passed", "strength": "moderate",
         "statement": "~50% UBE3A loss is tolerated",
         "evidence": {"lit_sources": [
             {"group": "elgersma", "test": "suggestive", "primary": True,
              "mode": "fulltext", "tier": 1, "paraphrase": "tolerated",
              "judge_status": "fresh", "supported": True, "quote": "around half"}]}}]
    (exp / "analysis" / "grounding_report.json").write_text(
        json.dumps({"claims": grounding}), encoding="utf-8")

    reports = exp / "reports" / "demo"
    reports.mkdir(parents=True)
    rp = reports / "report.md"
    rp.write_text("# Demo\n\nThe safety ceiling sits near 50% [claim:test_floor].\n",
                  encoding="utf-8")

    result = R.audit(rp, home=tmp_path)
    assert result["status"] == "GROUNDED"
    kinds = [a["kind"] for a in result["advisories"]]
    assert "weak-load-bearing" in kinds


def test_render_audit_shows_strength_note_and_does_not_crash_on_bound_list():
    # render_audit must handle a weak-load-bearing advisory (its `value` is a *list*, unlike
    # unsupported-quantity) and print the surfaced strength + review note.
    result = {
        "report": "demo/report.md", "status": "GROUNDED", "scope": "experiment",
        "citations": [], "embeds": [], "report_cites": [], "lit_cites": [], "findings": [],
        "advisories": [{
            "kind": "weak-load-bearing", "line": 3, "value": [50.0],
            "cites": ["K1::floor"],
            "weaknesses": {"K1::floor": ["strength=moderate", "single-group"]},
            "claims": [{"cite": "K1::floor", "strength": "moderate",
                        "weaknesses": ["strength=moderate", "single-group"],
                        "note": "all one lab; prenatal model"}],
            "sentence": "The ceiling sits near 50%."}]}
    out = R.render_audit(result)
    assert "weak-load-bearing" in out
    assert "single-group" in out
    assert "all one lab" in out
    assert "1 weak-load-bearing" in out      # summary counts by kind, not hard-coded label
