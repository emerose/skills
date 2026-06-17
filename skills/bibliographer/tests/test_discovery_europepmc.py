"""Offline unit tests for the Europe PMC discovery provider.

No network is touched — the fixture below is a captured real response from:
  GET https://www.ebi.ac.uk/europepmc/webservices/rest/search
      ?query=EXT_ID:34265844&format=json&pageSize=1&resultType=core
  (retrieved 2026-06-17)

Run with:
  uv run --with pytest --with httpx pytest tests/test_discovery_europepmc.py -q
"""

from _discovery import Filters, _from_europepmc

# ---------------------------------------------------------------------------
# Captured fixture — AlphaFold2 paper (PMID 34265844), trimmed to the fields
# _from_europepmc actually reads so the fixture stays readable.
# ---------------------------------------------------------------------------
_ALPHAFOLD2_RESULT = {
    "id": "34265844",
    "source": "MED",
    "pmid": "34265844",
    "pmcid": "PMC8371605",
    "doi": "10.1038/s41586-021-03819-2",
    "title": "Highly accurate protein structure prediction with AlphaFold.",
    "authorString": (
        "Jumper J, Evans R, Pritzel A, Green T, Figurnov M, Ronneberger O, "
        "Tunyasuvunakool K, Bates R, Žídek A, Potapenko A, Bridgland A, "
        "Meyer C, Kohl SAA, Ballard AJ, Cowie A, Romera-Paredes B, Nikolov S, "
        "Jain R, Adler J, Back T, Petersen S, Reiman D, Clancy E, Zielinski M, "
        "Steinegger M, Pacholska M, Berghammer T, Bodenstein S, Silver D, "
        "Vinyals O, Senior AW, Kavukcuoglu K, Kohli P, Hassabis D."
    ),
    "authorList": {
        "author": [
            {"fullName": "Jumper J", "firstName": "John", "lastName": "Jumper", "initials": "J"},
            {"fullName": "Evans R", "firstName": "Richard", "lastName": "Evans", "initials": "R"},
            {"fullName": "Pritzel A", "firstName": "Alexander", "lastName": "Pritzel", "initials": "A"},
            {"fullName": "Green T", "firstName": "Tim", "lastName": "Green", "initials": "T"},
            {"fullName": "Figurnov M", "firstName": "Michael", "lastName": "Figurnov", "initials": "M"},
            {"fullName": "Ronneberger O", "firstName": "Olaf", "lastName": "Ronneberger", "initials": "O"},
            {"fullName": "Hassabis D", "firstName": "Demis", "lastName": "Hassabis", "initials": "D"},
        ]
    },
    "journalInfo": {
        "issue": "7873",
        "volume": "596",
        "journal": {
            "title": "Nature",
            "medlineAbbreviation": "Nature",
        },
    },
    "pubYear": "2021",
    "abstractText": (
        "Proteins are essential to life, and understanding their structure can "
        "facilitate a mechanistic understanding of their function. Through an "
        "enormous experimental effort<sup>1-4</sup>, the structures of around "
        "100,000 unique proteins have been determined<sup>5</sup>."
    ),
    "isOpenAccess": "Y",
    "citedByCount": 32660,
    "fullTextUrlList": {
        "fullTextUrl": [
            {
                "availability": "Open access",
                "availabilityCode": "OA",
                "documentStyle": "pdf",
                "site": "Unpaywall",
                "url": "https://www.nature.com/articles/s41586-021-03819-2.pdf",
            },
            {
                "availability": "Open access",
                "availabilityCode": "OA",
                "documentStyle": "pdf",
                "site": "Europe_PMC",
                "url": "https://europepmc.org/articles/PMC8371605?pdf=render",
            },
        ]
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_title():
    rec = _from_europepmc(_ALPHAFOLD2_RESULT)
    assert rec["title"] == "Highly accurate protein structure prediction with AlphaFold."


def test_year():
    rec = _from_europepmc(_ALPHAFOLD2_RESULT)
    assert rec["year"] == 2021


def test_doi_lowercase():
    rec = _from_europepmc(_ALPHAFOLD2_RESULT)
    assert rec["doi"] == "10.1038/s41586-021-03819-2"
    assert rec["doi"] == rec["doi"].lower()


def test_pmid_and_pmcid():
    rec = _from_europepmc(_ALPHAFOLD2_RESULT)
    assert rec["pmid"] == "34265844"
    assert rec["pmcid"] == "PMC8371605"


def test_venue_from_journal_info():
    """journalInfo.journal.title is used when top-level journalTitle is absent."""
    rec = _from_europepmc(_ALPHAFOLD2_RESULT)
    assert rec["venue"] == "Nature"


def test_venue_prefers_top_level_journaltitle():
    """Top-level journalTitle wins over journalInfo when both present."""
    r = dict(_ALPHAFOLD2_RESULT, journalTitle="Nature (override)")
    rec = _from_europepmc(r)
    assert rec["venue"] == "Nature (override)"


def test_cited_by_count():
    rec = _from_europepmc(_ALPHAFOLD2_RESULT)
    assert rec["cited_by_count"] == 32660


def test_authors_prefer_author_list():
    """When authorList is present its lastName/firstName are used, not authorString."""
    rec = _from_europepmc(_ALPHAFOLD2_RESULT)
    assert rec["authors"][0] == {"family": "Jumper", "given": "John"}
    assert rec["authors"][1] == {"family": "Evans", "given": "Richard"}
    assert rec["authors"][-1] == {"family": "Hassabis", "given": "Demis"}


def test_authors_fall_back_to_author_string():
    """Without authorList, authorString is split on commas and parsed."""
    r = {**_ALPHAFOLD2_RESULT, "authorList": {}, "authorString": "Smith JA, Jones B."}
    rec = _from_europepmc(r)
    # _author_from_display_name splits "Smith JA" → given="Smith", family="JA"
    # (same heuristic as the rest of the discovery layer)
    assert len(rec["authors"]) == 2
    assert rec["authors"][0]["family"] == "JA"
    assert rec["authors"][0]["given"] == "Smith"


def test_abstract_jats_stripped():
    """JATS superscript tags in abstractText are stripped to plain text."""
    rec = _from_europepmc(_ALPHAFOLD2_RESULT)
    assert "<sup>" not in rec["abstract"]
    assert "Proteins are essential" in rec["abstract"]
    assert "100,000 unique proteins" in rec["abstract"]


def test_oa_pdf_url_first_oa_pdf():
    """First OA+pdf entry in fullTextUrlList is returned (Unpaywall before Europe PMC)."""
    rec = _from_europepmc(_ALPHAFOLD2_RESULT)
    assert rec["oa_pdf_url"] == "https://www.nature.com/articles/s41586-021-03819-2.pdf"


def test_source_url_uses_doi():
    rec = _from_europepmc(_ALPHAFOLD2_RESULT)
    assert rec["source_url"] == "https://doi.org/10.1038/s41586-021-03819-2"


def test_source_url_falls_back_to_pmid():
    """When there is no DOI, source_url is built from PMID."""
    r = {**_ALPHAFOLD2_RESULT, "doi": None}
    rec = _from_europepmc(r)
    assert rec.get("doi") is None
    assert "34265844" in rec["source_url"]


def test_bibtex_type():
    rec = _from_europepmc(_ALPHAFOLD2_RESULT)
    assert rec["bibtex_type"] == "article"


def test_source_field():
    rec = _from_europepmc(_ALPHAFOLD2_RESULT)
    assert rec["source"] == "europepmc"


def test_no_empty_values():
    """_drop_empty is applied — no None, '', [], or {} values in output."""
    rec = _from_europepmc(_ALPHAFOLD2_RESULT)
    for k, v in rec.items():
        assert v not in (None, "", [], {}), f"empty value for key {k!r}"


def test_missing_oa_pdf_omitted():
    """oa_pdf_url is absent (not None) when no OA PDF URL is available."""
    r = {**_ALPHAFOLD2_RESULT, "fullTextUrlList": {}}
    rec = _from_europepmc(r)
    assert "oa_pdf_url" not in rec


def test_filters_dataclass():
    """Sanity-check that the Filters dataclass is importable and behaves correctly."""
    f = Filters(year_min=2020, year_max=2023, open_access=True)
    assert f.year_min == 2020
    assert f.open_access is True
    f2 = Filters()
    assert f2.year_min is None
    assert f2.open_access is False
