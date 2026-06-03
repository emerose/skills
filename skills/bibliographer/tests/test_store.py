"""Integration tests for BiblioStore against real libkit, using a fake embedder +
Markdown loader + trivial chunker — so no model download and no API keys.

Skipped automatically if libkit isn't installed. Needs network the first time
DuckDB fetches its vss/fts extensions (cached thereafter).
"""

import asyncio
import hashlib
import struct

import pytest

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


def test_add_stub_then_dedup_by_doi(store):
    async def go():
        rec = {"title": "Attention Is All You Need", "year": 2017, "doi": "10.5/a",
               "authors": [{"family": "Vaswani", "given": "Ashish"}]}
        r = await store.add(dict(rec))
        assert r["status"] == "added"
        assert r["record"]["citekey"] == "vaswani2017attention"
        assert r["record"]["content_state"] == "stub"        # no file -> citation stub
        assert not r["record"].get("file_path")
        # same DOI -> refused as duplicate
        dup = await store.add({"title": "different", "year": 2017, "doi": "10.5/a"})
        assert dup["status"] == "duplicate"
    _run(store, go)


def test_dedup_by_title_year_without_identifier(store):
    async def go():
        await store.add({"title": "Some Unique Paper", "year": 2018,
                         "authors_text": "Doe, Jane"})
        dup = await store.add({"title": "some unique paper!", "year": 2018})
        assert dup["status"] == "duplicate"
    _run(store, go)


def test_tags_and_byte_dup_merge(store):
    async def go():
        rec = {"title": "Paper", "year": 2019, "doi": "10.5/b", "tags": ["nlp"]}
        r = await store.add(dict(rec))
        ck = r["record"]["citekey"]
        tagged = await store.set_tags(ck, add=["to-read"], remove=["nlp"])
        assert set(tagged["tags"]) == {"to-read"}
        # re-ingesting a byte-identical stub (force past identifier dedup) merges tags
        merged = await store.add({**rec, "tags": ["seminal"]}, force=True)
        assert merged["status"] == "merged"
        assert "seminal" in merged["record"]["tags"]
        assert len(await store.all_records()) == 1     # still one document
    _run(store, go)


def test_attach_pdf_upgrades_stub(store, tmp_path):
    async def go():
        r = await store.add({"title": "Upgradeable", "year": 2021, "doi": "10.5/c",
                             "authors": [{"family": "Roe", "given": "R"}]})
        ck = r["record"]["citekey"]
        assert r["record"]["content_state"] == "stub"
        pdf = tmp_path / "real.md"          # any ingestible file; .md needs no PDF backend
        pdf.write_text("# Upgradeable\n\nFull text body.\n")
        new = await store.attach_pdf(ck, pdf, move=False)
        assert new["citekey"] == ck                       # citekey preserved
        assert new["content_state"] == "full"
        assert new.get("file_path")
        assert len(await store.all_records()) == 1         # stub replaced, not duplicated
    _run(store, go)


def test_remove(store):
    async def go():
        r = await store.add({"title": "Tossable", "year": 2022, "doi": "10.5/d"})
        ck = r["record"]["citekey"]
        await store.remove(ck, delete_file=False)
        assert await store.get_by_citekey(ck) is None
        assert len(await store.all_records()) == 0
    _run(store, go)
