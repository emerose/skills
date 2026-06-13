"""Unit tests for _prose: the prose↔claims enforcement (ROADMAP §3).

Detection is inverted to the caller (the semantic-pass agent supplies the
quantitative assertions), so these tests feed assertions directly and exercise the
deterministic part: citation parsing, claim resolution, and grounding/strength
checks. The store-free `sci enforce-prose` wiring is exercised too."""

from scientist.store import _prose
from scientist.store import cli as STORE_CLI


def _claim(cid, statement, outcome="passed", strength="strong", kind="result"):
    return {"claim_id": cid, "statement": statement,
            "outcome": outcome, "strength": strength, "claim_kind": kind}


# --------------------------------------------------------------------------- #
# enforcement — the deterministic core
# --------------------------------------------------------------------------- #
def test_backed_assertion_passes():
    a = [{"text": "Knockdown reached 82% [claim:K1-1::test_kd.py::test_kd_lumbar].", "line": 4}]
    claims = [_claim("K1-1::test_kd.py::test_kd_lumbar", "Lumbar knockdown exceeds 80%")]
    res = _prose.enforce_prose(a, claims, source="K1-1/README.md")
    assert res["assertions"] == 1
    assert res["backed"] == 1
    assert res["flags"] == []


def test_bare_string_assertions_accepted():
    res = _prose.enforce_prose(["Knockdown reached 82% [claim:test_kd_lumbar]."],
                               [_claim("K1-1::test_kd.py::test_kd_lumbar", "x")])
    assert res["backed"] == 1 and res["flags"] == []


def test_citation_resolves_by_trailing_node():
    a = ["Knockdown reached 82% [claim:test_kd_lumbar]."]   # node-name only
    claims = [_claim("K1-1::test_kd.py::test_kd_lumbar", "Lumbar knockdown exceeds 80%")]
    res = _prose.enforce_prose(a, claims)
    assert res["backed"] == 1 and res["flags"] == []


def test_unbacked_assertion_flagged():
    a = [{"text": "Knockdown reached 82% in the lumbar cord.", "line": 7}]
    res = _prose.enforce_prose(a, [], source="K1-1/README.md")
    assert res["backed"] == 0
    assert len(res["flags"]) == 1
    flag = res["flags"][0]
    assert flag["status"] == "unbacked"
    assert flag["line"] == 7


def test_unbacked_carries_advisory_suggestion_but_does_not_clear():
    a = ["Lumbar knockdown reached 82% across replicates."]
    claims = [_claim("K1-1::test_kd.py::test_kd_lumbar",
                     "Lumbar knockdown across replicates exceeds eighty percent")]
    res = _prose.enforce_prose(a, claims)
    # an overlapping claim must NOT silently back an un-cited assertion
    assert res["backed"] == 0
    flag = res["flags"][0]
    assert flag["status"] == "unbacked"
    assert flag["suggestion"]["claim_id"] == "K1-1::test_kd.py::test_kd_lumbar"


def test_weak_backing_surfaced_with_outcome_and_strength():
    a = ["Knockdown reached 82% [claim:K1-1::test_kd.py::test_kd_lumbar]."]
    # the only backing is a contradicted (xfail) claim
    claims = [_claim("K1-1::test_kd.py::test_kd_lumbar", "Lumbar knockdown exceeds 80%",
                     outcome="xfail", strength="moderate")]
    res = _prose.enforce_prose(a, claims)
    assert res["backed"] == 0
    flag = res["flags"][0]
    assert flag["status"] == "weak-backing"
    assert flag["backing"][0]["outcome"] == "xfail"
    assert flag["backing"][0]["strength"] == "moderate"


def test_weak_strength_grounded_outcome_still_flagged():
    a = ["Knockdown reached 82% [claim:K1-1::test_kd.py::test_kd_lumbar]."]
    claims = [_claim("K1-1::test_kd.py::test_kd_lumbar", "Lumbar knockdown exceeds 80%",
                     outcome="passed", strength="weak")]
    res = _prose.enforce_prose(a, claims)
    assert res["flags"][0]["status"] == "weak-backing"
    assert res["flags"][0]["backing"][0]["strength"] == "weak"


def test_unknown_citation_flagged():
    a = ["Knockdown reached 82% [claim:K1-1::test_kd.py::nonexistent]."]
    claims = [_claim("K1-1::test_kd.py::test_kd_lumbar", "Lumbar knockdown exceeds 80%")]
    res = _prose.enforce_prose(a, claims)
    flag = res["flags"][0]
    assert flag["status"] == "unknown-claim"
    assert "K1-1::test_kd.py::nonexistent" in flag["cited"]


def test_moderate_grounded_claim_clears():
    a = ["Knockdown reached 82% [claim:test_kd_lumbar]."]
    claims = [_claim("K1-1::test_kd.py::test_kd_lumbar", "x", outcome="xpass", strength="moderate")]
    res = _prose.enforce_prose(a, claims)
    assert res["backed"] == 1 and res["flags"] == []


def test_empty_assertions_is_clean():
    res = _prose.enforce_prose([], [_claim("K1-1::t.py::x", "y")])
    assert res == {"source": None, "assertions": 0, "backed": 0,
                   "blocking": 0, "advisory": 0, "flags": []}


