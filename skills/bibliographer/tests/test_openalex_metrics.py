"""Offline tests for the OpenAlex work+venue metrics normalizer.

Fixtures are trimmed from real OpenAlex responses (work + its Source) for the
tofersen phase-3 NEJM paper. No network — only `_openalex_metrics` is exercised.
"""

from _resolvers import _openalex_metrics

_WORK = {
    "id": "https://openalex.org/W4297675233",
    "doi": "https://doi.org/10.1056/nejmoa2204705",
    "cited_by_count": 577,
    "fwci": 90.9942,
    "citation_normalized_percentile": {
        "value": 0.99965222, "is_in_top_1_percent": True, "is_in_top_10_percent": True,
    },
    "is_retracted": False,
    "open_access": {"oa_status": "bronze"},
    "type": "article",
    "primary_location": {
        "source": {
            "id": "https://openalex.org/S62468778",
            "display_name": "New England Journal of Medicine",
            "type": "journal",
            "is_in_doaj": False,
            "is_indexed_in_scopus": None,   # OpenAlex populates this sparsely
            "issn_l": "0028-4793",
            "host_organization_name": "Massachusetts Medical Society",
        }
    },
}

_SOURCE = {
    "id": "https://openalex.org/S62468778",
    "summary_stats": {"2yr_mean_citedness": 80.512, "h_index": 1234, "i10_index": 9876},
}


def test_work_level_fields():
    m = _openalex_metrics(_WORK, _SOURCE)
    assert m["source"] == "openalex"
    assert m["openalex_id"] == "W4297675233"          # prefix stripped
    assert m["fwci"] == 90.994                         # rounded to 3
    assert m["citation_percentile"] == 0.9997          # rounded to 4
    assert m["open_access"] == "bronze"
    assert m["work_type"] == "article"


def test_is_retracted_kept_when_false():
    # An explicit "checked, not retracted" is itself a signal — must be stored.
    m = _openalex_metrics(_WORK, _SOURCE)
    assert m["is_retracted"] is False


def test_is_retracted_true():
    m = _openalex_metrics({**_WORK, "is_retracted": True}, _SOURCE)
    assert m["is_retracted"] is True


def test_venue_identity():
    v = _openalex_metrics(_WORK, _SOURCE)["venue"]
    assert v["name"] == "New England Journal of Medicine"
    assert v["type"] == "journal"
    assert v["in_doaj"] is False                       # False kept, not dropped
    assert v["issn_l"] == "0028-4793"
    assert v["publisher"] == "Massachusetts Medical Society"
    assert "indexed_in_scopus" not in v                # None dropped


def test_venue_journal_stats_from_source():
    v = _openalex_metrics(_WORK, _SOURCE)["venue"]
    assert v["impact_2yr"] == 80.512
    assert v["h_index"] == 1234


def test_no_source_drops_journal_stats_but_keeps_identity():
    v = _openalex_metrics(_WORK, None)["venue"]
    assert v["name"] == "New England Journal of Medicine"
    assert "impact_2yr" not in v
    assert "h_index" not in v


def test_no_primary_location_still_has_work_fields():
    m = _openalex_metrics({k: v for k, v in _WORK.items() if k != "primary_location"}, None)
    assert m["fwci"] == 90.994
    assert "venue" not in m
