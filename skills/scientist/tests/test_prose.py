"""Unit tests for _prose: the quantitative-assertion detector and the prose↔claims
enforcement (ROADMAP §3). The store-free audit wiring is exercised too."""

from scientist.store import _prose
from scientist.store import cli as STORE_CLI


# --------------------------------------------------------------------------- #
# detector — conservative false-positive posture
# --------------------------------------------------------------------------- #
def test_detects_result_like_numbers():
    md = (
        "Knockdown reached 82% in the lumbar cord.\n"
        "We observed a 3-fold increase in target engagement.\n"
        "The effect was significant (p < 0.01).\n"
        "IC50 was 12 nM against the primary target.\n"
        "Dosing was 30 mg/kg weekly.\n"
    )
    found = {a["line"]: a["text"] for a in _prose.find_quantitative_assertions(md)}
    assert set(found) == {1, 2, 3, 4, 5}


def test_ignores_non_result_numbers():
    md = (
        "See Figure 3 and section 4 for the layout.\n"           # refs
        "Cells were incubated for 30 min at 37 °C over 3 days.\n"  # method time/temp
        "This is version 2 of the protocol, with 6 animals.\n"    # version + bare count
        "The README was last updated in 2024.\n"                  # date
    )
    assert _prose.find_quantitative_assertions(md) == []


def test_ignores_numbers_in_code_and_deps_comment():
    md = (
        "Setup: `n=3` replicates per group as shown in `df[p<0.05]`.\n"
        "<!-- scientist:deps file=data/kd.csv sha256=99% -->\n"
        "```\nresidual = 12 nM  # 80% knockdown\n```\n"
    )
    assert _prose.find_quantitative_assertions(md) == []


# --------------------------------------------------------------------------- #
# enforcement
# --------------------------------------------------------------------------- #
def _claim(cid, statement, outcome="passed", strength="strong", kind="result"):
    return {"claim_id": cid, "statement": statement,
            "outcome": outcome, "strength": strength, "claim_kind": kind}


def test_backed_assertion_passes():
    md = "Knockdown reached 82% in the lumbar cord [claim:K1-1::test_kd.py::test_kd_lumbar]."
    claims = [_claim("K1-1::test_kd.py::test_kd_lumbar", "Lumbar knockdown exceeds 80%")]
    res = _prose.enforce_prose(md, claims, source="K1-1/README.md")
    assert res["assertions"] == 1
    assert res["backed"] == 1
    assert res["flags"] == []


def test_citation_resolves_by_trailing_node():
    md = "Knockdown reached 82% [claim:test_kd_lumbar]."   # node-name only
    claims = [_claim("K1-1::test_kd.py::test_kd_lumbar", "Lumbar knockdown exceeds 80%")]
    res = _prose.enforce_prose(md, claims)
    assert res["backed"] == 1 and res["flags"] == []


def test_unbacked_assertion_flagged():
    md = "Knockdown reached 82% in the lumbar cord."
    res = _prose.enforce_prose(md, [], source="K1-1/README.md")
    assert res["backed"] == 0
    assert len(res["flags"]) == 1
    flag = res["flags"][0]
    assert flag["status"] == "unbacked"
    assert flag["line"] == 1
    assert "82%" in flag["matches"]


def test_unbacked_carries_advisory_suggestion_but_does_not_clear():
    md = "Lumbar knockdown reached 82% across replicates."
    claims = [_claim("K1-1::test_kd.py::test_kd_lumbar",
                     "Lumbar knockdown across replicates exceeds eighty percent")]
    res = _prose.enforce_prose(md, claims)
    # an overlapping claim must NOT silently back an un-cited assertion
    assert res["backed"] == 0
    flag = res["flags"][0]
    assert flag["status"] == "unbacked"
    assert flag["suggestion"]["claim_id"] == "K1-1::test_kd.py::test_kd_lumbar"


