"""Unit tests for the pure metadata helpers (stdlib only — no deps, no network)."""

from types import SimpleNamespace

import _meta


def test_make_citekey():
    rec = {"title": "Attention Is All You Need",
           "authors": [{"family": "Vaswani", "given": "Ashish"}], "year": 2017}
    assert _meta.make_citekey(rec) == "vaswani2017attention"


def test_make_citekey_fallbacks():
    # no authors -> "anon"; first non-stopword title token
    assert _meta.make_citekey({"title": "The Great Paper", "year": 2020}) == "anon2020great"
    # no year -> "nd"; uses authors_text when structured authors absent
    assert _meta.make_citekey({"title": "On Widgets", "authors_text": "Doe, Jane"}) == "doendwidgets"


def test_authors_text_and_short():
    a = [{"family": "Vaswani", "given": "Ashish"}, {"family": "Shazeer", "given": "Noam"}]
    assert _meta.authors_text(a) == "Vaswani, Ashish; Shazeer, Noam"
    assert _meta.short_authors({"authors_text": "Vaswani, Ashish"}) == "Vaswani"
    assert _meta.short_authors({"authors_text": "A, X; B, Y"}) == "A & B"
    assert _meta.short_authors({"authors_text": "A, X; B, Y; C, Z"}) == "A et al."
    assert _meta.short_authors({}) == "?"


def test_norm_title():
    assert _meta.norm_title("A, B: C's Study!") == "a b cs study"


def test_to_bibtex():
    rec = {"citekey": "v2017", "title": "Attn", "year": 2017, "doi": "10.1/x",
           "authors": [{"family": "Vaswani", "given": "Ashish"}],
           "venue": "NeurIPS", "bibtex_type": "inproceedings"}
    b = _meta.to_bibtex(rec)
    assert b.startswith("@inproceedings{v2017,")
    assert "author = {Vaswani, Ashish}" in b
    assert "booktitle = {NeurIPS}" in b  # inproceedings -> booktitle, not journal


def test_stub_markdown_is_deterministic():
    rec = {"title": "T", "authors_text": "Doe, Jane", "year": 2020,
           "doi": "10.1/x", "abstract": "hello world"}
    a = _meta.stub_markdown({**rec, "citekey": "x"})
    b = _meta.stub_markdown({**rec, "citekey": "y"})
    assert a == b  # citekey is NOT in the body -> re-ingest is idempotent
    assert "# T" in a and "hello world" in a


def test_record_to_metadata_drops_empty():
    m = _meta.record_to_metadata({"title": "T", "venue": "", "tags": [], "year": 2020, "x": None})
    assert m == {"title": "T", "year": 2020}


def test_document_to_record_overlays_libkit_fields():
    doc = SimpleNamespace(
        metadata={"doi": "10.1/x", "citekey": "k", "title": "stale"},
        title="Real Title", document_id="abc", content_hash="abc",
        source_url="u", content_type="application/pdf", page_count=3, chunk_count=5,
    )
    r = _meta.document_to_record(doc)
    assert r["doi"] == "10.1/x" and r["citekey"] == "k"
    assert r["title"] == "Real Title"  # libkit column wins over stale metadata
    assert r["document_id"] == "abc" and r["_page_count"] == 3
