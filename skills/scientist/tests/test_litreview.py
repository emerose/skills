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


def test_omissions_audit_fails_closed_on_misnamed_module(tmp_path):
    """A report that cites a litreview whose claim module is misnamed (contributes zero claims)
    must FAIL CLOSED: the must-confront set is empty for the wrong reason, so the omissions audit
    would otherwise silently pass. Parallel to litreview.audit's own guard."""
    prog = _program(tmp_path, slug="it-biodist")  # claims live under test_litreview_it_biodist.py
    # a litreview folder whose slug doesn't match the claim-module name → expected module
    # test_litreview_it_biodist_typo.py contributes nothing.
    (prog / "litreviews" / "it-biodist-typo").mkdir(parents=True, exist_ok=True)
    _review_md(prog, "it-biodist-typo", _GOOD_REVIEW)
    report = _report_md(prog, """---
title: "Dosing"
---
## Argument
Per the survey [litreview:program::it-biodist-typo], loss is tolerated [lit:test_floor].
""")
    res = R.audit(report, home=tmp_path)
    assert res["status"] == "BROKEN"
    mm = [f for f in res["findings"] if f["kind"] == "missing-claims-module"]
    assert len(mm) == 1
    assert "silently pass" in mm[0]["detail"]
    assert "test_litreview_it_biodist_typo.py" in mm[0]["detail"]
    assert res["litreview_cites"][0]["verdict"] == "missing-claims-module"


def test_omissions_audit_correctly_named_module_not_flagged(tmp_path):
    """The guard does not false-positive on the happy path: a correctly-named module with claims
    raises no missing-claims-module finding."""
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
    assert not any(f["kind"] == "missing-claims-module" for f in res["findings"])


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


# --------------------------------------------------------------------------- #
# Fix 1 — LOUD failure on a misnamed / empty claims module (fail closed, not open)
# --------------------------------------------------------------------------- #
def test_misnamed_claims_module_is_a_loud_finding(tmp_path):
    """The grounding report's claims live under ``test_litreview_it_biodist.py`` but the review
    folder is ``it-biodist-typo`` → the expected module ``test_litreview_it_biodist_typo.py``
    contributes zero claims. That must be a loud BLOCKING finding (the obligation set is silently
    dead), distinct from the mild empty-must-confront advisory."""
    prog = _program(tmp_path, slug="it-biodist")
    (prog / "litreviews" / "it-biodist-typo").mkdir(parents=True, exist_ok=True)
    review = _review_md(prog, "it-biodist-typo", _GOOD_REVIEW)
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "BROKEN"
    missing = [f for f in res["findings"] if f["kind"] == "missing-claims-module"]
    assert len(missing) == 1
    assert "misnamed module" in missing[0]["detail"]
    assert "test_litreview_it_biodist_typo.py" in missing[0]["detail"]
    assert "not found on disk" in missing[0]["detail"]
    # NOT downgraded to the soft "under-assessed?" advisory.
    assert not any(a["kind"] == "empty-must-confront" for a in res["advisories"])
    txt = LR.render_audit(res)
    assert "missing-claims-module" in txt
    assert "under-assessed" not in txt          # the misleading mild line is suppressed


def test_present_but_empty_named_module_still_loud(tmp_path):
    """A correctly-named module file that is on disk but contributes NO claims (empty / not yet
    run) is still loud — the obligation set is dead either way — but the message notes it is
    present on disk (so 'stale grounding' rather than 'misnamed' is the likely cause)."""
    prog = _program(tmp_path, slug="it-biodist")
    # wipe the claims out of the grounding report; create the correctly-named (empty) module file.
    gr = prog / "analysis" / "grounding_report.json"
    gr.write_text(json.dumps({"claims": []}), encoding="utf-8")
    claims_dir = prog / "claims"
    claims_dir.mkdir(exist_ok=True)
    (claims_dir / "test_litreview_it_biodist.py").write_text("# no claims yet\n", encoding="utf-8")
    review = _review_md(prog, "it-biodist", _GOOD_REVIEW)
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "BROKEN"
    missing = [f for f in res["findings"] if f["kind"] == "missing-claims-module"]
    assert len(missing) == 1 and "present on disk" in missing[0]["detail"]


