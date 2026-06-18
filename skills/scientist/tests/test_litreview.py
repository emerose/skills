"""The litreview phase — ``provenance.litreview`` (``sci litreview``) and the ``[litreview:]``
omissions audit in ``provenance.report``.

A *litreview* (``kind=litreview``) is a neutral, thesis-independent survey of the third-party
literature on one sub-question. It marks a **must-confront** subset (the pivotal/contested/
disconfirming claims), and a report that cites it via ``[litreview:<id>]`` must **address** each
must-confront claim (cite it, or ``[litreview-waive:<id>]`` it) — the omissions audit.

Pure: synthetic ``program/`` trees in tmp dirs — a hand-written ``grounding_report.json`` with
``[lit:]`` claims (some ``must_confront``), a ``review.md``, and a ``report.md``. No keys, no
libkit store, no paper library, no model.
"""
import json
from pathlib import Path

import pytest

from scientist import grounding
from scientist.grounding import plugin as PLUGIN
from scientist.provenance import litreview as LR
from scientist.provenance import report as R


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def _lit_claim(node: str, *, slug_mod: str, statement: str, must_confront=None,
               outcome="passed", strength="moderate", support=True, groups=2) -> dict:
    """A literature claim record shaped like the plugin emits (legacy @reviewed path)."""
    return {
        "id": f"program/claims/test_litreview_{slug_mod}.py::{node}",
        "statement": statement,
        "outcome": outcome, "kind": "literature", "strength": strength, "caveats": None,
        "reviewed": {"support": support, "primary": True, "independent_groups": groups},
        "must_confront": must_confront,
        "evidence": {"lit_sources": [
            {"citekey": f"{node}2020", "quote": "q", "primary": True, "group": node}]},
        "inputs": [], "reconcile": [],
    }


def _program(tmp_path: Path, slug: str = "it-biodist") -> Path:
    """A program/ tree: a grounding report with two must-confront [lit:] claims + one ordinary
    claim, and a litreview folder. ``slug`` is hyphenated; the claim module underscores it."""
    mod = slug.replace("-", "_")
    prog = tmp_path / "program"
    (prog / "analysis").mkdir(parents=True, exist_ok=True)
    (prog / "litreviews" / slug).mkdir(parents=True, exist_ok=True)
    claims = [
        _lit_claim("test_floor", slug_mod=mod, statement="~50% loss is tolerated prenatally",
                   must_confront="any dosing report must address the tolerated ~50% loss"),
        _lit_claim("test_ceiling", slug_mod=mod, statement="reciprocal dosing makes WT the optimum",
                   must_confront="the over-knockdown ceiling rests on this"),
        _lit_claim("test_minor", slug_mod=mod, statement="a minor corroborating detail"),
    ]
    (prog / "analysis" / "grounding_report.json").write_text(
        json.dumps({"claims": claims}, indent=2), encoding="utf-8")
    return prog


def _review_md(prog: Path, slug: str, body: str) -> Path:
    md = prog / "litreviews" / slug / "review.md"
    md.write_text(body, encoding="utf-8")
    return md


def _report_md(prog: Path, body: str, slug: str = "dosing") -> Path:
    d = prog / "reports" / slug
    d.mkdir(parents=True, exist_ok=True)
    md = d / "report.md"
    md.write_text(body, encoding="utf-8")
    return md


_GOOD_REVIEW = """---
title: "IT ASO biodistribution"
---

## Question & scope
How a lumbar ASO distributes across the CNS.

## Exposure gradient
The cord sees the most drug [lit:test_floor]; reciprocal dosing matters [lit:test_ceiling], with a
minor detail [lit:test_minor].

## Controversies / unresolved
Lab A and Lab B disagree on the deep-brain ratio [lit:test_floor].

## Gaps / open questions
Human region-to-region ratios are unmeasured.
"""


# --------------------------------------------------------------------------- #
# the @must_confront marker
# --------------------------------------------------------------------------- #
def test_must_confront_marker_is_registered_and_exported():
    assert "must_confront" in grounding.__all__
    assert callable(grounding.must_confront)
    assert "must_confront" in PLUGIN._MARKERS


