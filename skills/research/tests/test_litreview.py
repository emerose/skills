"""The litreview phase — ``research.litreview`` (``res litreview``) and the protocol-keyed
``stale-litreview`` pin in the ``[litreview:]`` citation layer.

A *litreview* (``kind=litreview``) is a neutral, thesis-independent survey of the third-party
literature on one sub-question. Phase 1 of the litreview redesign replaces the hand-tagged
``@must_confront`` obligation set with committed, auditable **PROSPERO/PRISMA** artifacts:

* ``protocol.md`` — pre-registered question/scope/queries/inclusion+exclusion;
* ``screening.jsonl`` — every candidate tracked to included|excluded(+reason), the PRISMA funnel;
* a **coverage cross-check** — every ``[lit:]``-cited paper must be an ``included`` screening row;

and the consuming report pins to the survey's **search protocol** (queries + as_of + sources)
rather than to a must-confront membership set.

Pure: synthetic ``program/`` trees in tmp dirs — a hand-written ``grounding_report.json`` with
``[lit:]`` claims, a ``review.md``, a ``protocol.md``, a ``screening.jsonl``, and a ``report.md``.
No keys, no libkit store, no paper library, no model.
"""
from __future__ import annotations

import json
from pathlib import Path

import research as grounding
from research import litreview as LR
from reportkit import report as R
from research import literature_cites as LIT


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def _lit_claim(node: str, *, slug_mod: str, statement: str,
               outcome="passed", strength="moderate", support=True, groups=2) -> dict:
    """A literature claim record shaped like the plugin emits (legacy @reviewed path). Its single
    source carries citekey ``<node>2020`` — the unit the coverage cross-check matches on."""
    return {
        "id": f"program/claims/test_litreview_{slug_mod}.py::{node}",
        "statement": statement,
        "outcome": outcome, "kind": "literature", "strength": strength, "caveats": None,
        "reviewed": {"support": support, "primary": True, "independent_groups": groups},
        "evidence": {"lit_sources": [
            {"citekey": f"{node}2020", "quote": "q", "primary": True, "group": node}]},
        "inputs": [], "reconcile": [],
    }


def _program(tmp_path: Path, slug: str = "it-biodist") -> Path:
    """A program/ tree: a grounding report with three [lit:] claims (citekeys floor2020 /
    ceiling2020 / minor2020) and a litreview folder. ``slug`` is hyphenated; the module underscores
    it. The protocol.md + screening.jsonl are laid down separately (so a test can omit/break one)."""
    mod = slug.replace("-", "_")
    prog = tmp_path / "program"
    (prog / "analysis").mkdir(parents=True, exist_ok=True)
    (prog / "litreviews" / slug).mkdir(parents=True, exist_ok=True)
    claims = [
        _lit_claim("test_floor", slug_mod=mod, statement="~50% loss is tolerated prenatally"),
        _lit_claim("test_ceiling", slug_mod=mod, statement="reciprocal dosing makes WT the optimum"),
        _lit_claim("test_minor", slug_mod=mod, statement="a minor corroborating detail"),
    ]
    (prog / "analysis" / "grounding_report.json").write_text(
        json.dumps({"claims": claims}, indent=2), encoding="utf-8")
    return prog


def _review_md(prog: Path, slug: str, body: str) -> Path:
    md = prog / "litreviews" / slug / "review.md"
    md.write_text(body, encoding="utf-8")
    return md


def _write_protocol(prog: Path, slug: str = "it-biodist", *, as_of="2026-06-19",
                    sources=("openalex", "pubmed"),
                    queries="ASO biodistribution CNS lumbar",
                    inclusion="Primary biodistribution data in mammals.",
                    drop_field: str | None = None, drop_heading: str | None = None) -> Path:
    """Write protocol.md beside review.md. ``drop_field`` omits a front-matter key;
    ``drop_heading`` empties one of the four required sections."""
    fm_lines = [f"slug: {slug}", f'as_of: "{as_of}"', f"sources: {json.dumps(list(sources))}"]
    fm_lines = [ln for ln in fm_lines if not (drop_field and ln.startswith(f"{drop_field}:"))]
    secs = {
        "Question & scope": "How a lumbar ASO distributes across the CNS.",
        "Search queries": queries,
        "Inclusion criteria": inclusion,
        "Exclusion criteria": "Reviews; modeling-only; no primary data.",
    }
    body = "---\n" + "\n".join(fm_lines) + "\n---\n\n"
    for heading, text in secs.items():
        body += f"## {heading}\n" + ("" if heading == drop_heading else text) + "\n\n"
    p = prog / "litreviews" / slug / "protocol.md"
    p.write_text(body, encoding="utf-8")
    return p


