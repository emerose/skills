"""Bibliometric (meta) claims — ``@kind("bibliometric")`` grounded on a stored OpenAlex metric via
``scientist.grounding.metric``/``cited_by`` (a claim ABOUT the literature, e.g. "most-cited"), not a
quote in a paper. Covers the runtime capture seam, the pure audit helpers, and the end-to-end
``report.audit`` path (backing, the staleness pin, the as_of freshness advisory).

Pure: seed the per-process ``_PAPER_CACHE`` with a ``PaperRef`` carrying credibility (so no DuckDB /
library), or hand-write a ``grounding_report.json`` (so no pytest subprocess). No keys, no model.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from grounding import Capture
from grounding._capture import _CURRENT
import research as grounding
from research import LiteratureError, PaperRef, cited_by, metric
from research import report as R


# --------------------------------------------------------------------------- #
# runtime: metric() / cited_by() read a stored metric, record it, return the value
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clear_cache():
    grounding._PAPER_CACHE.clear()
    yield
    grounding._PAPER_CACHE.clear()


def _seed(citekey: str, *, credibility: dict, text: str = "some text") -> PaperRef:
    ref = PaperRef(citekey=citekey, sha256="deadbeef", mode="fulltext", title="T", year="2020",
                   doi="10.1/x", is_retracted=bool(credibility.get("is_retracted")),
                   credibility=credibility, text=text)
    grounding._PAPER_CACHE[citekey] = ref
    return ref


def _in_capture():
    """Activate a fresh capture (as the plugin does per claim) and return it."""
    cap = Capture(claim_id="t")
    _CURRENT.set(cap)
    return cap


def test_cited_by_returns_value_and_records_source():
    _seed("a2020", credibility={"cited_by_count": 202, "as_of": "2026-06"})
    cap = _in_capture()
    val = cited_by("a2020")
    assert val == 202                                   # bare value → plain-Python assert in claims
    ms = cap.evidence["metric_sources"]
    assert len(ms) == 1
    rec = ms[0]
    assert rec["citekey"] == "a2020" and rec["metric"] == "cited_by_count"
    assert rec["value"] == 202 and rec["as_of"] == "2026-06" and rec["source"] == "openalex"


def test_metric_generic_name():
    _seed("a2020", credibility={"fwci": 6.1, "as_of": "2026-06"})
    _in_capture()
    assert metric("a2020", "fwci") == 6.1


def test_metric_missing_raises_loudly():
    _seed("a2020", credibility={"as_of": "2026-06"})   # no cited_by_count
    _in_capture()
    with pytest.raises(LiteratureError, match="not in the library record"):
        cited_by("a2020")


def test_metric_as_of_absent_is_none_not_error():
    _seed("a2020", credibility={"cited_by_count": 5})   # no as_of (not yet enriched)
    cap = _in_capture()
    assert cited_by("a2020") == 5
    assert cap.evidence["metric_sources"][0]["as_of"] is None


def test_credibility_surfaces_as_of():
    rec = {"cited_by_count": 9, "metrics": {"as_of": "2026-06-01", "fwci": 1.2}}
    c = grounding._credibility_from_rec(rec)
    assert c["as_of"] == "2026-06-01"
    # falls back to updated_date when as_of absent
    rec2 = {"metrics": {"updated_date": "2025-01-01"}}
    assert grounding._credibility_from_rec(rec2)["as_of"] == "2025-01-01"


# --------------------------------------------------------------------------- #
# pure audit helpers
# --------------------------------------------------------------------------- #
def test_bucket_metric_tolerates_small_drift():
    # +1 must NOT change the 2-sig-fig bucket (no churn), a material jump must
    assert R._bucket_metric(202) == R._bucket_metric(203) == "200"
    assert R._bucket_metric(202) != R._bucket_metric(260)


def _claim(node="test_x", *, value=202, value2=31, as_of="2026-06", support=True,
           outcome="passed", kind="bibliometric", sources=True) -> dict:
    ms = []
    if sources:
        ms = [{"citekey": "a2020", "metric": "cited_by_count", "value": value, "as_of": as_of},
              {"citekey": "b2020", "metric": "cited_by_count", "value": value2, "as_of": as_of}]
    reviewed = {"support": support} if support is not None else None
    return {"id": f"program/claims/test_litreview_x.py::{node}", "statement": "S",
            "outcome": outcome, "kind": kind, "strength": "moderate",
            "reviewed": reviewed,
            "evidence": {"metric_sources": ms}, "inputs": []}


def test_metric_review_sha_stable_under_tolerance_changes_on_material():
    base = R.metric_review_sha(_claim(value=202))
    assert base == R.metric_review_sha(_claim(value=203))        # +1 → same pin (bucketed)
    assert base != R.metric_review_sha(_claim(value=260))        # material → new pin
    assert base != R.metric_review_sha(_claim(as_of="2027-01"))  # refreshed snapshot → new pin


def test_bibliometric_verdict_paths():
    assert R.bibliometric_verdict(_claim())[0] == "backed"
    assert R.bibliometric_verdict(_claim(support=None))[0] == "needs-review"
    assert R.bibliometric_verdict(_claim(support=False))[0] == "unsupported"
    assert R.bibliometric_verdict(_claim(sources=False))[0] == "no-metric"
    assert R.bibliometric_verdict(_claim(outcome="failed"))[0] == "broken"
    assert R.bibliometric_verdict(_claim(kind="literature"))[0] == "wrong-kind"


def test_asof_age_days():
    assert R._asof_age_days("not-a-date") is None
    assert R._asof_age_days("2000-01-01") > 365        # clearly old
    assert R._asof_age_days("1999") > 365


# --------------------------------------------------------------------------- #
# end-to-end: report.audit backs / flags a [lit:] citation to a bibliometric claim
# --------------------------------------------------------------------------- #
def _program(tmp_path: Path, claim: dict) -> Path:
    prog = tmp_path / "program"
    (prog / "analysis").mkdir(parents=True, exist_ok=True)
    (prog / "analysis" / "grounding_report.json").write_text(
        json.dumps({"claims": [claim]}, indent=2), encoding="utf-8")
    return prog


def _report_md(prog: Path, body: str) -> Path:
    d = prog / "reports" / "dosing"
    d.mkdir(parents=True, exist_ok=True)
    md = d / "report.md"
    md.write_text(body, encoding="utf-8")
    return md


_BODY = """---
title: "Dosing"
---