def test_must_confront_marker_flows_into_grounding_report(tmp_path):
    """End-to-end: a @must_confront-decorated claim emits a ``must_confront`` field. Run in a
    subprocess so the grounding plugin (auto-loaded via its pytest11 entry point) emits the report
    without colliding with the outer session's already-registered plugin."""
    import subprocess
    import sys

    claims = tmp_path / "claims"
    claims.mkdir()
    (claims / "test_litreview_demo.py").write_text(
        "from scientist.grounding import kind, must_confront\n"
        "@kind('literature')\n"
        "@must_confront('a report here must address this')\n"
        "def test_pivotal():\n"
        "    '''a pivotal fact'''\n"
        "    assert True\n", encoding="utf-8")
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(claims), "--grounding-out", str(out)],
        capture_output=True, text=True)
    report = out / "grounding_report.json"
    if not report.is_file():
        pytest.skip(f"grounding plugin not active in subprocess (entry point not installed):\n"
                    f"{proc.stdout}\n{proc.stderr}")
    data = json.loads(report.read_text(encoding="utf-8"))
    rec = next(c for c in data["claims"] if c["id"].endswith("::test_pivotal"))
    assert rec["must_confront"] == "a report here must address this"


# --------------------------------------------------------------------------- #
# litreview.audit — the artifact's own contract
# --------------------------------------------------------------------------- #
def test_litreview_audit_grounded(tmp_path):
    prog = _program(tmp_path)
    review = _review_md(prog, "it-biodist", _GOOD_REVIEW)
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "GROUNDED", res["findings"]
    assert res["kind"] == "litreview"
    assert sorted(R._short_claim_id(c) for c in res["must_confront"]) == \
        ["program::ceiling", "program::floor"]
    assert res["has_controversy_section"] is True


def test_litreview_audit_missing_gaps_section_is_broken(tmp_path):
    prog = _program(tmp_path)
    body = _GOOD_REVIEW.replace("## Gaps / open questions\nHuman region-to-region ratios are unmeasured.", "")
    review = _review_md(prog, "it-biodist", body)
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "BROKEN"
    assert any(f["kind"] == "missing-gaps-section" for f in res["findings"])


def test_litreview_audit_rejects_kicho_data(tmp_path):
    prog = _program(tmp_path)
    body = _GOOD_REVIEW.replace("[lit:test_minor]", "[claim:test_minor]")
    review = _review_md(prog, "it-biodist", body)
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "BROKEN"
    assert any(f["kind"] == "kicho-data-in-litreview" for f in res["findings"])


def test_litreview_audit_empty_must_confront_is_advisory(tmp_path):
    prog = _program(tmp_path, slug="uncontested")
    # claims module has no must_confront → strip the tags by rewriting the grounding report.
    gr = prog / "analysis" / "grounding_report.json"
    data = json.loads(gr.read_text())
    for c in data["claims"]:
        c["must_confront"] = None
    gr.write_text(json.dumps(data), encoding="utf-8")
    review = _review_md(prog, "uncontested", _GOOD_REVIEW)
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "GROUNDED"          # empty set does NOT block
    assert any(a["kind"] == "empty-must-confront" for a in res["advisories"])


def test_must_confront_listing(tmp_path):
    prog = _program(tmp_path)
    review = _review_md(prog, "it-biodist", _GOOD_REVIEW)
    listing = LR.must_confront_listing(review, home=tmp_path)
    assert {R._short_claim_id(m["claim_id"]) for m in listing} == \
        {"program::floor", "program::ceiling"}
    assert all(m["reason"] for m in listing)


# --------------------------------------------------------------------------- #
# the [litreview:] omissions audit (in provenance.report)
# --------------------------------------------------------------------------- #
def test_omissions_audit_blocks_unaddressed_must_confront(tmp_path):
    prog = _program(tmp_path)
    _review_md(prog, "it-biodist", _GOOD_REVIEW)
    # A report that cites the litreview but addresses NEITHER must-confront claim.
    report = _report_md(prog, """---
title: "Dosing"
---
## Argument
We rely on the IT biodistribution survey [litreview:program::it-biodist] but cite nothing from it.
""")
    res = R.audit(report, home=tmp_path)
    assert res["status"] == "BROKEN"
    unaddressed = [f for f in res["findings"] if f["kind"] == "unaddressed-must-confront"]
    assert len(unaddressed) == 2
    lrc = res["litreview_cites"][0]
    assert lrc["verdict"] == "unaddressed-must-confront"
    assert len(lrc["unaddressed"]) == 2


def test_omissions_audit_satisfied_by_citing_each(tmp_path):
    prog = _program(tmp_path)
    _review_md(prog, "it-biodist", _GOOD_REVIEW)
    report = _report_md(prog, """---
title: "Dosing"
---
## Argument
Per the survey [litreview:program::it-biodist], ~50% loss is tolerated [lit:test_floor] and
reciprocal dosing sets the optimum [lit:test_ceiling].
""")
    res = R.audit(report, home=tmp_path)
    assert res["status"] == "GROUNDED", res["findings"]
    assert res["litreview_cites"][0]["verdict"] == "backed"