_DEFAULT_SCREEN = [
    {"id": "doi:10.1/floor", "title": "Floor", "year": 2015, "source": ["openalex"],
     "query": "q", "decision": "included", "citekey": "test_floor2020"},
    {"id": "doi:10.1/ceiling", "title": "Ceiling", "year": 2016, "source": ["pubmed"],
     "query": "q", "decision": "included", "citekey": "test_ceiling2020"},
    {"id": "doi:10.1/minor", "title": "Minor", "year": 2017, "source": ["openalex"],
     "query": "q", "decision": "included", "citekey": "test_minor2020"},
    {"id": "arxiv:2401.0001", "title": "Off-topic", "year": 2024, "source": ["arxiv"],
     "query": "q", "decision": "excluded", "reason": "review only — no primary biodistribution data"},
]


def _write_screening(prog: Path, slug: str = "it-biodist", rows=None) -> Path:
    rows = _DEFAULT_SCREEN if rows is None else rows
    p = prog / "litreviews" / slug / "screening.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


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


def _full_litreview(tmp_path: Path, slug: str = "it-biodist", body: str = _GOOD_REVIEW) -> Path:
    """A complete, GROUNDED-by-default litreview: grounding report + review + protocol + screening."""
    prog = _program(tmp_path, slug)
    review = _review_md(prog, slug, body)
    _write_protocol(prog, slug)
    _write_screening(prog, slug)
    return review


def _report_md(prog: Path, body: str, slug: str = "dosing") -> Path:
    d = prog / "reports" / slug
    d.mkdir(parents=True, exist_ok=True)
    md = d / "report.md"
    md.write_text(body, encoding="utf-8")
    return md


# --------------------------------------------------------------------------- #
# the @must_confront marker is GONE
# --------------------------------------------------------------------------- #
def test_must_confront_marker_is_removed():
    assert "must_confront" not in grounding.__all__
    assert not hasattr(grounding, "must_confront")
    # Markers are now registered by the grounding package's plugin (scientist's companion
    # plugin, PLUGIN, no longer owns the marker set); must_confront is not among them.
    import grounding.plugin as _gplugin
    assert "must_confront" not in _gplugin._MARKERS


# --------------------------------------------------------------------------- #
# litreview.audit — the artifact's own contract (happy path)
# --------------------------------------------------------------------------- #
def test_litreview_audit_grounded(tmp_path):
    review = _full_litreview(tmp_path)
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "GROUNDED", res["findings"]
    assert res["kind"] == "litreview"
    assert res["protocol_present"] is True
    assert res["contested_status_addressed"] is True
    assert res["funnel"] == {"identified": 4, "included": 3, "excluded": 1, "pending": 0,
                             "excluded_by_reason": {"review only — no primary biodistribution data": 1}}


def test_litreview_audit_missing_gaps_section_is_broken(tmp_path):
    review = _full_litreview(tmp_path, body=_GOOD_REVIEW.replace(
        "## Gaps / open questions\nHuman region-to-region ratios are unmeasured.", ""))
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "BROKEN"
    assert any(f["kind"] == "missing-gaps-section" for f in res["findings"])


def test_litreview_audit_rejects_kicho_data(tmp_path):
    review = _full_litreview(tmp_path, body=_GOOD_REVIEW.replace("[lit:test_minor]", "[claim:test_minor]"))
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "BROKEN"
    assert any(f["kind"] == "kicho-data-in-litreview" for f in res["findings"])


# --------------------------------------------------------------------------- #
# protocol.md validation
# --------------------------------------------------------------------------- #
def test_missing_protocol_is_blocking(tmp_path):
    prog = _program(tmp_path)
    review = _review_md(prog, "it-biodist", _GOOD_REVIEW)
    _write_screening(prog)                                  # screening present, protocol absent
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "BROKEN"
    assert any(f["kind"] == "missing-protocol" for f in res["findings"])
    assert res["protocol_present"] is False


def test_missing_protocol_front_matter_field_is_blocking(tmp_path):
    prog = _program(tmp_path)
    review = _review_md(prog, "it-biodist", _GOOD_REVIEW)
    _write_screening(prog)
    _write_protocol(prog, drop_field="sources")
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "BROKEN"
    bad = [f for f in res["findings"] if f["kind"] == "missing-protocol-field"]
    assert len(bad) == 1 and bad[0]["field"] == "sources"