## Centrality
The most-cited result is not the depth datum [lit:test_most_cited].
"""


def test_report_backs_pinned_bibliometric_claim(tmp_path):
    claim = _claim("test_most_cited")
    claim["reviewed"]["sha"] = R.metric_review_sha(claim)[:12]   # correctly pinned
    prog = _program(tmp_path, claim)
    report = _report_md(prog, _BODY)
    res = R.audit(report, home=tmp_path)
    assert res["status"] == "GROUNDED", res["findings"]
    lc = next(c for c in res["lit_cites"] if c["id"] == "test_most_cited")
    assert lc["verdict"] == "backed"


def test_report_blocks_unreviewed_bibliometric_claim(tmp_path):
    claim = _claim("test_most_cited", support=None)             # no @reviewed
    prog = _program(tmp_path, claim)
    report = _report_md(prog, _BODY)
    res = R.audit(report, home=tmp_path)
    assert res["status"] == "BROKEN"
    assert any(f["kind"] == "needs-review-lit" for f in res["findings"])


def test_report_flags_stale_pin(tmp_path):
    claim = _claim("test_most_cited")
    claim["reviewed"]["sha"] = "stale00000000"                  # wrong pin
    prog = _program(tmp_path, claim)
    report = _report_md(prog, _BODY)
    res = R.audit(report, home=tmp_path)
    assert res["status"] == "BROKEN"
    assert any(f["kind"] == "stale-review-lit" for f in res["findings"])


def test_report_asof_unknown_is_advisory_not_blocking(tmp_path):
    claim = _claim("test_most_cited", as_of=None)               # no as_of recorded
    claim["reviewed"]["sha"] = R.metric_review_sha(claim)[:12]
    prog = _program(tmp_path, claim)
    report = _report_md(prog, _BODY)
    res = R.audit(report, home=tmp_path)
    assert res["status"] == "GROUNDED", res["findings"]         # advisory, not blocking
    assert any(a["kind"] == "metric-asof-unknown" for a in res["advisories"])
