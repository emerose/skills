"""Unit tests for the prose-quantity advisory layer (non-blocking §3 recall aid).

These pin the scoping decisions that were tuned against false positives on real reports:
paragraph (not line) scope, the report-wide restatement filter, skipping ``[report:]``
paragraphs, cited-paragraphs-only, and ×↔% normalization with a rounding tolerance.
"""

from reportkit import report as R


# --- pure number extraction ------------------------------------------------- #

def test_quantities_parses_pct_fold_and_ranges():
    q = R._quantities("knockdown of 60% and ~2.5× over WT, with a 2–2.5× band and 30-fold rise")
    assert 60.0 in q
    assert 250.0 in q          # 2.5× -> 250
    assert 200.0 in q and 250.0 in q   # the 2–2.5× range, both ends
    assert 3000.0 in q         # 30-fold -> 3000


def test_quantities_ignores_non_evidentiary_numbers():
    # years, n=, p-values, postnatal days, loci — none carry %/×/fold
    q = R._quantities("in 2021, n=6 animals at P21, p<0.001, locus 15q11-q13")
    assert q == set()


def test_to_pct_normalization():
    assert R._to_pct(2.0, "×") == 200.0
    assert R._to_pct(48.0, "%") == 48.0


# --- claim number harvesting ------------------------------------------------ #

def test_claim_quantities_from_data_evidence_and_statement():
    claim = {"statement": "reduces it to ~117% of WT",
             "evidence": {"oe_vehicle_pct_of_WT": 224.8, "pct_reduction": 47.8, "max_p": 0.03}}
    nums = R._claim_quantities(claim)
    assert 224.8 in nums and 47.8 in nums   # structured evidence leaves
    assert 117.0 in nums                    # %/× number mined from the statement


def test_claim_quantities_from_literature_quotes():
    claim = {"statement": "buffered to ~229% at two extra copies",
             "evidence": {"lit_sources": [{"quote": "just over 300% of the UBE3A protein level"},
                                          {"quote": "500% of WT brain UBE3A content would be expected"}]}}
    nums = R._claim_quantities(claim)
    assert 229.0 in nums and 300.0 in nums and 500.0 in nums


# --- the advisory pass ------------------------------------------------------ #

def _index(*claims):
    """Build a claim_index keyed like a grounding report (``exp::file::node``)."""
    return {f"K1::t.py::{c['node']}": {**c, "id": f"t.py::{c['node']}"} for c in claims}


def test_flags_derived_number_no_claim_asserts():
    idx = _index({"node": "test_kd", "statement": "~48% knockdown",
                  "kind": "result", "evidence": {"pct_reduction": 48.0}})
    adv = R.prose_quantity_advisories("Ceiling near 75% knockdown [claim:test_kd].", idx)
    assert len(adv) == 1 and adv[0]["value"] == 75.0


def test_does_not_flag_a_supported_number():
    idx = _index({"node": "test_kd", "statement": "~48% knockdown",
                  "kind": "result", "evidence": {"pct_reduction": 48.0}})
    assert R.prose_quantity_advisories("Knockdown of ~48% [claim:test_kd].", idx) == []


def test_rounding_tolerance_two_fold_matches_measured_pct():
    # 2× (=200) should match a measured 224.8% within the 15% tolerance — not flagged.
    idx = _index({"node": "test_base", "statement": "baseline",
                  "kind": "result", "evidence": {"pct_of_WT": 224.8}})
    assert R.prose_quantity_advisories("Baseline ~2× of wild-type [claim:test_base].", idx) == []


def test_restatement_filter_number_backed_elsewhere():
    # 75% is not in test_kd, but another cited claim (in a second paragraph) asserts it →
    # report-wide pool covers it → not flagged.
    idx = _index({"node": "test_kd", "statement": "~48%", "kind": "result",
                  "evidence": {"pct_reduction": 48.0}},
                 {"node": "test_other", "statement": "a 75% effect", "kind": "result",
                  "evidence": {"pct": 75.0}})
    text = "Ceiling near 75% [claim:test_kd].\n\nElsewhere, a 75% effect [claim:test_other]."
    assert R.prose_quantity_advisories(text, idx) == []


def test_uncited_paragraph_not_flagged():
    idx = _index({"node": "test_kd", "statement": "~48%", "kind": "result",
                  "evidence": {"pct_reduction": 48.0}})
    assert R.prose_quantity_advisories("The ceiling is near 75% knockdown.", idx) == []


def test_report_cite_paragraph_skipped():
    idx = _index({"node": "test_kd", "statement": "~48%", "kind": "result",
                  "evidence": {"pct_reduction": 48.0}})
    text = "Baseline ~2× [report:program::gene-dose] implies 75% [claim:test_kd]."
    assert R.prose_quantity_advisories(text, idx) == []


def test_fenced_block_not_scanned():
    idx = _index({"node": "test_kd", "statement": "~48%", "kind": "result",
                  "evidence": {"pct_reduction": 48.0}})
    text = "```\nknockdown of 99% [claim:test_kd]\n```"
    assert R.prose_quantity_advisories(text, idx) == []


def test_paragraph_scope_joins_wrapped_lines():
    # the number and its citation on different wrapped lines of one paragraph: not a
    # false positive (this was the line-scope bug).
    idx = _index({"node": "test_2x", "statement": "at the ~2× level the phenotype is partial",
                  "kind": "literature",
                  "evidence": {"lit_sources": [{"quote": "the disease-relevant 2x level"}]}})
    text = "At the disease-relevant ~2x level the phenotype\nis partial [claim:test_2x]."
    assert R.prose_quantity_advisories(text, idx) == []
