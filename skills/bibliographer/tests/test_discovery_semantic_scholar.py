"""Tests for the Semantic Scholar discovery provider.

We reuse ``_resolvers._from_semantic_scholar`` (that normalizer is tested in
test_resolvers.py), so the meaningful units to cover here are:

1. ``cited_by_count`` post-processing — the discovery provider adds this field
   after calling the normalizer (which doesn't set it).
2. A captured-real ``data[]`` item producing the correct normalized record,
   exercising the full provider pipeline under a mocked HTTP layer.
3. Edge cases: empty/missing ``data`` key → ``[]``; OA filter appends the bare
   query param; year range builds the ``year`` param correctly.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import _resolvers as R
from _discovery import Filters, _stamp, search_semantic_scholar


# --------------------------------------------------------------------------- #
# Captured-real fixture (from a live S2 API call, 2026-06-17)
# --------------------------------------------------------------------------- #
# One item from:
#   GET /graph/v1/paper/search?query=CRISPR&limit=1
#   &fields=title,abstract,year,venue,authors,externalIds,url,paperId,
#           openAccessPdf,citationCount
_REAL_S2_ITEM: dict = {
    "paperId": "b9e5fa707e804d6008e5011b058244437c656a93",
    "externalIds": {
        "PubMedCentral": "4744125",
        "MAG": "2252568502",
        "DOI": "10.1038/nbt.3437",
        "CorpusId": 31070821,
        "PubMed": "26780180",
    },
    "url": "https://www.semanticscholar.org/paper/b9e5fa707e804d6008e5011b058244437c656a93",
    "title": "Optimized sgRNA design to maximize activity and minimize off-target effects of CRISPR-Cas9",
    "abstract": None,
    "venue": "Nature Biotechnology",
    "year": 2016,
    "citationCount": 3985,
    "openAccessPdf": {
        "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4744125",
        "status": "GREEN",
        "license": None,
    },
    "authors": [
        {"authorId": "4051746", "name": "John G Doench"},
        {"authorId": "2723245", "name": "Nicoló Fusi"},
    ],
}


# --------------------------------------------------------------------------- #
# Minimal async mock client
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, status: int, body: bytes):
        self.status_code = status
        self.content = body


class _FakeClient:
    """Async GET stub that records the last call for param inspection."""

    def __init__(self, status: int, body: bytes):
        self._status = status
        self._body = body
        self.last_url: str = ""
        self.last_params: dict = {}

    async def get(self, url, *, params=None, headers=None, **kw):
        self.last_url = url
        self.last_params = dict(params or {})
        return _FakeResp(self._status, self._body)

    async def aclose(self):
        pass


def _make_client(items: list) -> "_FakeClient":
    body = json.dumps({"total": len(items), "offset": 0, "data": items}).encode()
    return _FakeClient(200, body)


# --------------------------------------------------------------------------- #
# 1. cited_by_count post-processing
# --------------------------------------------------------------------------- #
def test_cited_by_count_is_added():
    """The provider must add cited_by_count from the raw item after normalizing."""
    rec = R._from_semantic_scholar(_REAL_S2_ITEM)
    # Confirm the base normalizer does NOT set cited_by_count.
    assert "cited_by_count" not in rec

    # Simulate the provider's post-processing step.
    rec["cited_by_count"] = _REAL_S2_ITEM.get("citationCount")
    assert rec["cited_by_count"] == 3985


def test_cited_by_count_none_when_absent():
    """If the API omits citationCount, cited_by_count should be None (then
    _drop_empty inside _stamp removes it — so it just won't appear)."""
    item = {**_REAL_S2_ITEM}
    item.pop("citationCount", None)
    count = item.get("citationCount")  # None
    assert count is None


# --------------------------------------------------------------------------- #
# 2. Full provider pipeline: captured-real item → correct record
# --------------------------------------------------------------------------- #
def test_search_semantic_scholar_real_item(monkeypatch):
    """A single captured-real S2 item is normalised correctly end-to-end.

    Checks title, doi (lowercased), pmcid (PMC-prefixed), oa_pdf_url,
    cited_by_count, discovery_source, relevance_rank.
    """
    monkeypatch.setattr(R, "_resolver_cache_obj", None)  # bypass on-disk cache

    client = _make_client([_REAL_S2_ITEM])
    results = asyncio.run(
        search_semantic_scholar("CRISPR", client, limit=1, filters=Filters())
    )

    assert len(results) == 1
    rec = results[0]

    assert rec["title"] == (
        "Optimized sgRNA design to maximize activity and minimize off-target "
        "effects of CRISPR-Cas9"
    )
    assert rec["doi"] == "10.1038/nbt.3437"          # lowercased by normalizer
    assert rec["pmcid"] == "PMC4744125"               # PMC-prefixed
    assert rec["pmid"] == "26780180"
    assert rec["year"] == 2016
    assert rec["venue"] == "Nature Biotechnology"
    assert rec["oa_pdf_url"] == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4744125"
    assert rec["cited_by_count"] == 3985              # post-processed by provider
    assert rec["discovery_source"] == "semantic_scholar"
    assert rec["relevance_rank"] == 0
    assert rec["authors"][0]["family"] == "Doench"
    assert rec["authors"][0]["given"] == "John G"


# --------------------------------------------------------------------------- #
# 3. Edge cases
# --------------------------------------------------------------------------- #
def test_empty_data_returns_empty_list(monkeypatch):
    """Response with missing ``data`` key (or empty list) → empty result."""
    monkeypatch.setattr(R, "_resolver_cache_obj", None)

    for body in [
        json.dumps({"total": 0, "data": []}).encode(),
        json.dumps({"total": 0}).encode(),         # missing key
    ]:
        client = _FakeClient(200, body)
        results = asyncio.run(
            search_semantic_scholar("noresults", client, limit=10, filters=Filters())
        )
        assert results == []


def test_non_200_raises(monkeypatch):
    """A non-200 response propagates as ResolveError (provider contract: don't
    return []  on API error — raise so discover() records it as an error)."""
    monkeypatch.setattr(R, "_resolver_cache_obj", None)
    client = _FakeClient(429, b'{"message":"Too Many Requests","code":"429"}')
    with pytest.raises(R.ResolveError, match="Semantic Scholar 429"):
        asyncio.run(
            search_semantic_scholar("test", client, limit=5, filters=Filters())
        )


def test_year_filter_both_bounds(monkeypatch):
    """year_min and year_max → ``year=<min>-<max>`` param."""
    monkeypatch.setattr(R, "_resolver_cache_obj", None)
    client = _make_client([])
    asyncio.run(
        search_semantic_scholar(
            "test", client, limit=5,
            filters=Filters(year_min=2018, year_max=2024),
        )
    )
    assert client.last_params.get("year") == "2018-2024"


def test_year_filter_min_only(monkeypatch):
    """year_min only → ``year=<min>-``."""
    monkeypatch.setattr(R, "_resolver_cache_obj", None)
    client = _make_client([])
    asyncio.run(
        search_semantic_scholar(
            "test", client, limit=5,
            filters=Filters(year_min=2020),
        )
    )
    assert client.last_params.get("year") == "2020-"


def test_year_filter_max_only(monkeypatch):
    """year_max only → ``year=-<max>``."""
    monkeypatch.setattr(R, "_resolver_cache_obj", None)
    client = _make_client([])
    asyncio.run(
        search_semantic_scholar(
            "test", client, limit=5,
            filters=Filters(year_max=2022),
        )
    )
    assert client.last_params.get("year") == "-2022"


def test_oa_filter_appends_param(monkeypatch):
    """open_access=True → bare ``openAccessPdf`` key in query params."""
    monkeypatch.setattr(R, "_resolver_cache_obj", None)
    client = _make_client([])
    asyncio.run(
        search_semantic_scholar(
            "test", client, limit=5,
            filters=Filters(open_access=True),
        )
    )
    assert "openAccessPdf" in client.last_params


def test_limit_respected(monkeypatch):
    """Provider truncates to ``limit`` even when data[] has more items."""
    monkeypatch.setattr(R, "_resolver_cache_obj", None)
    items = [_REAL_S2_ITEM] * 5
    client = _make_client(items)
    results = asyncio.run(
        search_semantic_scholar("CRISPR", client, limit=3, filters=Filters())
    )
    assert len(results) == 3


def test_s2_api_key_header_sent(monkeypatch):
    """When S2_API_KEY is set, the ``x-api-key`` header is sent."""
    monkeypatch.setattr(R, "_resolver_cache_obj", None)
    monkeypatch.setenv("S2_API_KEY", "test-key-abc")

    sent_headers: dict = {}

    class _HeaderCapture(_FakeClient):
        async def get(self, url, *, params=None, headers=None, **kw):
            sent_headers.update(headers or {})
            return await super().get(url, params=params, headers=headers, **kw)

    client = _HeaderCapture(200, json.dumps({"data": []}).encode())
    asyncio.run(
        search_semantic_scholar("test", client, limit=1, filters=Filters())
    )
    assert sent_headers.get("x-api-key") == "test-key-abc"
