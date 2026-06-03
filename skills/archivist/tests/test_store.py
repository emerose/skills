"""Integration tests for ArchivistStore against real libkit, using a fake embedder
+ Markdown loader + trivial chunker — so no model download and no API keys.

Skipped automatically if libkit isn't installed. Needs network the first time
DuckDB fetches its vss/fts extensions (cached thereafter).
"""

import asyncio
import hashlib
import struct

import pytest

libkit = pytest.importorskip("libkit")

from _store import ArchivistStore  # noqa: E402
import _meta  # noqa: E402
from libkit import Library, LibraryConfig  # noqa: E402
from libkit.concurrency import ConcurrencyHint  # noqa: E402
from libkit.loaders.markdown import MarkdownLoader  # noqa: E402
from libkit.types import ChunkText  # noqa: E402

_DIM = 32


class _FakeEmbedder:
    @property
    def dim(self) -> int:
        return _DIM

    def _vec(self, text):
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
    (tmp_path / ".archivist").mkdir()
    cfg = LibraryConfig(
        db_path=tmp_path / ".archivist" / "catalog.duckdb",
        embedder=_FakeEmbedder(),
        chunker=_FakeChunker(),
        loaders={".md": MarkdownLoader(), ".txt": MarkdownLoader()},
        cache_enabled=False,
    )
    return ArchivistStore(tmp_path, Library(cfg))


def _run(store, coro_factory):
    async def main():
        try:
            return await coro_factory()
        finally:
            await store.close()
    return asyncio.run(main())


def test_upsert_experiment_keyed_by_exp_id(store):
    async def go():
        r = await store.upsert_experiment({"exp_id": "K1-230901", "name": "Dose-Response",
                                           "cro_study_ids": ["C0790222"]})
        assert r["kind"] == "experiment"
        assert r["exp_id"] == "K1-230901"
        added = r["added_at"]
        # update: new content -> new document_id, but still one experiment record
        r2 = await store.upsert_experiment({"exp_id": "K1-230901", "name": "Dose-Response",
                                            "cro_study_ids": ["C0790222"], "status": "complete"})
        exps = await store.experiments()
        assert len(exps) == 1
        assert exps[0]["status"] == "complete"
        assert r2["added_at"] == added            # added_at preserved across updates
    _run(store, go)


def test_add_file_card_and_lookup_by_path(store):
    async def go():
        rec = {"exp_id": "K1-1", "path": "K1-1/data/x.csv", "filename": "x.csv",
               "role": "data", "file_type": "csv", "sha256": "abc",
               "indexed_as": _meta.INDEXED_SCHEMA}
        schema = {"columns": [{"name": "weight"}], "n_rows": 6}
        card = _meta.file_card_markdown(rec, schema=schema, preview="weight\n310")
        stored = await store.add_file(rec, card_markdown=card)
        assert stored["kind"] == "file"
        got = await store.get_file("K1-1/data/x.csv")
        assert got is not None and got["sha256"] == "abc"
        assert len(await store.files("K1-1")) == 1
    _run(store, go)


def test_add_file_narrative_ingests_real_file(store, tmp_path):
    async def go():
        f = tmp_path / "README.md"
        f.write_text("# Study\n\nThe key finding was a 25% residual expression.\n")
        rec = {"exp_id": "K1-1", "path": "K1-1/README.md", "filename": "README.md",
               "role": "readme", "file_type": "md", "sha256": "z",
               "indexed_as": _meta.INDEXED_CONTENT}
        await store.add_file(rec, ingest_path=f)
        hits = await store.query("residual expression", limit=5)
        assert any("residual" in h.chunk.text for h in hits)
    _run(store, go)


def test_txt_narrative_indexes_and_is_queryable(store, tmp_path):
    async def go():
        f = tmp_path / "DATA_QUALITY_NOTES.txt"
        f.write_text("Wells B3 and C4 were excluded due to bubble artifacts.\n")
        rec = {"exp_id": "K1-1", "path": "K1-1/DATA_QUALITY_NOTES.txt",
               "filename": "DATA_QUALITY_NOTES.txt", "role": "raw", "file_type": "txt",
               "sha256": "t", "indexed_as": _meta.INDEXED_CONTENT}
        await store.add_file(rec, ingest_path=f)          # .txt must not error
        hits = await store.query("bubble artifacts excluded wells", limit=5)
        assert any("bubble" in h.chunk.text for h in hits)
    _run(store, go)


def test_byte_identical_files_record_both_paths(store, tmp_path):
    async def go():
        a = tmp_path / "a.md"; a.write_text("# Same\n\nidentical bytes\n")
        b = tmp_path / "b.md"; b.write_text("# Same\n\nidentical bytes\n")  # identical content
        await store.add_file({"exp_id": "K1-1", "path": "K1-1/protocol/a.md",
                              "role": "protocol", "file_type": "md", "sha256": "x"}, ingest_path=a)
        await store.add_file({"exp_id": "K1-1", "path": "K1-1/reports/b.md",
                              "role": "report", "file_type": "md", "sha256": "x"}, ingest_path=b)
        files = await store.files("K1-1")
        assert len(files) == 1                       # one document (byte-identical)
        f = files[0]
        assert f["path"] == "K1-1/protocol/a.md"     # first-seen path is primary, preserved
        assert f.get("other_paths") == ["K1-1/reports/b.md"]   # duplicate path kept, not dropped
        # both paths resolve to the record
        assert (await store.get_file("K1-1/protocol/a.md")) is not None
        assert (await store.get_file("K1-1/reports/b.md")) is not None
        # re-indexing the duplicate path doesn't clobber the primary
        await store.add_file({"exp_id": "K1-1", "path": "K1-1/reports/b.md",
                              "role": "report", "file_type": "md", "sha256": "x"}, ingest_path=b)
        f2 = (await store.files("K1-1"))[0]
        assert f2["path"] == "K1-1/protocol/a.md"
        assert f2.get("other_paths") == ["K1-1/reports/b.md"]
    _run(store, go)


def test_add_file_replaces_changed_content(store, tmp_path):
    async def go():
        f = tmp_path / "README.md"
        f.write_text("# v1\n")
        rec = {"exp_id": "K1-1", "path": "K1-1/README.md", "filename": "README.md",
               "role": "readme", "file_type": "md", "sha256": "1"}
        await store.add_file(rec, ingest_path=f)
        f.write_text("# v2 changed\n")
        rec["sha256"] = "2"
        await store.add_file(rec, ingest_path=f)
        files = await store.files("K1-1")
        assert len(files) == 1                    # old document replaced, not duplicated
        assert files[0]["sha256"] == "2"
    _run(store, go)


def test_xor_ingest_argument(store):
    async def go():
        with pytest.raises(ValueError):
            await store.add_file({"path": "p"}, ingest_path=None, card_markdown=None)
    _run(store, go)


def test_set_tags_on_experiment(store):
    async def go():
        await store.upsert_experiment({"exp_id": "K1-5", "name": "x"})
        tagged = await store.set_tags(kind="experiment", key_field="exp_id", key="K1-5",
                                      add=["lead-candidate", "rat"], remove=[])
        assert set(tagged["tags"]) == {"lead-candidate", "rat"}
    _run(store, go)


def test_curated_entity_note(store):
    async def go():
        await store.upsert_entity({"entity_id": "aso-154", "title": "ASO 154",
                                   "note": "Lead candidate selected from the 10-ASO rat IT screen."})
        e = await store.get_entity("aso-154")
        assert e is not None and "Lead candidate" in e["note"]
    _run(store, go)
