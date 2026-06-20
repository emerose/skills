"""Integration tests for `bib compact` against real libkit + DuckDB.

Builds a real libkit library with a fake embedder + Markdown loader (no model
download, no API keys), churns it, then exercises the compaction: it shrinks the
file, preserves counts + the HNSW/FTS indexes, keeps the library usable
afterwards, honors --keep-backup, refuses when a writer holds the lock, and the
dry run changes nothing.

Skipped automatically if libkit isn't installed. Needs network the first time
DuckDB fetches its vss/fts extensions (cached thereafter).
"""

import asyncio
import hashlib
import struct

import pytest

libkit = pytest.importorskip("libkit")

from bibliographer import compact as _compact  # noqa: E402
from bibliographer.store import BiblioStore  # noqa: E402
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


class _ParagraphChunker:
    def chunk(self, markdown: str):
        paras = [p for p in (markdown or " ").split("\n\n") if p.strip()] or [" "]
        out, pos = [], 0
        for p in paras:
            out.append(ChunkText(text=p, start_index=pos, end_index=pos + len(p)))
            pos += len(p)
        return out


def _make_lib(home):
    cfg = LibraryConfig(
        db_path=home / "catalog.duckdb",
        embedder=_FakeEmbedder(),
        chunker=_ParagraphChunker(),
        loaders={".md": MarkdownLoader()},
        cache_enabled=False,
    )
    return Library(cfg)


def _doc_md(i: int) -> str:
    body = "\n\n".join(
        f"Para {j} of doc {i}. " + ("lorem ipsum dolor sit amet " * 6) for j in range(5)
    )
    return f"# Doc {i}\n\n{body}\n"


async def _build_churned(home, n_initial=60, rounds=4):
    """A real, churned library: add many, delete a slice + re-add each round."""
    (home / "papers").mkdir(exist_ok=True)
    src = home / "_src"
    src.mkdir(exist_ok=True)
    lib = _make_lib(home)
    ids = []
    try:
        for i in range(n_initial):
            f = src / f"d{i}.md"
            f.write_text(_doc_md(i))
            r = await lib.ingest(f, metadata={"citekey": f"d{i}"})
            ids.append(r.document_id)
        nxt = n_initial
        for _ in range(rounds):
            for did in ids[:15]:
                with __import__("contextlib").suppress(Exception):
                    await lib.delete(did)
            ids = ids[15:]
            for _ in range(15):
                f = src / f"d{nxt}.md"
                f.write_text(_doc_md(nxt))
                r = await lib.ingest(f, metadata={"citekey": f"d{nxt}"})
                ids.append(r.document_id)
                nxt += 1
    finally:
        await lib.close()
    return home / "catalog.duckdb"


def _counts(db):
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        d = con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        c = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        con.close()
    return d, c


def test_compact_shrinks_and_preserves(tmp_path):
    db = asyncio.run(_build_churned(tmp_path))
    before_size = db.stat().st_size
    before_counts = _counts(db)

    result = _compact.compact(tmp_path, keep_backup=False)

    assert result["documents"], "expected some documents"
    assert _counts(db) == before_counts          # nothing lost
    assert db.stat().st_size <= before_size       # never grows
    assert db.stat().st_size < before_size        # churned lib actually shrinks
    assert result["reclaimed"] > 0
    assert result["backup"] is None               # removed by default
    assert not (tmp_path / "catalog.duckdb.bloated-bak").exists()

    # Rebuilt file has both indexes and answers a vector query.
    _compact._verify(db, expect_docs=before_counts[0], expect_chunks=before_counts[1])


def test_library_usable_after_compact(tmp_path):
    asyncio.run(_build_churned(tmp_path))
    _compact.compact(tmp_path)

    # Re-open the real library via the same FTS-only reader path the CLI uses and
    # confirm reads + a fresh write both still work on the compacted file.
    async def go():
        store = BiblioStore(tmp_path, _make_lib(tmp_path))
        try:
            recs = await store.all_records()
            assert recs
            r = await store.add({"title": "Post Compact", "year": 2030, "doi": "10.9/z"})
            assert r["status"] == "added"
        finally:
            await store.close()

    asyncio.run(go())


def test_keep_backup(tmp_path):
    asyncio.run(_build_churned(tmp_path))
    result = _compact.compact(tmp_path, keep_backup=True)
    assert result["backup"]
    assert (tmp_path / "catalog.duckdb.bloated-bak").exists()


def test_dry_run_changes_nothing(tmp_path):
    db = asyncio.run(_build_churned(tmp_path))
    before = db.stat().st_size
    result = _compact.compact(tmp_path, dry_run=True)
    assert result["dry_run"] is True
    assert "would_do" in result
    assert "block_stats" in result
    assert db.stat().st_size == before            # untouched
    assert not (tmp_path / "catalog.duckdb.bloated-bak").exists()


def test_refuses_when_writer_active(tmp_path):
    asyncio.run(_build_churned(tmp_path))
    db = tmp_path / "catalog.duckdb"
    import filelock

    # Simulate a live writer by holding libkit's write lock.
    held = filelock.FileLock(str(db.with_name(db.name + ".writelock")))
    held.acquire()
    try:
        assert _compact.writer_active(db) is True
        with pytest.raises(_compact.CompactError):
            _compact.compact(tmp_path)
    finally:
        held.release()


def test_missing_catalog_errors(tmp_path):
    with pytest.raises(_compact.CompactError):
        _compact.compact(tmp_path)