def test_empty_sources_list_is_blocking(tmp_path):
    prog = _program(tmp_path)
    review = _review_md(prog, "it-biodist", _GOOD_REVIEW)
    _write_screening(prog)
    _write_protocol(prog, sources=())                       # sources: []
    res = LR.audit(review, home=tmp_path)
    assert any(f["kind"] == "missing-protocol-field" and f["field"] == "sources"
               for f in res["findings"])


def test_missing_protocol_heading_is_blocking(tmp_path):
    prog = _program(tmp_path)
    review = _review_md(prog, "it-biodist", _GOOD_REVIEW)
    _write_screening(prog)
    _write_protocol(prog, drop_heading="Inclusion criteria")
    res = LR.audit(review, home=tmp_path)
    bad = [f for f in res["findings"] if f["kind"] == "missing-protocol-field"]
    assert len(bad) == 1 and bad[0]["field"] == "Inclusion criteria"


# --------------------------------------------------------------------------- #
# screening.jsonl parse + the PRISMA funnel
# --------------------------------------------------------------------------- #
def test_missing_screening_is_blocking(tmp_path):
    prog = _program(tmp_path)
    review = _review_md(prog, "it-biodist", _GOOD_REVIEW)
    _write_protocol(prog)                                   # protocol present, screening absent
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "BROKEN"
    assert any(f["kind"] == "missing-screening" for f in res["findings"])


def test_malformed_screening_row_is_blocking(tmp_path):
    prog = _program(tmp_path)
    _write_protocol(prog)
    p = prog / "litreviews" / "it-biodist" / "screening.jsonl"
    # a valid included row, then a junk line, then an id-less row.
    p.write_text(json.dumps(_DEFAULT_SCREEN[0]) + "\n"
                 + "{not json}\n"
                 + json.dumps({"title": "no id", "decision": "included"}) + "\n",
                 encoding="utf-8")
    rows, findings = LR.parse_screening(p)
    kinds = [f["kind"] for f in findings]
    assert kinds.count("malformed-screening-row") == 2     # junk line + id-less row
    assert len(rows) == 1                                   # only the valid row survives


def test_excluded_without_reason_is_blocking(tmp_path):
    rows = [{"id": "doi:10.1/x", "decision": "excluded"}]   # no reason
    _, findings = LR.parse_screening(_screening_file(tmp_path, rows))
    assert any(f["kind"] == "excluded-without-reason" for f in findings)


def test_included_without_citekey_is_malformed(tmp_path):
    rows = [{"id": "doi:10.1/x", "decision": "included"}]   # no citekey
    _, findings = LR.parse_screening(_screening_file(tmp_path, rows))
    assert any(f["kind"] == "malformed-screening-row" for f in findings)


def test_pending_row_is_not_a_finding(tmp_path):
    rows = [{"id": "doi:10.1/x", "title": "pending"}]       # decision unset
    parsed, findings = LR.parse_screening(_screening_file(tmp_path, rows))
    assert findings == []
    funnel = LR.prisma_funnel(parsed)
    assert funnel == {"identified": 1, "included": 0, "excluded": 0, "pending": 1,
                      "excluded_by_reason": {}}


def test_prisma_funnel_groups_exclusions_by_reason():
    rows = [
        {"id": "a", "decision": "included", "citekey": "a2020"},
        {"id": "b", "decision": "excluded", "reason": "review only"},
        {"id": "c", "decision": "excluded", "reason": "review only"},
        {"id": "d", "decision": "excluded", "reason": "wrong species"},
    ]
    funnel = LR.prisma_funnel(rows)
    assert funnel["identified"] == 4 and funnel["included"] == 1 and funnel["excluded"] == 3
    assert funnel["excluded_by_reason"] == {"review only": 2, "wrong species": 1}


def _screening_file(tmp_path: Path, rows) -> Path:
    p = tmp_path / "screening.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# the coverage cross-check (the integrity core)