# --------------------------------------------------------------------------- #
# severity tiers — quantitative blocks, qualitative is advisory
# --------------------------------------------------------------------------- #
def test_quantitative_unbacked_is_blocking_by_default():
    # a bare string defaults to quantitative → blocking
    res = _prose.enforce_prose(["Knockdown reached 82%."], [])
    f = res["flags"][0]
    assert f["status"] == "unbacked" and f["tier"] == "quantitative"
    assert f["severity"] == "blocking"
    assert res["blocking"] == 1 and res["advisory"] == 0


def test_qualitative_unbacked_is_advisory():
    a = [{"text": "The ASO was well tolerated.", "line": 3, "kind": "qualitative"}]
    res = _prose.enforce_prose(a, [])
    f = res["flags"][0]
    assert f["status"] == "unbacked" and f["tier"] == "qualitative"
    assert f["severity"] == "advisory"
    assert res["blocking"] == 0 and res["advisory"] == 1


def test_qualitative_assertion_can_be_backed():
    # qualitative claims are grounded/audited exactly like numeric ones
    a = [{"text": "Knockdown was sustained [claim:test_sustained].", "kind": "qualitative"}]
    claims = [_claim("K1-1::test_kd.py::test_sustained", "Knockdown persists through week 8")]
    res = _prose.enforce_prose(a, claims)
    assert res["backed"] == 1 and res["flags"] == []


def test_qualitative_bad_citation_still_blocks():
    # an explicit-but-bad citation is a checkable author error → blocking regardless of tier
    a = [{"text": "Well tolerated [claim:K1-1::test_tox.py::test_tox].", "kind": "qualitative"}]
    claims = [_claim("K1-1::test_tox.py::test_tox", "Tox seen", outcome="xfail", strength="weak")]
    res = _prose.enforce_prose(a, claims)
    f = res["flags"][0]
    assert f["status"] == "weak-backing" and f["tier"] == "qualitative"
    assert f["severity"] == "blocking"
    assert res["blocking"] == 1


def test_unknown_kind_defaults_to_blocking():
    # fail-safe: a typo'd/unknown kind must NOT silently downgrade to advisory
    a = [{"text": "Knockdown reached 82%.", "kind": "qualitatve"}]   # typo
    res = _prose.enforce_prose(a, [])
    assert res["flags"][0]["tier"] == "quantitative"
    assert res["blocking"] == 1 and res["advisory"] == 0


# --------------------------------------------------------------------------- #
# `sci enforce-prose` wiring (store-free, backed by the grounding report)
# --------------------------------------------------------------------------- #
def _make_exp(tmp_path):
    exp = tmp_path / "K1-000000 - Prose Demo"
    (exp / "reports").mkdir(parents=True)
    (exp / "analysis").mkdir()
    return exp


def _write_report(exp, claims):
    import json
    (exp / "analysis" / "grounding_report.json").write_text(
        json.dumps({"claims": claims}), encoding="utf-8")


def test_prose_docs_on_disk_lists_readme_and_reports(tmp_path):
    exp = _make_exp(tmp_path)
    (exp / "README.md").write_text("# R\n", encoding="utf-8")
    (exp / "reports" / "summary.md").write_text("# S\n", encoding="utf-8")
    (exp / "reports" / "data.csv").write_text("a,b\n", encoding="utf-8")  # not prose
    docs = set(STORE_CLI.prose_docs_on_disk(exp, tmp_path))
    assert docs == {"K1-000000 - Prose Demo/README.md",
                    "K1-000000 - Prose Demo/reports/summary.md"}


def test_run_enforce_prose_flags_unbacked(tmp_path, capsys):
    exp = _make_exp(tmp_path)
    rc = STORE_CLI.run_enforce_prose(
        str(exp), ["Knockdown reached 82% in the lumbar cord."],
        source="README.md", as_json=True)
    assert rc == 1                       # flagged → nonzero exit (gate)
    import json
    res = json.loads(capsys.readouterr().out)
    assert res["flags"][0]["status"] == "unbacked"


def test_run_enforce_prose_backs_from_grounding_report(tmp_path, capsys):
    exp = _make_exp(tmp_path)
    # report claim keyed by stable claim_id (exp_id + test-file + node)
    _write_report(exp, [{"id": "/abs/path/analysis/claims/test_kd.py::test_kd_lumbar",
                         "statement": "Lumbar knockdown exceeds 80%",
                         "outcome": "passed", "strength": "strong", "kind": "result"}])
    rc = STORE_CLI.run_enforce_prose(
        str(exp), ["Knockdown reached 82% [claim:test_kd_lumbar]."],
        source="README.md", as_json=True)
    assert rc == 0                       # fully backed → zero exit
    import json
    res = json.loads(capsys.readouterr().out)
    assert res["backed"] == 1 and res["flags"] == []


def test_run_enforce_prose_full_claim_id_resolves(tmp_path, capsys):
    exp = _make_exp(tmp_path)
    _write_report(exp, [{"id": "/abs/analysis/claims/test_kd.py::test_kd_lumbar",
                         "statement": "x", "outcome": "passed", "strength": "strong",
                         "kind": "result"}])
    rc = STORE_CLI.run_enforce_prose(
        str(exp),
        ["Knockdown reached 82% [claim:K1-000000::test_kd.py::test_kd_lumbar]."],
        as_json=True)
    assert rc == 0
    import json
    assert json.loads(capsys.readouterr().out)["backed"] == 1
