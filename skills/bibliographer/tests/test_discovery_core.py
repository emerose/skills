"""Offline tests for the discovery core: OpenAlex normalizer + cross-provider merge."""

from _discovery import (
    Filters,
    _from_openalex,
    _openalex_abstract,
    _stamp,
    merge_candidates,
)


# --------------------------------------------------------------------------- #
# OpenAlex normalizer
# --------------------------------------------------------------------------- #
_OA_WORK = {
    "id": "https://openalex.org/W123",
    "ids": {"openalex": "https://openalex.org/W123", "pmid": "https://pubmed.ncbi.nlm.nih.gov/12345678"},
    "doi": "https://doi.org/10.1038/S41586-021-03819-2",
    "title": "Highly accurate protein structure prediction",
    "publication_year": 2021,
    "authorships": [
        {"author": {"display_name": "John Jumper"}},
        {"author": {"display_name": "Demis Hassabis"}},
    ],
    "primary_location": {"source": {"display_name": "Nature"}},
    "abstract_inverted_index": {"Proteins": [0], "are": [1], "essential": [2]},
    "cited_by_count": 32660,
}


def test_openalex_core_fields():
    rec = _from_openalex(_OA_WORK)
    assert rec["title"] == "Highly accurate protein structure prediction"
    assert rec["year"] == 2021
    assert rec["doi"] == "10.1038/s41586-021-03819-2"   # lowercased, prefix stripped
    assert rec["pmid"] == "12345678"
    assert rec["venue"] == "Nature"
    assert rec["cited_by_count"] == 32660
    assert rec["authors"][0] == {"family": "Jumper", "given": "John"}


def test_openalex_abstract_reconstruction():
    assert _openalex_abstract({"Proteins": [0], "are": [1], "essential": [2]}) == "Proteins are essential"
    assert _openalex_abstract(None) is None
    assert _openalex_abstract({}) is None


# --------------------------------------------------------------------------- #
# cross-provider merge / dedup
# --------------------------------------------------------------------------- #
def test_merge_dedups_by_doi_and_corroborates():
    # Same paper from two sources (one richer), plus a unique third.
    openalex = _stamp([
        {"doi": "10.1/a", "title": "Paper A", "year": 2020, "cited_by_count": 100},
        {"doi": "10.1/b", "title": "Paper B", "year": 2021},
    ], "openalex")
    pubmed = _stamp([
        {"doi": "10.1/a", "title": "Paper A", "year": 2020, "abstract": "rich abstract", "pmid": "111"},
    ], "pubmed")

    merged = merge_candidates([openalex, pubmed])
    assert len(merged) == 2
    # Paper A found by both → corroborated → sorts first.
    a = merged[0]
    assert a["doi"] == "10.1/a"
    assert a["source_count"] == 2
    assert a["found_in"] == ["openalex", "pubmed"]
    # Fields union across sources (abstract+pmid from pubmed, citations from openalex).
    assert a["abstract"] == "rich abstract"
    assert a["pmid"] == "111"
    assert a["cited_by_count"] == 100
    # discovery_source/relevance_rank are internal — stripped from merged output.
    assert "discovery_source" not in a and "relevance_rank" not in a


def test_merge_dedups_by_title_when_no_identifier():
    g1 = _stamp([{"title": "A Shared Title", "year": 2019}], "crossref")
    g2 = _stamp([{"title": "a shared   title", "year": 2019, "venue": "J"}], "arxiv")
    merged = merge_candidates([g1, g2])
    assert len(merged) == 1
    assert merged[0]["source_count"] == 2


def test_merge_keeps_distinct_papers_apart():
    g1 = _stamp([{"doi": "10.1/x", "title": "X"}], "openalex")
    g2 = _stamp([{"doi": "10.1/y", "title": "Y"}], "openalex")
    assert len(merge_candidates([g1, g2])) == 2


def test_filters_dataclass_defaults():
    assert Filters() == Filters(year_min=None, year_max=None, open_access=False)
