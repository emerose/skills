"""Embedder resilience: reads + full-text search work with NO embedder.

`BiblioStore.open()` used to build an embedder eagerly even for keyless,
read-only reads, so a paper-text read crashed when no embedding backend was
configured. The fix opens read-only commands FTS-only (no embedder) and only
builds an embedder when a command actually wants semantic search (`bib query`),
loudly flagging the FTS-only fallback instead of silently degrading.

These tests inject a fake/absent embedder through ``BiblioStore._build_embedder``
so they need no real model or API key. They require libkit >= 0.5.0 (for
``Library.open_reader`` + ``query(fts_only=...)``).
"""

import asyncio
import hashlib
import struct

import pytest

pytest.importorskip("libkit")

from libkit import Library, LibraryConfig  # noqa: E402

if not hasattr(Library, "open_reader"):  # FTS-only / no-embedder open landed in 0.5.0
    pytest.skip("requires libkit>=0.5.0 (Library.open_reader)", allow_module_level=True)

from bibliographer.store import BiblioStore  # noqa: E402
from libkit.concurrency import ConcurrencyHint  # noqa: E402
from libkit.errors import EmbedderUnavailable  # noqa: E402
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


def _seed_library(home):
    """Create a real (fake-embedder) library under ``home`` with one paper."""
    (home / "papers").mkdir(parents=True, exist_ok=True)
    cfg = LibraryConfig(
        db_path=home / "catalog.duckdb",
        embedder=_FakeEmbedder(),
        chunker=_FakeChunker(),
        loaders={".md": MarkdownLoader()},
        cache_enabled=False,
    )
    store = BiblioStore(home, Library(cfg))

    async def _go():
        doc = home / "papers" / "paper.md"
        doc.write_text(
            "# Widget Transport\n\nThe membrane shuttles widgets across the lipid bilayer.",
            encoding="utf-8",
        )
        await store.add(
            {"title": "Widget Transport", "year": 2024, "doi": "10.1/widget",
             "authors": [{"family": "Ng", "given": "N"}]},
            file_path=doc,
        )
        await store.close()

    asyncio.run(_go())


# ---- the failing case: reads + FTS with no embedder -------------------------


def test_reads_and_fts_work_without_embedder(tmp_path):
    _seed_library(tmp_path)

    async def _go():
        # read_only without want_semantic => FTS-only open, NO embedder built.
        store = BiblioStore.open(tmp_path, read_only=True)
        try:
            assert store.semantic_available is False

            # metadata read
            rec = await store.get_by_citekey("ng2024widget")
            assert rec is not None and rec["title"] == "Widget Transport"

            # full document text read (the operation that used to crash)
            text = await store.leading_text(rec["document_id"], chunks=100)
            assert "membrane shuttles widgets" in text

            # all_records / FTS search
            recs = await store.all_records()
            assert len(recs) == 1

            # explicit FTS-only query works
            hits = await store.query("widgets", fts_only=True)
            assert hits and any("widget" in h.chunk.text.lower() for h in hits)

            # a SEMANTIC query without an embedder raises (never silent FTS)
            with pytest.raises(EmbedderUnavailable):
                await store.query("widgets")
        finally:
            await store.close()

    asyncio.run(_go())


def test_open_records_reason_when_semantic_requested_but_unavailable(tmp_path, monkeypatch):
    _seed_library(tmp_path)
    # Simulate "no embedding backend": the probe returns (None, reason).
    monkeypatch.setattr(
        BiblioStore, "_build_embedder",
        staticmethod(lambda embedding, model: (None, "embedding='local' needs a local model")),
    )

    async def _go():
        store = BiblioStore.open(tmp_path, read_only=True, want_semantic=True)
        try:
            assert store.semantic_available is False
            assert store.embedder_reason and "local model" in store.embedder_reason
        finally:
            await store.close()

    asyncio.run(_go())


def test_semantic_query_works_when_embedder_available(tmp_path, monkeypatch):
    _seed_library(tmp_path)
    # Simulate "embedder available": the probe returns a matching fake embedder.
    monkeypatch.setattr(
        BiblioStore, "_build_embedder",
        staticmethod(lambda embedding, model: (_FakeEmbedder(), None)),
    )

    async def _go():
        store = BiblioStore.open(tmp_path, read_only=True, want_semantic=True)
        try:
            assert store.semantic_available is True
            hits = await store.query("membrane")  # hybrid path, no raise
            assert hits
        finally:
            await store.close()

    asyncio.run(_go())


def test_writable_open_without_embedder_errors_actionably(tmp_path, monkeypatch):
    _seed_library(tmp_path)
    # Force the writable open's embedder construction to fail (no backend).
    from libkit import embedders as _emb

    def _boom(**_):
        raise RuntimeError("embedding='local' needs a local model")

    monkeypatch.setattr(_emb, "default_embedder", _boom)
    # A writable open must refuse with actionable guidance, not a raw crash.
    from bibliographer.store import EmbedderConfigError

    with pytest.raises(EmbedderConfigError) as exc:
        BiblioStore.open(tmp_path, read_only=False)
    msg = str(exc.value)
    assert "DEEPINFRA_API_KEY" in msg or "fancychunk" in msg
    # And it tells the user reads still work without an embedder.
    assert "read-only" in msg.lower()