# --------------------------------------------------------------------------- #
# Fix 2 — `sci new-litreview` scaffolding (the highest-risk manual step removed)
# --------------------------------------------------------------------------- #
def test_scaffold_lays_out_correctly_named_module(tmp_path):
    res = LR.scaffold(tmp_path, "it-aso-biodistribution")
    review = tmp_path / "program/litreviews/it-aso-biodistribution/review.md"
    prompt = tmp_path / "program/litreviews/it-aso-biodistribution/prompt.md"
    module = tmp_path / "program/claims/test_litreview_it_aso_biodistribution.py"
    assert review.is_file() and prompt.is_file() and module.is_file()
    assert res["module"].endswith("claims/test_litreview_it_aso_biodistribution.py")
    assert len(res["created"]) == 3 and res["skipped"] == []
    # the module is correctly named by construction → its prefix carries claims once authored.
    assert R.litreview_module_path(review, tmp_path) == module


def test_scaffold_review_is_minimal_no_structure_template(tmp_path):
    LR.scaffold(tmp_path, "dosage-biology")
    body = (tmp_path / "program/litreviews/dosage-biology/review.md").read_text().lower()
    # MINIMAL: front matter + a pointer to references, mandatory gaps section — but NOT the
    # supporting/contradicting/equivocal/absent template (the structure is being redesigned).
    assert "references/litreview.md" in body
    assert "structure tbd" in body
    assert "gaps / open questions" in body
    for baked in ("supporting", "contradicting", "equivocal"):
        assert baked not in body
    assert "must_confront" in (tmp_path / "program/claims/test_litreview_dosage_biology.py").read_text()


def test_scaffold_is_idempotent(tmp_path):
    LR.scaffold(tmp_path, "it-biodist")
    again = LR.scaffold(tmp_path, "it-biodist")
    assert again["created"] == [] and len(again["skipped"]) == 3


# --------------------------------------------------------------------------- #
# Fix 3 — litreview-specific advisory tuning (sci report unchanged)
# --------------------------------------------------------------------------- #
_GAPS_HYPOTHETICAL_REVIEW = """---
title: "T"
---
## Body
A stray figure of 75% appears here [lit:test_minor].

## Gaps / open questions
Regimes below 75% knockdown are unmeasured [lit:test_minor]; e.g. 25/50 are untested.
"""


def test_unsupported_quantity_skips_gaps_section(tmp_path):
    prog = _program(tmp_path)
    review = _review_md(prog, "it-biodist", _GAPS_HYPOTHETICAL_REVIEW)
    res = LR.audit(review, home=tmp_path)
    uq = [a for a in res["advisories"] if a["kind"] == "unsupported-quantity"]
    # the 75 in the BODY paragraph flags; the 75 / 25 / 50 in the gaps section do not.
    assert [a["value"] for a in uq] == [75.0]
    gaps_line = _GAPS_HYPOTHETICAL_REVIEW[: _GAPS_HYPOTHETICAL_REVIEW.index("## Gaps")].count("\n") + 1
    assert all(a["line"] < gaps_line for a in uq)


_WLB_REVIEW = """---
title: "T"
---
## A
Lumbar dosing gives a 50% knockdown [lit:test_floor].

## B
A reciprocal 200% effect is seen [lit:test_ceiling].

## Gaps / open questions
Unmeasured regimes remain.
"""


def test_weak_load_bearing_collapsed_to_one_summary(tmp_path):
    """In a conclusion-free survey single-group/moderate claims are the norm — the per-bound
    weak-load-bearing finding is noise. Collapse to one summary advisory, deduped across cites."""
    prog = _program(tmp_path)
    review = _review_md(prog, "it-biodist", _WLB_REVIEW)
    res = LR.audit(review, home=tmp_path)
    assert not any(a["kind"] == "weak-load-bearing" for a in res["advisories"])
    summary = [a for a in res["advisories"] if a["kind"] == "weak-load-bearing-survey"]
    assert len(summary) == 1
    assert summary[0]["count"] == 2
    assert set(summary[0]["cites"]) == {"program::floor", "program::ceiling"}
    assert "weak-load-bearing-survey" in LR.render_audit(res)


