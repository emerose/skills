"""Offline tests for the PubMed discovery provider's normalizer.

The fixture is a single item captured from a real esummary response (trimmed to
the fields ``_from_pubmed`` reads). No network is needed.
"""

from bibliographer.discovery import _from_pubmed

_ITEM = {
    "uid": "38189543",
    "pubdate": "2023 Nov 22",
    "epubdate": "",
    "source": "Brief Bioinform",
    "fulljournalname": "Briefings in bioinformatics",
    "title": "Attention is all you need: utilizing attention in AI-enabled drug discovery.",
    "authors": [
        {"name": "Zhang Y", "authtype": "Author"},
        {"name": "Huang CB", "authtype": "Author"},
        {"name": "Ning L", "authtype": "Author"},
    ],
    "articleids": [
        {"idtype": "pubmed", "value": "38189543"},
        {"idtype": "pmc", "value": "PMC10772984"},
        {"idtype": "pmcid", "value": "pmc-id: PMC10772984;"},
        {"idtype": "doi", "value": "10.1093/bib/bbad467"},
    ],
}


def test_core_fields():
    rec = _from_pubmed(_ITEM)
    assert rec["title"].startswith("Attention is all you need")
    assert rec["year"] == 2023
    assert rec["venue"] == "Briefings in bioinformatics"
    assert rec["doi"] == "10.1093/bib/bbad467"
    assert rec["pmid"] == "38189543"
    assert rec["source_url"] == "https://pubmed.ncbi.nlm.nih.gov/38189543/"


def test_pmcid_prefers_clean_form():
    # idtype "pmc" wins over the verbose "pmcid" blob.
    assert _from_pubmed(_ITEM)["pmcid"] == "PMC10772984"


def test_authors_surname_first():
    authors = _from_pubmed(_ITEM)["authors"]
    assert authors[0] == {"family": "Zhang", "given": "Y"}
    assert authors[1] == {"family": "Huang", "given": "CB"}


def test_collective_name_excluded():
    item = {**_ITEM, "authors": [
        {"name": "Smith J", "authtype": "Author"},
        {"name": "ENCODE Consortium", "authtype": "CollectiveName"},
    ]}
    rec = _from_pubmed(item)
    assert len(rec["authors"]) == 1
    assert rec["authors"][0]["family"] == "Smith"


def test_no_abstract_key():
    # esummary has no abstract; the key must be absent, not empty.
    assert "abstract" not in _from_pubmed(_ITEM)


def test_drops_missing_ids():
    item = {"uid": "999", "pubdate": "2020", "source": "J Ex",
            "title": "Minimal", "authors": [{"name": "Doe J", "authtype": "Author"}],
            "articleids": [{"idtype": "pubmed", "value": "999"}]}
    rec = _from_pubmed(item)
    assert "doi" not in rec and "pmcid" not in rec
    assert rec["venue"] == "J Ex"