def test_omissions_audit_satisfied_by_waiver(tmp_path):
    prog = _program(tmp_path)
    _review_md(prog, "it-biodist", _GOOD_REVIEW)
    report = _report_md(prog, """---
title: "Dosing"
---
## Argument
Per the survey [litreview:program::it-biodist], ~50% loss is tolerated [lit:test_floor].

## Assumptions
- [litreview-waive:test_ceiling] out of scope — this report does not bound the over-knockdown ceiling.
""")
    res = R.audit(report, home=tmp_path)
    assert res["status"] == "GROUNDED", res["findings"]


def test_omissions_audit_missing_litreview(tmp_path):
    prog = _program(tmp_path)
    report = _report_md(prog, """---
title: "Dosing"
---
## Argument
Citing a survey that doesn't exist [litreview:program::nonexistent].
""")
    res = R.audit(report, home=tmp_path)
    assert res["status"] == "BROKEN"
    assert any(f["kind"] == "missing-litreview" for f in res["findings"])


def _dosing_report(prog: Path, pin: str | None = None, slug: str = "dosing") -> Path:
    """A report that cites the litreview and addresses both must-confront claims, optionally with a
    recorded ``litreview_pins`` front-matter stamp."""
    fm = '---\ntitle: "Dosing"\n'
    if pin is not None:
        fm += f'litreview_pins:\n  program::it-biodist: "{pin}"\n'
    fm += "---\n"
    body = fm + ("## Argument\nPer the survey [litreview:program::it-biodist], ~50% loss is "
                 "tolerated [lit:test_floor] and reciprocal dosing sets the optimum [lit:test_ceiling].\n")
    return _report_md(prog, body, slug=slug)


def _set_strength(prog: Path, node: str, strength: str) -> None:
    gr = prog / "analysis" / "grounding_report.json"
    data = json.loads(gr.read_text(encoding="utf-8"))
    for c in data["claims"]:
        if c["id"].endswith(f"::{node}"):
            c["strength"] = strength
    gr.write_text(json.dumps(data), encoding="utf-8")


# --------------------------------------------------------------------------- #
# staleness pin
# --------------------------------------------------------------------------- #
def test_litreview_pin_unrecorded_is_advisory(tmp_path):
    prog = _program(tmp_path)
    _review_md(prog, "it-biodist", _GOOD_REVIEW)
    res = R.audit(_dosing_report(prog), home=tmp_path)
    assert res["status"] == "GROUNDED", res["findings"]        # unpinned does NOT block
    lrc = res["litreview_cites"][0]
    assert lrc.get("pin_unrecorded") is True
    assert len(lrc["pin"]) == 12


def test_litreview_pin_recorded_is_clean(tmp_path):
    prog = _program(tmp_path)
    _review_md(prog, "it-biodist", _GOOD_REVIEW)
    pin = R.audit(_dosing_report(prog), home=tmp_path)["litreview_cites"][0]["pin"]
    res = R.audit(_dosing_report(prog, pin=pin), home=tmp_path)
    assert res["status"] == "GROUNDED", res["findings"]
    lrc = res["litreview_cites"][0]
    assert lrc["verdict"] == "backed"
    assert not lrc.get("pin_unrecorded")


def test_stale_litreview_on_cited_claim_drift(tmp_path):
    prog = _program(tmp_path)
    _review_md(prog, "it-biodist", _GOOD_REVIEW)
    pin = R.audit(_dosing_report(prog), home=tmp_path)["litreview_cites"][0]["pin"]
    _dosing_report(prog, pin=pin)
    _set_strength(prog, "test_floor", "weak")                  # drift a CITED claim
    res = R.audit(prog / "reports" / "dosing" / "report.md", home=tmp_path)
    assert res["status"] == "BROKEN"
    assert any(f["kind"] == "stale-litreview" for f in res["findings"])
    assert res["litreview_cites"][0]["verdict"] == "stale-litreview"


def test_stale_litreview_on_must_confront_membership_change(tmp_path):
    prog = _program(tmp_path)
    _review_md(prog, "it-biodist", _GOOD_REVIEW)
    pin = R.audit(_dosing_report(prog), home=tmp_path)["litreview_cites"][0]["pin"]
    _dosing_report(prog, pin=pin)
    # un-tag a must-confront claim → the obligation set membership changes → pin drifts.
    gr = prog / "analysis" / "grounding_report.json"
    data = json.loads(gr.read_text(encoding="utf-8"))
    for c in data["claims"]:
        if c["id"].endswith("::test_ceiling"):
            c["must_confront"] = None
    gr.write_text(json.dumps(data), encoding="utf-8")
    res = R.audit(prog / "reports" / "dosing" / "report.md", home=tmp_path)
    assert any(f["kind"] == "stale-litreview" for f in res["findings"])