def test_report_advisories_unchanged_for_sci_report(tmp_path):
    """The tuning is litreview-only: a plain report keeps the per-bound weak-load-bearing finding
    and flags numbers in any section (no gaps exemption)."""
    prog = _program(tmp_path)
    report = _report_md(prog, _WLB_REVIEW)        # same body, audited AS a report
    res = R.audit(report, home=tmp_path)
    assert any(a["kind"] == "weak-load-bearing" for a in res["advisories"])
    assert not any(a["kind"] == "weak-load-bearing-survey" for a in res["advisories"])


# --------------------------------------------------------------------------- #
# Fix 4 — stale-grounding guard (cheap mtime check; warn, don't block)
# --------------------------------------------------------------------------- #
def test_stale_grounding_warns_when_module_is_newer(tmp_path):
    import os

    prog = _program(tmp_path, slug="it-biodist")
    gr = prog / "analysis" / "grounding_report.json"
    claims_dir = prog / "claims"
    claims_dir.mkdir(exist_ok=True)
    mod = claims_dir / "test_litreview_it_biodist.py"
    mod.write_text("# claims source\n", encoding="utf-8")
    base = gr.stat().st_mtime
    os.utime(gr, (base, base))
    os.utime(mod, (base + 100, base + 100))       # module edited after the grounding was emitted

    review = _review_md(prog, "it-biodist", _GOOD_REVIEW)
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "GROUNDED"            # non-blocking
    assert res.get("warnings")
    assert any("re-run pytest --grounding-out" in w["detail"] for w in res["warnings"])
    assert "stale-grounding" in LR.render_audit(res)
    # same guard on the sci report path.
    rep = R.audit(_dosing_report(prog), home=tmp_path)
    assert any("re-run pytest --grounding-out" in w["detail"] for w in rep["warnings"])


def test_no_stale_warning_when_grounding_is_fresh(tmp_path):
    import os

    prog = _program(tmp_path, slug="it-biodist")
    gr = prog / "analysis" / "grounding_report.json"
    claims_dir = prog / "claims"
    claims_dir.mkdir(exist_ok=True)
    mod = claims_dir / "test_litreview_it_biodist.py"
    mod.write_text("# claims source\n", encoding="utf-8")
    base = gr.stat().st_mtime
    os.utime(mod, (base - 100, base - 100))       # grounding newer than the module → fresh
    os.utime(gr, (base, base))
    review = _review_md(prog, "it-biodist", _GOOD_REVIEW)
    assert not LR.audit(review, home=tmp_path).get("warnings")


# --------------------------------------------------------------------------- #
# Fix 5 — `sci report --write-pins` (mechanize the manual paste)
# --------------------------------------------------------------------------- #
def test_write_litreview_pins_records_surfaced_pin(tmp_path):
    prog = _program(tmp_path)
    _review_md(prog, "it-biodist", _GOOD_REVIEW)
    report = _dosing_report(prog)                 # addresses both must-confront, pin not recorded
    res = R.audit(report, home=tmp_path)
    lrc = res["litreview_cites"][0]
    assert lrc.get("pin_unrecorded") is True      # surfaces only because unaddressed is empty
    assert not lrc.get("unaddressed")

    merged = R.write_litreview_pins(report, {lrc["id"]: lrc["pin"]})
    assert merged[lrc["id"]] == lrc["pin"]
    # the recorded 12-char prefix matches by startswith → clean on re-audit, nudge gone.
    assert len(lrc["pin"]) == 12
    pins = R.litreview_pins(report.read_text())
    assert pins[lrc["id"]] == lrc["pin"]
    res2 = R.audit(report, home=tmp_path)
    lrc2 = res2["litreview_cites"][0]
    assert res2["status"] == "GROUNDED"
    assert lrc2["verdict"] == "backed"
    assert not lrc2.get("pin_unrecorded")


def test_write_litreview_pins_preserves_existing_front_matter(tmp_path):
    prog = _program(tmp_path)
    _review_md(prog, "it-biodist", _GOOD_REVIEW)
    report = _dosing_report(prog)
    pin = R.audit(report, home=tmp_path)["litreview_cites"][0]["pin"]
    R.write_litreview_pins(report, {"program::it-biodist": pin})
    text = report.read_text()
    assert 'title: "Dosing"' in text              # the existing key is left intact
    assert "litreview_pins:" in text
    # second write merges a second litreview without dropping the first.
    merged = R.write_litreview_pins(report, {"program::other": "abcdef012345"})
    assert merged["program::it-biodist"] == pin
    assert merged["program::other"] == "abcdef012345"