# --------------------------------------------------------------------------- #
def test_cited_paper_unscreened_is_blocking(tmp_path):
    """A [lit:]-cited paper (minor2020) that is not an `included` screening row blocks."""
    prog = _program(tmp_path)
    review = _review_md(prog, "it-biodist", _GOOD_REVIEW)
    _write_protocol(prog)
    # screen in floor + ceiling only; minor is cited but never screened in.
    _write_screening(prog, rows=_DEFAULT_SCREEN[:2])
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "BROKEN"
    unscreened = [f for f in res["findings"] if f["kind"] == "cited-paper-unscreened"]
    assert len(unscreened) == 1 and unscreened[0]["cite"] == "test_minor2020"


def test_included_but_uncited_is_advisory(tmp_path):
    prog = _program(tmp_path)
    review = _review_md(prog, "it-biodist", _GOOD_REVIEW)
    _write_protocol(prog)
    rows = _DEFAULT_SCREEN + [
        {"id": "doi:10.1/extra", "decision": "included", "citekey": "extra2020"}]
    _write_screening(prog, rows=rows)
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "GROUNDED", res["findings"]     # advisory does NOT block
    adv = [a for a in res["advisories"] if a["kind"] == "included-but-uncited"]
    assert len(adv) == 1 and adv[0]["cites"] == ["extra2020"]


# --------------------------------------------------------------------------- #
# --ingest-discover — map `bib discover --json` into screening.jsonl
# --------------------------------------------------------------------------- #
_DISCOVER = {
    "query": "ASO CNS biodistribution",
    "sources": {"openalex": 2, "semantic-scholar": 1},
    "results": [
        {"title": "Silva-Santos 2015", "year": 2015, "authors": "Silva-Santos", "venue": "J",
         "doi": "10.1234/abc", "found_in": ["openalex", "pubmed"],
         "citation_percentile": 99.2, "fwci": 3.4, "cited_by_count": 120,
         "in_library": True, "library_citekey": "silvasantos2015"},
        {"title": "Preprint", "year": 2024, "authors": "Y", "venue": "arXiv",
         "arxiv_id": "2401.00001", "found_in": ["semantic-scholar"], "in_library": False},
        {"title": "No identifier", "year": 2020, "authors": "Z", "venue": "K",
         "found_in": ["crossref"]},
    ],
}


def test_ingest_discover_maps_and_dedupes(tmp_path):
    review = _full_litreview(tmp_path)
    # start from an empty screening so the mapping is unambiguous.
    (tmp_path / "program/litreviews/it-biodist/screening.jsonl").write_text("", encoding="utf-8")
    disc = tmp_path / "discover.json"
    disc.write_text(json.dumps(_DISCOVER), encoding="utf-8")

    res = LR.ingest_discover(review, disc, home=tmp_path)
    assert res["appended"] == 2 and res["skipped_no_id"] == 1 and res["skipped_duplicate"] == 0

    rows, findings = LR.parse_screening(tmp_path / "program/litreviews/it-biodist/screening.jsonl")
    assert findings == []                                   # pending rows are not findings
    by_id = {r["id"]: r for r in rows}
    a = by_id["doi:10.1234/abc"]
    assert a["source"] == ["openalex", "pubmed"]            # found_in → source
    assert a["query"] == "ASO CNS biodistribution"          # from the payload
    assert a["citation_percentile"] == 99.2 and a["fwci"] == 3.4 and a["cited_by_count"] == 120
    assert a["citekey"] == "silvasantos2015"                # library_citekey (in_library)
    assert "decision" not in a                              # unset — the author screens by hand
    b = by_id["arxiv:2401.00001"]
    assert "citekey" not in b                               # not in library

    # re-ingesting the same payload appends nothing (de-duped by id).
    res2 = LR.ingest_discover(review, disc, home=tmp_path)
    assert res2["appended"] == 0 and res2["skipped_duplicate"] == 2


def test_ingest_discover_query_override(tmp_path):
    review = _full_litreview(tmp_path)
    (tmp_path / "program/litreviews/it-biodist/screening.jsonl").write_text("", encoding="utf-8")
    disc = tmp_path / "discover.json"
    disc.write_text(json.dumps(_DISCOVER), encoding="utf-8")
    LR.ingest_discover(review, disc, home=tmp_path, query="my explicit query")
    rows, _ = LR.parse_screening(tmp_path / "program/litreviews/it-biodist/screening.jsonl")
    assert all(r.get("query") == "my explicit query" for r in rows)