def test_noncited_nonmustconfront_change_does_not_stale(tmp_path):
    prog = _program(tmp_path)
    _review_md(prog, "it-biodist", _GOOD_REVIEW)
    pin = R.audit(_dosing_report(prog), home=tmp_path)["litreview_cites"][0]["pin"]
    _dosing_report(prog, pin=pin)
    _set_strength(prog, "test_minor", "weak")                  # not cited, not must-confront
    res = R.audit(prog / "reports" / "dosing" / "report.md", home=tmp_path)
    assert res["status"] == "GROUNDED", res["findings"]
    assert not any(f["kind"] == "stale-litreview" for f in res["findings"])


# --------------------------------------------------------------------------- #
# --delta (cheap-update claim-set diff)
# --------------------------------------------------------------------------- #
def test_delta(tmp_path):
    prog = _program(tmp_path)
    review = _review_md(prog, "it-biodist", _GOOD_REVIEW)
    gr = prog / "analysis" / "grounding_report.json"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(gr.read_text(encoding="utf-8"), encoding="utf-8")
    # Mutate current: add a new must-confront claim, drift floor's strength, un-tag ceiling.
    data = json.loads(gr.read_text(encoding="utf-8"))
    data["claims"].append(_lit_claim("test_new", slug_mod="it_biodist",
                                     statement="a new pivotal finding", must_confront="new pivotal"))
    for c in data["claims"]:
        if c["id"].endswith("::test_floor"):
            c["strength"] = "weak"
        if c["id"].endswith("::test_ceiling"):
            c["must_confront"] = None
    gr.write_text(json.dumps(data), encoding="utf-8")

    d = LR.delta(review, baseline, home=tmp_path)
    short = lambda ids: {R._short_claim_id(i) for i in ids}  # noqa: E731
    assert short(d["added"]) == {"program::new"}
    assert d["removed"] == []
    assert short(d["drifted"]) == {"program::floor"}
    assert short(d["must_confront_added"]) == {"program::new"}
    assert short(d["must_confront_removed"]) == {"program::ceiling"}


def test_delta_no_change_is_empty(tmp_path):
    prog = _program(tmp_path)
    review = _review_md(prog, "it-biodist", _GOOD_REVIEW)
    gr = prog / "analysis" / "grounding_report.json"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(gr.read_text(encoding="utf-8"), encoding="utf-8")
    d = LR.delta(review, baseline, home=tmp_path)
    assert all(not v for v in d.values())
    assert "no change" in LR.render_delta(d)


# --------------------------------------------------------------------------- #
# kind=litreview store card (store-free determinism)
# --------------------------------------------------------------------------- #
def test_litreview_card_markdown_deterministic():
    from scientist.store import _meta as M
    card = {
        "litreview_id": "program::it-biodist", "scope": "program", "slug": "it-biodist",
        "title": "IT ASO biodistribution", "abstract": "How a lumbar ASO distributes the CNS.",
        "sections": [{"heading": "Exposure", "summary": "cord highest"}],
        "must_confront": ["program::test_litreview_it_biodist.py::test_floor"],
        "cited_claims": ["program::test_litreview_it_biodist.py::test_floor"],
        "audit_status": "GROUNDED", "path": "program/litreviews/it-biodist/review.md",
    }
    md1 = M.litreview_card_markdown(card)
    md2 = M.litreview_card_markdown(card)
    assert md1 == md2                                          # deterministic → stable document_id
    assert md1.startswith("# Literature review: IT ASO biodistribution")
    assert "## Abstract" in md1
    assert "## Must-confront" in md1
    assert "## Cites" in md1


def test_litreview_cite_renders_as_footnote(tmp_path):
    prog = _program(tmp_path)
    _review_md(prog, "it-biodist", _GOOD_REVIEW)
    report = _report_md(prog, """---
title: "Dosing"
---
## Argument
Per the survey [litreview:program::it-biodist], loss is tolerated [lit:test_floor] and the optimum
is set [lit:test_ceiling].

## Assumptions
- [litreview-waive:test_minor] irrelevant.
""")
    md = R.render_markdown(report, home=tmp_path)
    assert "[^litreview-1]" in md
    assert "Literature review: *IT ASO biodistribution*" in md
    assert "litreview-waive" not in md          # the waiver token is stripped from the render
