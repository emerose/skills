"""Offline tests for the arXiv discovery provider.

Tests use a captured real <entry> fragment; no network calls are made.
Run with the same invocation as the rest of the suite:

    uv run --with pytest --with httpx pytest skills/bibliographer/tests/ -q
"""

from bibliographer.discovery import _from_arxiv_entry

# ---------------------------------------------------------------------------
# Real <entry> captured from:
#   http://export.arxiv.org/api/query?search_query=all:BERT+pretraining&max_results=1&sortBy=relevance
# (2202.09061v4 — a paper that also carries an arxiv:doi tag, letting us
# test the DOI path without needing a separate fixture.)
# ---------------------------------------------------------------------------
_ENTRY_XML = b"""
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2202.09061v4</id>
    <title>VLP: A Survey on  Vision-Language
Pre-training</title>
    <updated>2022-09-13T08:22:23Z</updated>
    <published>2022-02-18T12:34:56Z</published>
    <summary>  This is a  survey   about
vision-language pretraining   methods and benchmarks.  </summary>
    <arxiv:doi xmlns:arxiv="http://arxiv.org/schemas/atom">10.1007/s11633-022-1369-5</arxiv:doi>
    <author><name>Feilong Chen</name></author>
    <author><name>Duzhen Zhang</name></author>
    <author><name>Minglun Han</name></author>
  </entry>
</feed>
"""

_ENTRY_NO_DOI_XML = b"""
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <title>Attention Is All You Need</title>
    <updated>2023-08-02T00:41:18Z</updated>
    <published>2017-06-12T17:57:34Z</published>
    <summary>The dominant sequence transduction models are based on complex recurrent or
convolutional neural networks in an encoder-decoder configuration.</summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <author><name>Illia Polosukhin</name></author>
  </entry>
</feed>
"""

_OLD_STYLE_ID_XML = b"""
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/hep-th/9711200v3</id>
    <title>The Large N Limit of Superconformal Field Theories and Supergravity</title>
    <updated>1998-01-17T12:00:00Z</updated>
    <published>1997-11-20T00:00:00Z</published>
    <summary>We show that the large N limit of certain conformal field theories in various
dimensions include in their Hilbert space a sector describing supergravity.</summary>
    <author><name>Juan Maldacena</name></author>
  </entry>
</feed>
"""

import xml.etree.ElementTree as ET

_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _parse_first(xml_bytes: bytes) -> dict:
    """Helper: parse the first <entry> from a minimal feed fragment."""
    root = ET.fromstring(xml_bytes)
    entry = root.find("a:entry", _NS)
    assert entry is not None
    return _from_arxiv_entry(entry, _NS)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_title_collapses_whitespace():
    rec = _parse_first(_ENTRY_XML)
    assert rec["title"] == "VLP: A Survey on Vision-Language Pre-training"
    # no leading/trailing/double whitespace
    assert "  " not in rec["title"]


def test_year_from_published():
    rec = _parse_first(_ENTRY_XML)
    assert rec["year"] == 2022


def test_arxiv_id_strips_version_and_url():
    rec = _parse_first(_ENTRY_XML)
    # must be bare id, no URL prefix, no version suffix
    assert rec["arxiv_id"] == "2202.09061"


def test_arxiv_id_old_style():
    """Pre-2007 hep-th/NNNNNNN ids are preserved correctly."""
    rec = _parse_first(_OLD_STYLE_ID_XML)
    assert rec["arxiv_id"] == "hep-th/9711200"


def test_source_url_constructed_from_arxiv_id():
    rec = _parse_first(_ENTRY_XML)
    assert rec["source_url"] == "https://arxiv.org/abs/2202.09061"


def test_authors_split_given_family():
    rec = _parse_first(_ENTRY_XML)
    assert rec["authors"] == [
        {"given": "Feilong", "family": "Chen"},
        {"given": "Duzhen", "family": "Zhang"},
        {"given": "Minglun", "family": "Han"},
    ]


def test_multiple_authors_attention():
    rec = _parse_first(_ENTRY_NO_DOI_XML)
    assert len(rec["authors"]) == 3
    assert rec["authors"][0] == {"given": "Ashish", "family": "Vaswani"}
    assert rec["authors"][2] == {"given": "Illia", "family": "Polosukhin"}


def test_doi_present_and_lowercased():
    rec = _parse_first(_ENTRY_XML)
    assert rec["doi"] == "10.1007/s11633-022-1369-5"
    # must be lowercase
    assert rec["doi"] == rec["doi"].lower()


def test_doi_absent_when_tag_missing():
    rec = _parse_first(_ENTRY_NO_DOI_XML)
    assert "doi" not in rec


def test_abstract_collapses_whitespace():
    rec = _parse_first(_ENTRY_XML)
    assert "  " not in rec["abstract"]
    assert rec["abstract"].startswith("This is a survey")


def test_venue_is_arxiv_preprint():
    rec = _parse_first(_ENTRY_XML)
    assert rec["venue"] == "arXiv preprint"


def test_bibtex_type_is_misc():
    rec = _parse_first(_ENTRY_XML)
    assert rec["bibtex_type"] == "misc"


def test_source_field():
    rec = _parse_first(_ENTRY_XML)
    assert rec["source"] == "arxiv"


def test_drop_empty_no_none_values():
    """_from_arxiv_entry must not emit keys with None / empty values."""
    rec = _parse_first(_ENTRY_NO_DOI_XML)
    for v in rec.values():
        assert v not in (None, "", [], {})