# --------------------------------------------------------------------------- #
# protocol-keyed staleness pin (in provenance.report)
# --------------------------------------------------------------------------- #
def _dosing_report(prog: Path, pin: str | None = None, slug: str = "dosing") -> Path:
    fm = '---\ntitle: "Dosing"\n'
    if pin is not None:
        fm += f'litreview_pins:\n  program::it-biodist: "{pin}"\n'
    fm += "---\n"
    body = fm + ("## Argument\nPer the survey [litreview:program::it-biodist], ~50% loss is "
                 "tolerated [lit:test_floor].\n")
    return _report_md(prog, body, slug=slug)


def test_litreview_pin_unrecorded_is_advisory(tmp_path):
    _full_litreview(tmp_path)
    prog = tmp_path / "program"
    res = R.audit(_dosing_report(prog), home=tmp_path)
    assert res["status"] == "GROUNDED", res["findings"]     # unpinned does NOT block
    lrc = res["litreview_cites"][0]
    assert lrc["verdict"] == "backed"
    assert lrc.get("pin_unrecorded") is True
    assert len(lrc["pin"]) == 12


def test_litreview_pin_recorded_is_clean(tmp_path):
    _full_litreview(tmp_path)
    prog = tmp_path / "program"
    pin = R.audit(_dosing_report(prog), home=tmp_path)["litreview_cites"][0]["pin"]
    res = R.audit(_dosing_report(prog, pin=pin), home=tmp_path)
    assert res["status"] == "GROUNDED", res["findings"]
    lrc = res["litreview_cites"][0]
    assert lrc["verdict"] == "backed"
    assert not lrc.get("pin_unrecorded")


def test_stale_litreview_on_query_change(tmp_path):
    _full_litreview(tmp_path)
    prog = tmp_path / "program"
    pin = R.audit(_dosing_report(prog), home=tmp_path)["litreview_cites"][0]["pin"]
    _dosing_report(prog, pin=pin)
    _write_protocol(prog, queries="ASO biodistribution CNS lumbar AND deep-brain ratio")  # new query
    res = R.audit(prog / "reports" / "dosing" / "report.md", home=tmp_path)
    assert res["status"] == "BROKEN"
    assert any(f["kind"] == "stale-litreview" for f in res["findings"])
    assert res["litreview_cites"][0]["verdict"] == "stale-litreview"


def test_stale_litreview_on_asof_change(tmp_path):
    _full_litreview(tmp_path)
    prog = tmp_path / "program"
    pin = R.audit(_dosing_report(prog), home=tmp_path)["litreview_cites"][0]["pin"]
    _dosing_report(prog, pin=pin)
    _write_protocol(prog, as_of="2026-12-31")               # refreshed snapshot
    res = R.audit(prog / "reports" / "dosing" / "report.md", home=tmp_path)
    assert any(f["kind"] == "stale-litreview" for f in res["findings"])


def test_stale_litreview_on_sources_change(tmp_path):
    _full_litreview(tmp_path)
    prog = tmp_path / "program"
    pin = R.audit(_dosing_report(prog), home=tmp_path)["litreview_cites"][0]["pin"]
    _dosing_report(prog, pin=pin)
    _write_protocol(prog, sources=("openalex", "pubmed", "crossref"))  # added a source
    res = R.audit(prog / "reports" / "dosing" / "report.md", home=tmp_path)
    assert any(f["kind"] == "stale-litreview" for f in res["findings"])


def test_non_protocol_change_does_not_stale(tmp_path):
    """Editing the review prose or a non-pinned protocol section (Inclusion criteria) does not
    move the protocol pin — the survey can grow without a BROKEN cascade on consuming reports."""
    review = _full_litreview(tmp_path)
    prog = tmp_path / "program"
    pin = R.audit(_dosing_report(prog), home=tmp_path)["litreview_cites"][0]["pin"]
    _dosing_report(prog, pin=pin)
    review.write_text(review.read_text() + "\n\nA new non-pivotal paragraph [lit:test_minor].\n",
                      encoding="utf-8")
    _write_protocol(prog, inclusion="Primary biodistribution data; any mammalian model.")
    res = R.audit(prog / "reports" / "dosing" / "report.md", home=tmp_path)
    assert res["status"] == "GROUNDED", res["findings"]
    assert not any(f["kind"] == "stale-litreview" for f in res["findings"])


def test_missing_litreview_still_blocks(tmp_path):
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


