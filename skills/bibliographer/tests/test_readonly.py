"""The read-only reader path: read-only `bib` subcommands open the store
``read_only=True`` (no write lock, so they run concurrently); write subcommands
open it read-write. Two layers are exercised:

1. A spy over ``BiblioStore.open`` confirms each command's classification
   (representative read read-only, representative write read-write), so the
   ``_READ_ONLY_COMMANDS`` list can't silently drift from real call sites.
2. Against real libkit (fake embedder), a record added read-write is readable
   through a store opened ``read_only=True`` — and a write through that same
   read-only store raises ``ReadOnlyStore``.

The integration half is skipped automatically if libkit isn't installed.
"""

import asyncio

import pytest

import bib
from bibliographer.store import BiblioStore


# ---- classification: which commands open read-only vs read-write ------------

def _spy_dispatch(monkeypatch, argv):
    """Run ``bib <argv>`` with a stubbed ``BiblioStore.open`` and return the
    ``read_only`` flag it was called with (or raise the dispatch's own error)."""
    captured = {}

    class _FakeStore:
        home = None

        def prune_empty_dirs(self):
            return 0

        async def close(self):
            return None

    def fake_open(home, *, read_only=False, **kw):
        captured["read_only"] = read_only
        store = _FakeStore()
        store.home = home
        return store

    async def noop(args, store):  # the command body — we only care about open()
        return None

    monkeypatch.setattr(BiblioStore, "open", staticmethod(fake_open))
    monkeypatch.setattr(bib, "BiblioStore", BiblioStore)

    args = bib.build_parser().parse_args(argv)
    # Replace the resolved handler so no real command runs.
    args.func = noop
    asyncio.run(bib.dispatch(args))
    return captured["read_only"]


@pytest.mark.parametrize("cmd", sorted(bib._READ_ONLY_COMMANDS))
def test_read_only_commands_open_read_only(monkeypatch, tmp_path, cmd):
    # Minimal valid argv per command (only the ones needing a positional).
    needs_arg = {"show": ["x"], "text": ["x"], "query": ["q"], "export": []}
    argv = ["--home", str(tmp_path), cmd, *needs_arg.get(cmd, [])]
    assert _spy_dispatch(monkeypatch, argv) is True


@pytest.mark.parametrize("argv", [
    ["add", "10.5/x"],
    ["tag", "k"],
    ["rm", "k"],
    ["viewer"],
])
def test_write_commands_open_read_write(monkeypatch, tmp_path, argv):
    full = ["--home", str(tmp_path), *argv]
    assert _spy_dispatch(monkeypatch, full) is False


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
        db_path=tmp_path / "catalog.duckdb",
        embedder=_FakeEmbedder(),
        chunker=_FakeChunker(),
        loaders={".md": MarkdownLoader()},
        cache_enabled=False,
        read_only=read_only,
    )


def test_readonly_open_reads_but_refuses_writes(tmp_path):
    (tmp_path / "papers").mkdir()

    async def seed():
        store = BiblioStore(tmp_path, Library(_cfg(tmp_path, read_only=False)))
        try:
            r = await store.add({"title": "Readable", "year": 2020, "doi": "10.5/ro"})
            return r["record"]["citekey"]
        finally:
            await store.close()

    citekey = asyncio.run(seed())

    async def read_then_try_write():
        store = BiblioStore(tmp_path, Library(_cfg(tmp_path, read_only=True)))
        try:
            rec = await store.get_by_citekey(citekey)        # read works
            assert rec is not None and rec["citekey"] == citekey
            with pytest.raises(ReadOnlyStore):               # write is refused
                await store.add({"title": "Nope", "year": 2021, "doi": "10.5/nope"})
        finally:
            await store.close()

    asyncio.run(read_then_try_write())


def test_open_read_only_missing_store_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        BiblioStore.open(tmp_path, read_only=True)
