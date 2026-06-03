"""Offline tests for the resolver helpers (no network — pure parsing/logic)."""

import _resolvers as R


def test_classify_identifier():
    assert R.classify_identifier("10.1038/nphys1170") == ("doi", "10.1038/nphys1170")
    assert R.classify_identifier("doi:10.1/x") == ("doi", "10.1/x")
    assert R.classify_identifier("arXiv:1706.03762v5") == ("arxiv", "1706.03762v5")
    assert R.classify_identifier("https://arxiv.org/abs/1706.03762") == ("arxiv", "1706.03762")
    assert R.classify_identifier("PMC9283931") == ("pmcid", "PMC9283931")
    assert R.classify_identifier("PMID:17284678") == ("pmid", "17284678")
    assert R.classify_identifier("something else") == ("unknown", "something else")


def test_sniff_filename_reconstructions():
    assert ("doi", "10.1038/s41598-024-55666-6") in R.sniff_filename("s41598_024_55666_6.pdf")
    assert ("doi", "10.1038/nbt.3779") in R.sniff_filename("nbt_3779.pdf")
    # \b would miss PMC next to an underscore — the filename path uses a tolerant regex
    assert ("pmcid", "PMC9283931") in R.sniff_filename("317_pmc9283931.pdf")
    assert ("doi", "10.1038/nphys1170") in R.sniff_filename("10.1038_nphys1170.pdf")
    assert R.sniff_filename("rudick1982.pdf") == []  # no false positives


def test_reference_doi_is_distrusted():
    """A DOI that appears only in the reference list must not pass content check."""
    text = ("ETHICAL PUBLICATION STATEMENT. ORCID Jane Doe. "
            "REFERENCES 1. Smith J. Some cited paper about widgets. doi:10.1234/cited")
    before, after = R._split_at_references(text)
    assert R._extract_ids(before) == []
    assert ("doi", "10.1234/cited") in R._extract_ids(after)
    # the cited title's words aren't in the pre-references content -> low overlap
    assert R._title_overlap("Some cited paper about widgets", before) < 0.34


def test_title_overlap_matches_real_content():
    title = "Quantitative electrophysiological biomarker of duplication syndrome"
    content = "we report a quantitative electrophysiological biomarker of the duplication syndrome ..."
    assert R._title_overlap(title, content) >= 0.8


def test_from_crossref_strips_title_markup():
    cr = R._from_crossref({"title": ["A <i>gene</i> study"], "DOI": "10.1/X",
                           "type": "journal-article", "issued": {"date-parts": [[2020, 3]]}})
    assert "<" not in cr["title"] and "gene study" in cr["title"]
    assert cr["year"] == 2020 and cr["bibtex_type"] == "article"


def test_from_semantic_scholar_normalizes():
    s2 = R._from_semantic_scholar({
        "title": "T", "paperId": "p1", "year": 2019,
        "externalIds": {"DOI": "10.2/Y", "PubMedCentral": 9283931, "ArXiv": "1234.5678"},
        "authors": [{"name": "Sam Quigley"}],
        "openAccessPdf": {"url": "https://example.org/x.pdf"},
    })
    assert s2["doi"] == "10.2/y"  # lowercased
    assert s2["pmcid"] == "PMC9283931"  # PMC-prefixed
    assert s2["authors"][0] == {"family": "Quigley", "given": "Sam"}
    assert s2["oa_pdf_url"] == "https://example.org/x.pdf"