# --------------------------------------------------------------------------- #
# --write-pins (mechanize the manual paste) — now protocol-keyed
# --------------------------------------------------------------------------- #
def test_write_litreview_pins_records_surfaced_pin(tmp_path):
    _full_litreview(tmp_path)
    prog = tmp_path / "program"
    report = _dosing_report(prog)
    res = R.audit(report, home=tmp_path)
    lrc = res["litreview_cites"][0]
    assert lrc.get("pin_unrecorded") is True
    LIT.write_litreview_pins(report, {lrc["id"]: lrc["pin"]})
    pins = LIT.litreview_pins(report.read_text())
    assert pins[lrc["id"]] == lrc["pin"]
    res2 = R.audit(report, home=tmp_path)
    lrc2 = res2["litreview_cites"][0]
    assert res2["status"] == "GROUNDED"
    assert lrc2["verdict"] == "backed" and not lrc2.get("pin_unrecorded")


# --------------------------------------------------------------------------- #
# --delta (cheap-update claim-set diff) — must_confront fields gone
# --------------------------------------------------------------------------- #
def test_delta(tmp_path):
    review = _full_litreview(tmp_path)
    prog = tmp_path / "program"
    gr = prog / "analysis" / "grounding_report.json"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(gr.read_text(encoding="utf-8"), encoding="utf-8")
    # Mutate current: add a new claim, drift floor's strength.
    data = json.loads(gr.read_text(encoding="utf-8"))
    data["claims"].append(_lit_claim("test_new", slug_mod="it_biodist",
                                     statement="a new finding"))
    for c in data["claims"]:
        if c["id"].endswith("::test_floor"):
            c["strength"] = "weak"
    gr.write_text(json.dumps(data), encoding="utf-8")

    d = LR.delta(review, baseline, home=tmp_path)
    short = lambda ids: {R._short_claim_id(i) for i in ids}  # noqa: E731
    assert short(d["added"]) == {"program::new"}
    assert d["removed"] == []
    assert short(d["drifted"]) == {"program::floor"}
    assert "must_confront_added" not in d and "must_confront_removed" not in d


def test_delta_no_change_is_empty(tmp_path):
    review = _full_litreview(tmp_path)
    prog = tmp_path / "program"
    gr = prog / "analysis" / "grounding_report.json"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(gr.read_text(encoding="utf-8"), encoding="utf-8")
    d = LR.delta(review, baseline, home=tmp_path)
    assert all(not v for v in d.values())
    assert "no change" in LR.render_delta(d)


def test_litreview_cite_renders_as_footnote(tmp_path):
    _full_litreview(tmp_path)
    prog = tmp_path / "program"
    report = _report_md(prog, """---
title: "Dosing"
---
## Argument
Per the survey [litreview:program::it-biodist], loss is tolerated [lit:test_floor].
""")
    md = R.render_markdown(report, home=tmp_path)
    assert "[^litreview-1]" in md
    assert "Literature review: *IT ASO biodistribution*" in md


# --------------------------------------------------------------------------- #
# `sci new-litreview` scaffolding
# --------------------------------------------------------------------------- #
def test_scaffold_lays_out_all_artifacts(tmp_path):
    res = LR.scaffold(tmp_path, "it-aso-biodistribution")
    base = tmp_path / "program/litreviews/it-aso-biodistribution"
    review = base / "review.md"
    protocol = base / "protocol.md"
    screening = base / "screening.jsonl"
    prompt = base / "prompt.md"
    module = tmp_path / "program/claims/test_litreview_it_aso_biodistribution.py"
    assert review.is_file() and protocol.is_file() and screening.is_file()
    assert prompt.is_file() and module.is_file()
    assert res["module"].endswith("claims/test_litreview_it_aso_biodistribution.py")
    assert len(res["created"]) == 5 and res["skipped"] == []
    assert LIT.litreview_module_path(review, tmp_path) == module
    # the protocol stub carries the four required headings + the front-matter keys.
    pbody = protocol.read_text()
    for heading in ("Question & scope", "Search queries", "Inclusion criteria", "Exclusion criteria"):
        assert f"## {heading}" in pbody
    assert "slug: it-aso-biodistribution" in pbody and "as_of:" in pbody and "sources:" in pbody
    assert screening.read_text() == ""                       # empty PRISMA log to seed


