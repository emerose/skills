"""Tests for the stub-backfill helpers and command.

The pure helpers (article_url / stub_records / worklist_entry) run with no deps.
The command test uses the same fake-embedder store as test_store.py and a
monkeypatched OA resolver, so it needs libkit but no network or PDF backend.
"""

import asyncio
import hashlib
import struct
import types

import pytest

import bib


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #
def test_article_url_prefers_doi():
    assert bib.article_url({"doi": "10.5/a", "pmid": "123"}) == "https://doi.org/10.5/a"


def test_article_url_falls_back_through_identifiers():
    assert bib.article_url({"arxiv_id": "1706.03762v5"}) == "https://arxiv.org/abs/1706.03762"
    assert bib.article_url({"pmcid": "PMC9283931"}) == (
        "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9283931/"
    )
    assert bib.article_url({"pmid": "31452104"}) == "https://pubmed.ncbi.nlm.nih.gov/31452104/"
    assert bib.article_url({"title": "no ids"}) is None


def test_stub_records_filters_on_content_state():
    recs = [
        {"citekey": "a", "content_state": "stub"},
        {"citekey": "b", "content_state": "full"},
        {"citekey": "c"},  # no state -> not a stub
    ]
    assert [r["citekey"] for r in bib.stub_records(recs)] == ["a"]


def test_worklist_entry_collects_ids_and_url():
    e = bib.worklist_entry(
        {"citekey": "vaswani2017attention", "title": "Attention", "year": 2017,
         "doi": "10.5/a", "s2_id": "abc", "authors": [{"family": "Vaswani", "given": "A"}]}
    )
    assert e["citekey"] == "vaswani2017attention"
    assert e["ids"] == {"doi": "10.5/a", "s2_id": "abc"}
    assert e["url"] == "https://doi.org/10.5/a"


# --------------------------------------------------------------------------- #
# command — needs libkit (fake embedder, no network)
# --------------------------------------------------------------------------- #
libkit = pytest.importorskip("libkit")

from _store import BiblioStore  # noqa: E402
from libkit import Library, LibraryConfig  # noqa: E402
from libkit.concurrency import ConcurrencyHint  # noqa: E402
from libkit.loaders.markdown import MarkdownLoader  # noqa: E402
from libkit.types import ChunkText  # noqa: E402

_DIM = 32


class _FakeEmbedder:
    @property
    def dim(self) -> int:
        return _DIM

    def _vec(self, text: str) -> list[float]:
        seed = hashlib.sha256(text.encode()).digest()
        raw = (seed * (_DIM * 4 // len(seed) + 1))[: _DIM * 4]
        return [v / 1e9 for v in struct.unpack(f"{_DIM}i", raw)]

    async def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    async def embed_query(self, text):
        return self._vec(text)

    def concurrency_hint(self) -> ConcurrencyHint:
        return ConcurrencyHint(initial=1)


class _FakeChunker:
    def chunk(self, markdown: str):
        return [ChunkText(text=markdown or " ", start_index=0, end_index=len(markdown))]


@pytest.fixture
def store(tmp_path):
    (tmp_path / "papers").mkdir()
    cfg = LibraryConfig(
        db_path=tmp_path / "catalog.duckdb",
        embedder=_FakeEmbedder(),
        chunker=_FakeChunker(),
        loaders={".md": MarkdownLoader()},
        cache_enabled=False,
    )
    return BiblioStore(tmp_path, Library(cfg))


def _run(store, coro_factory):
    async def main():
        try:
            return await coro_factory()
        finally:
            await store.close()
    return asyncio.run(main())


def _args(**kw):
    return types.SimpleNamespace(tag=None, limit=None, dry_run=False, json=True, **kw)


def test_backfill_worklists_stubs_with_no_oa(store, monkeypatch, capsys):
    """With every OA fetch failing, no file is attached and each stub lands on
    the worklist with its identifiers + resolvable URL."""

    import _resolvers

    async def _no_oa(rec, dest, client):
        return None

    monkeypatch.setattr(_resolvers, "acquire_oa_pdf", _no_oa)

    def go():
        async def inner():
            await store.add({"title": "Stubby", "year": 2020, "doi": "10.9/x",
                             "authors": [{"family": "Roe", "given": "R"}]})
            await bib.cmd_backfill(_args(), store)
        return inner()

    _run(store, go)
    out = capsys.readouterr().out
    import json
    payload = json.loads(out)
    assert payload["checked"] == 1
    assert payload["fetched"] == []
    assert payload["remaining"][0]["ids"] == {"doi": "10.9/x"}
    assert payload["remaining"][0]["url"] == "https://doi.org/10.9/x"


def test_backfill_reports_nothing_when_no_stubs(store, capsys):
    def go():
        async def inner():
            await bib.cmd_backfill(_args(), store)
        return inner()

    _run(store, go)
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"checked": 0, "fetched": [], "remaining": []}
