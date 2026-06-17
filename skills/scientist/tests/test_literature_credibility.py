"""Retraction integrity check + display credibility markers on literature sources.

These exercise the grounding layer without a real bibliographer library by seeding
the per-process ``_PAPER_CACHE`` with a ``PaperRef`` directly, so no DuckDB / libkit
is touched. The pure ``_credibility_from_rec`` mapping is tested on synthetic records.

Run: ``uv run --with-editable skills/scientist pytest
skills/scientist/tests/test_literature_credibility.py -q``.
"""
from __future__ import annotations

import pytest

import scientist.grounding as grounding
from scientist.grounding import LiteratureError, PaperRef, paper, source


@pytest.fixture(autouse=True)
def _clear_cache():
    grounding._PAPER_CACHE.clear()
    yield
    grounding._PAPER_CACHE.clear()


def _seed(citekey: str, *, retracted: bool, text: str = "the quoted phrase here",
          credibility: dict | None = None) -> PaperRef:
    cred = credibility if credibility is not None else {"is_retracted": retracted}
    ref = PaperRef(citekey=citekey, sha256="deadbeef", mode="fulltext",
                   title="T", year="2020", doi="10.1/x",
                   is_retracted=retracted, credibility=cred, text=text)
    grounding._PAPER_CACHE[citekey] = ref
    return ref


# --------------------------------------------------------------------------- #
# _credibility_from_rec — pure mapping (display markers, never a score)
# --------------------------------------------------------------------------- #
_REC = {
    "cited_by_count": 106,
    "metrics": {
        "source": "openalex", "fwci": 6.078, "citation_percentile": 0.9691,
        "open_access": "gold", "work_type": "article", "is_retracted": False,
        "venue": {"type": "journal", "in_doaj": True, "indexed_in_scopus": None,
                  "impact_2yr": 5.488, "h_index": 172},
    },
}


def test_credibility_maps_metrics():
    c = grounding._credibility_from_rec(_REC)
    assert c["fwci"] == 6.078
    assert c["citation_percentile"] == 0.9691
    assert c["cited_by_count"] == 106
    assert c["open_access"] == "gold"
    assert c["work_type"] == "article"
    assert c["venue_type"] == "journal"
    assert c["in_doaj"] is True
    assert c["journal_impact_2yr"] == 5.488
    assert c["journal_h_index"] == 172
    assert c["is_retracted"] is False          # False kept (checked, not retracted)
    assert "indexed_in_scopus" not in c         # None dropped


def test_credibility_empty_without_metrics():
    assert grounding._credibility_from_rec({}) == {}
    assert grounding._credibility_from_rec({"cited_by_count": 5}) == {"cited_by_count": 5}


# --------------------------------------------------------------------------- #
# retraction integrity check
# --------------------------------------------------------------------------- #
def test_paper_retracted_raises():
    _seed("retracted2020paper", retracted=True)
    with pytest.raises(LiteratureError, match="RETRACTED"):
        paper("retracted2020paper")


def test_paper_allow_retracted_overrides():
    _seed("retracted2020paper", retracted=True)
    ref = paper("retracted2020paper", allow_retracted=True)
    assert ref.is_retracted is True


def test_paper_not_retracted_ok():
    _seed("sound2020paper", retracted=False)
    ref = paper("sound2020paper")
    assert ref.is_retracted is False


def test_source_on_retracted_raises_before_quote_check():
    _seed("retracted2020paper", retracted=True, text="the quoted phrase here")
    with pytest.raises(LiteratureError, match="RETRACTED"):
        source("retracted2020paper", quote="the quoted phrase here")


# --------------------------------------------------------------------------- #
# credibility markers ride along on a source() record (display only)
# --------------------------------------------------------------------------- #
def test_source_carries_credibility():
    cred = {"is_retracted": False, "fwci": 6.1, "in_doaj": True, "venue_type": "journal"}
    _seed("sound2020paper", retracted=False, text="alpha beta gamma", credibility=cred)
    rec = source("sound2020paper", quote="beta gamma")
    assert rec["credibility"] == cred
    # markers are context only — they must not leak into the assert/strength path
    assert rec["citekey"] == "sound2020paper" and rec["primary"] is True