def test_scaffold_drops_must_confront_from_stubs(tmp_path):
    LR.scaffold(tmp_path, "dosage-biology")
    module = (tmp_path / "program/claims/test_litreview_dosage_biology.py").read_text()
    prompt = (tmp_path / "program/litreviews/dosage-biology/prompt.md").read_text()
    review = (tmp_path / "program/litreviews/dosage-biology/review.md").read_text().lower()
    assert "must_confront" not in module                     # no obligation-tag import/decorator
    assert "must_confront" not in prompt and "must-confront" not in prompt
    assert "controversies" not in prompt.lower()             # the must-confront guidance is gone
    assert "ingest-discover" in prompt                       # the PRISMA workflow is documented
    # review stub stays minimal: pointer to references, mandatory gaps, no baked template.
    assert "references/litreview.md" in review
    assert "gaps / open questions" in review
    for baked in ("supporting", "contradicting", "equivocal"):
        assert baked not in review


def test_scaffold_is_idempotent(tmp_path):
    LR.scaffold(tmp_path, "it-biodist")
    again = LR.scaffold(tmp_path, "it-biodist")
    assert again["created"] == [] and len(again["skipped"]) == 5


# --------------------------------------------------------------------------- #
# contested-status read (advisory, content-based) — unchanged behavior
# --------------------------------------------------------------------------- #
def test_contested_status_satisfied_by_no_controversy_finding(tmp_path):
    body = _GOOD_REVIEW.replace(
        "## Controversies / unresolved\nLab A and Lab B disagree on the deep-brain ratio [lit:test_floor].",
        "## Convergence & dependence\nThere is no genuine two-camp split here; the evidence "
        "converges, and the real fault line is single-lab dependence [lit:test_floor].")
    review = _full_litreview(tmp_path, body=body)
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "GROUNDED", res["findings"]
    assert res["contested_status_addressed"] is True


def test_contested_status_not_satisfied_by_heading_alone(tmp_path):
    body = _GOOD_REVIEW.replace(
        "## Controversies / unresolved\nLab A and Lab B disagree on the deep-brain ratio [lit:test_floor].",
        "## Controversies\nThe cord sees the most drug at the lumbar level [lit:test_floor].")
    review = _full_litreview(tmp_path, body=body)
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "GROUNDED", res["findings"]
    assert res["contested_status_addressed"] is False


# --------------------------------------------------------------------------- #
# litreview-specific advisory tuning (sci report unchanged)
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
    # screen in only minor2020 (the only cited paper here) so coverage stays clean.
    review = _full_litreview(tmp_path, body=_GAPS_HYPOTHETICAL_REVIEW)
    _write_screening(tmp_path / "program", rows=[_DEFAULT_SCREEN[2]])
    res = LR.audit(review, home=tmp_path)
    uq = [a for a in res["advisories"] if a["kind"] == "unsupported-quantity"]
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
    review = _full_litreview(tmp_path, body=_WLB_REVIEW)
    _write_screening(tmp_path / "program", rows=_DEFAULT_SCREEN[:2])  # floor + ceiling cited
    res = LR.audit(review, home=tmp_path)
    assert not any(a["kind"] == "weak-load-bearing" for a in res["advisories"])
    summary = [a for a in res["advisories"] if a["kind"] == "weak-load-bearing-survey"]
    assert len(summary) == 1
    assert summary[0]["count"] == 2
    assert set(summary[0]["cites"]) == {"program::floor", "program::ceiling"}
    assert "weak-load-bearing-survey" in LR.render_audit(res)


def test_report_advisories_unchanged_for_sci_report(tmp_path):
    """The tuning is litreview-only: a plain report keeps the per-bound weak-load-bearing finding."""
    prog = _program(tmp_path)
    report = _report_md(prog, _WLB_REVIEW)        # same body, audited AS a report
    res = R.audit(report, home=tmp_path)
    assert any(a["kind"] == "weak-load-bearing" for a in res["advisories"])
    assert not any(a["kind"] == "weak-load-bearing-survey" for a in res["advisories"])


# --------------------------------------------------------------------------- #
# universal-negative gaps lint (advisory) — gaps-negative-claim-unreconciled
# --------------------------------------------------------------------------- #
# The motivating defect: a gaps bullet asserting a universal negative ("No faithful UBE3A-attributable
# mouse …") that contradicted a cited paper-claim AND the review's own synthesis, yet passed the audit
# (which checks only that a gaps section exists) and the completeness critic (which checked
# conflict-survival, not internal consistency). The lint flags every such line so the author must
# reconcile it against the screening log + claim store. Advisory: it never blocks.
_NEG_GAP_REVIEW = """---
title: "T"
---
## Body
Lumbar dosing gives a 50% knockdown [lit:test_floor].

## Gaps / open questions
- No faithful UBE3A-attributable mouse reproduces the human maternal-duplication phenotype.
- Human region-to-region ratios remain unmeasured.
"""