def test_weak_backing_surfaced_with_outcome_and_strength():
    md = "Knockdown reached 82% [claim:K1-1::test_kd.py::test_kd_lumbar]."
    # the only backing is a contradicted (xfail) claim
    claims = [_claim("K1-1::test_kd.py::test_kd_lumbar", "Lumbar knockdown exceeds 80%",
                     outcome="xfail", strength="moderate")]
    res = _prose.enforce_prose(md, claims)
    assert res["backed"] == 0
    flag = res["flags"][0]
    assert flag["status"] == "weak-backing"
    assert flag["backing"][0]["outcome"] == "xfail"
    assert flag["backing"][0]["strength"] == "moderate"


def test_weak_strength_grounded_outcome_still_flagged():
    md = "Knockdown reached 82% [claim:K1-1::test_kd.py::test_kd_lumbar]."
    claims = [_claim("K1-1::test_kd.py::test_kd_lumbar", "Lumbar knockdown exceeds 80%",
                     outcome="passed", strength="weak")]
    res = _prose.enforce_prose(md, claims)
    assert res["flags"][0]["status"] == "weak-backing"
    assert res["flags"][0]["backing"][0]["strength"] == "weak"


def test_unknown_citation_flagged():
    md = "Knockdown reached 82% [claim:K1-1::test_kd.py::nonexistent]."
    claims = [_claim("K1-1::test_kd.py::test_kd_lumbar", "Lumbar knockdown exceeds 80%")]
    res = _prose.enforce_prose(md, claims)
    flag = res["flags"][0]
    assert flag["status"] == "unknown-claim"
    assert "K1-1::test_kd.py::nonexistent" in flag["cited"]


def test_moderate_grounded_claim_clears():
    md = "Knockdown reached 82% [claim:test_kd_lumbar]."
    claims = [_claim("K1-1::test_kd.py::test_kd_lumbar", "x", outcome="xpass", strength="moderate")]
    res = _prose.enforce_prose(md, claims)
    assert res["backed"] == 1 and res["flags"] == []


# --------------------------------------------------------------------------- #
# doc discovery + audit wiring (store-free)
# --------------------------------------------------------------------------- #
def _make_exp(tmp_path):
    exp = tmp_path / "K1-000000 - Prose Demo"
    (exp / "reports").mkdir(parents=True)
    (exp / "analysis").mkdir()
    return exp


def test_iter_prose_docs_picks_readme_and_reports(tmp_path):
    exp = _make_exp(tmp_path)
    (exp / "README.md").write_text("# R\n", encoding="utf-8")
    (exp / "reports" / "summary.md").write_text("# S\n", encoding="utf-8")
    (exp / "reports" / "data.csv").write_text("a,b\n", encoding="utf-8")  # not prose
    labels = {label for label, _ in _prose.iter_prose_docs(exp, tmp_path)}
    assert labels == {"K1-000000 - Prose Demo/README.md",
                      "K1-000000 - Prose Demo/reports/summary.md"}


def test_audit_report_storefree_flags_unbacked_prose(tmp_path):
    exp = _make_exp(tmp_path)
    (exp / "README.md").write_text("Knockdown reached 82% in the lumbar cord.\n",
                                   encoding="utf-8")
    report = STORE_CLI.audit_report(tmp_path, only=None)
    entry = next(e for e in report if e["exp_id"] == "K1-000000")
    assert "prose" in entry
    flags = entry["prose"][0]["flags"]
    assert flags[0]["status"] == "unbacked"


def test_audit_report_storefree_backs_prose_from_grounding_report(tmp_path):
    import json

    exp = _make_exp(tmp_path)
    (exp / "README.md").write_text(
        "Knockdown reached 82% [claim:test_kd_lumbar].\n", encoding="utf-8")
    # a grounding report whose claim (keyed by stable claim_id) backs the prose
    (exp / "analysis" / "grounding_report.json").write_text(json.dumps({"claims": [
        {"id": "/abs/path/analysis/claims/test_kd.py::test_kd_lumbar",
         "statement": "Lumbar knockdown exceeds 80%",
         "outcome": "passed", "strength": "strong", "kind": "result"}]}), encoding="utf-8")
    report = STORE_CLI.audit_report(tmp_path, only=None)
    entry = next(e for e in report if e["exp_id"] == "K1-000000")
    assert entry["prose"][0]["backed"] == 1
    assert entry["prose"][0]["flags"] == []
