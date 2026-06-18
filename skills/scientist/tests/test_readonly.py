"""The read-only reader path: read-only `sci` store subcommands open the libkit
store ``read_only=True`` (no write lock, so they run concurrently); write
subcommands open it read-write. Two layers are exercised:

1. A spy over ``Store.open`` confirms each command's classification (a
   representative read read-only, a representative write read-write), so the
   ``_READ_ONLY_COMMANDS`` list can't silently drift from real call sites.
2. Against real libkit (fake embedder), a record indexed read-write is readable
   through a store opened ``read_only=True`` — and a write through that same
   read-only store raises ``ReadOnlyStore``.

The integration half is skipped automatically if libkit isn't installed.
"""

import asyncio

import pytest

from scientist.store import cli
from scientist.store._store import Store


# ---- classification: which commands open read-only vs read-write ------------

def _spy_read_only(monkeypatch, tmp_path, argv):
    """Run ``sci <argv>`` with ``Store.open`` and the command body stubbed, and
    return the ``read_only`` flag the store-open wrapper was called with."""
    captured = {}

    class _FakeStore:
        async def close(self):
            return None

    def fake_open(home, *, read_only=False, **kw):
        captured["read_only"] = read_only
        return _FakeStore()

    async def noop(store, args):
        return None

    monkeypatch.setattr(Store, "open", staticmethod(fake_open))
    monkeypatch.setattr(cli, "_require_initialized", lambda home: None)
    monkeypatch.setattr(cli, "_load_dotenv", lambda home: None)
    # Stub the resolved command body so nothing real runs.
    monkeypatch.setitem(cli.COMMANDS, argv[0], noop)

    # Drive cli._run directly with a namespace carrying just what _run reads.
    import argparse

    ns = argparse.Namespace(cmd=argv[0], home=str(tmp_path))
    asyncio.run(cli._run(ns))
    return captured["read_only"]


@pytest.mark.parametrize("cmd", sorted(cli._READ_ONLY_COMMANDS))
def test_read_only_commands_open_read_only(monkeypatch, tmp_path, cmd):
    assert _spy_read_only(monkeypatch, tmp_path, [cmd]) is True


@pytest.mark.parametrize("cmd", ["index", "reindex", "new", "intake", "review"])
def test_write_commands_open_read_write(monkeypatch, tmp_path, cmd):
    assert _spy_read_only(monkeypatch, tmp_path, [cmd]) is False


# ---- integration: real libkit, read-only open actually reads & blocks writes -

libkit = pytest.importorskip("libkit")

from libkit import Library, LibraryConfig  # noqa: E402
from libkit.concurrency import ConcurrencyHint  # noqa: E402
from libkit.errors import ReadOnlyStore  # noqa: E402
from libkit.loaders.markdown import MarkdownLoader  # noqa: E402
from libkit.types import ChunkText  # noqa: E402

_DIM = 32


class _FakeEmbedder:
    @property
    def dim(self) -> int:
        return _DIM

    async def embed_documents(self, texts):
        return [[0.0] * _DIM for _ in texts]

    async def embed_query(self, text):
        return [0.0] * _DIM

    def concurrency_hint(self) -> ConcurrencyHint:
        return ConcurrencyHint(initial=1)


class _FakeChunker:
    def chunk(self, markdown: str):
        return [ChunkText(text=markdown or " ", start_index=0, end_index=len(markdown))]


def _cfg(tmp_path, *, read_only):
    return LibraryConfig(
        db_path=tmp_path / ".scientist" / "catalog.duckdb",
        embedder=_FakeEmbedder(),
        chunker=_FakeChunker(),
        loaders={".md": MarkdownLoader(), ".txt": MarkdownLoader()},
        cache_enabled=False,
        read_only=read_only,
    )


def test_readonly_open_reads_but_refuses_writes(tmp_path):
    (tmp_path / ".scientist").mkdir()

    async def seed():
        store = Store(tmp_path, Library(_cfg(tmp_path, read_only=False)))
        try:
            await store.upsert_experiment({"exp_id": "K1-000000", "name": "Readable"})
        finally:
            await store.close()

    asyncio.run(seed())

    async def read_then_try_write():
        store = Store(tmp_path, Library(_cfg(tmp_path, read_only=True)))
        try:
            rec = await store.get_experiment("K1-000000")     # read works
            assert rec is not None and rec["exp_id"] == "K1-000000"
            with pytest.raises(ReadOnlyStore):                # write is refused
                await store.upsert_experiment({"exp_id": "K1-000001", "name": "Nope"})
        finally:
            await store.close()

    asyncio.run(read_then_try_write())


def test_open_read_only_missing_store_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        Store.open(tmp_path, read_only=True)