def test_gaps_negative_claim_flagged_and_nonblocking(tmp_path):
    review = _full_litreview(tmp_path, body=_NEG_GAP_REVIEW)
    _write_screening(tmp_path / "program", rows=_DEFAULT_SCREEN[:1])  # only floor is cited
    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "GROUNDED", res["findings"]              # advisory does NOT block
    neg = [a for a in res["advisories"] if a["kind"] == "gaps-negative-claim-unreconciled"]
    # The "No faithful … mouse reproduces …" universal-negative is flagged; the benign
    # "ratios remain unmeasured" line (no no/never/only trigger) is not.
    assert len(neg) == 1
    assert "UBE3A-attributable mouse" in neg[0]["text"]
    assert "gaps-negative-claim-unreconciled" in LR.render_audit(res)


def test_gaps_negative_lint_is_function_level():
    """Direct check on the motivating bullet + a couple of pattern variants, away from the audit."""
    flagged = lambda body: [a["text"] for a in LR.gaps_negative_claim_advisories(body)]  # noqa: E731
    base = "## Gaps / open questions\n{bullet}\n"
    # the real defect, the exact phrasing
    assert flagged(base.format(
        bullet="- No faithful UBE3A-attributable mouse for human maternal Dup15q exists."))
    # other universal-negative forms over a survey subject
    assert flagged(base.format(bullet="- The dose-response was never measured in primates."))
    assert flagged(base.format(bullet="- Only one lab has reproduced the seizure model."))
    # a plain open question with no universal-negative is NOT flagged
    assert not flagged(base.format(bullet="- How timing modulates the phenotype is open."))
    # a universal-negative OUTSIDE the gaps section is not this lint's job
    assert not LR.gaps_negative_claim_advisories(
        "## Body\nNo model reproduces the phenotype.\n## Gaps / open questions\nAll settled.\n")


# --------------------------------------------------------------------------- #
# stale-grounding guard (cheap mtime check; warn, don't block)
# --------------------------------------------------------------------------- #
def test_stale_grounding_warns_when_module_is_newer(tmp_path):
    import os

    review = _full_litreview(tmp_path)
    prog = tmp_path / "program"
    gr = prog / "analysis" / "grounding_report.json"
    claims_dir = prog / "claims"
    claims_dir.mkdir(exist_ok=True)
    mod = claims_dir / "test_litreview_it_biodist.py"
    mod.write_text("# claims source\n", encoding="utf-8")
    base = gr.stat().st_mtime
    os.utime(gr, (base, base))
    os.utime(mod, (base + 100, base + 100))       # module edited after the grounding was emitted

    res = LR.audit(review, home=tmp_path)
    assert res["status"] == "GROUNDED"            # non-blocking
    assert res.get("warnings")
    assert any("re-run pytest --grounding-out" in w["detail"] for w in res["warnings"])
    assert "stale-grounding" in LR.render_audit(res)


# --------------------------------------------------------------------------- #
# list_reviews — the "what context exists" coverage scan (res litreview --list)
# --------------------------------------------------------------------------- #
def test_list_reviews_scans_the_tree(tmp_path):
    _full_litreview(tmp_path, slug="it-biodist")
    _full_litreview(tmp_path, slug="aso-chemistry")
    rows = LR.list_reviews(tmp_path)
    assert [r["id"] for r in rows] == ["program::aso-chemistry", "program::it-biodist"]
    biodist = next(r for r in rows if r["slug"] == "it-biodist")
    assert biodist["title"] == "IT ASO biodistribution"
    assert biodist["question"] == "How a lumbar ASO distributes across the CNS."
    assert biodist["as_of"] == "2026-06-19"
    assert biodist["sources"] == ["openalex", "pubmed"]
    assert biodist["funnel"] == {"identified": 4, "included": 3, "excluded": 1, "pending": 0}
    assert biodist["tree"] is False
    # render is human-readable and surfaces the citation id + question
    out = LR.render_list(rows)
    assert "[litreview:program::it-biodist]" in out
    assert "How a lumbar ASO distributes across the CNS." in out


def test_list_reviews_empty_tree(tmp_path):
    (tmp_path / "program").mkdir()
    assert LR.list_reviews(tmp_path) == []
    assert "no litreviews found" in LR.render_list([])
