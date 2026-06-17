"""`bib text` — dump one paper's full stored library text (the exact string a
scientist ``[lit:]`` quote-check reads). Uses a real libkit store with a fake
embedder + trivial chunker (no model download, no API keys), same as test_store.

Skipped automatically if libkit isn't installed.
"""

import argparse
import asyncio
import hashlib
import struct

import pytest

libkit = pytest.importorskip("libkit")

import bib  # noqa: E402
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


def _args(citekey, *, offset=0, chars=None, all=False, json=False):
    return argparse.Namespace(citekey=citekey, offset=offset, chars=chars, all=all, json=json)


# A body comfortably longer than the default excerpt cap, with a marker near the
# start (inside the default window) and another past it (only --all reaches it).
_HEAD = ("# Antisense oligonucleotide knockdown\n\n"
         "The treatment reduced target transcript by 78% at the lumbar segment on Day 29. ")
_MARKER = "reduced target transcript by 78% at the lumbar segment on Day 29"
_FILLER = "Additional discussion of methods and controls. " * 200  # ~9.4k chars
_TAIL_MARKER = "the conclusion phrase appears only at the very end"
_BODY = _HEAD + _FILLER + "In closing, " + _TAIL_MARKER + "."
assert len(_BODY) > bib._DEFAULT_TEXT_CHARS  # the default must actually truncate


def test_text_default_is_a_bounded_excerpt(store, tmp_path, capsys):
    def go():
        async def _():
            md = tmp_path / "paper.md"
            md.write_text(_BODY)
            r = await store.add({"title": "ASO Knockdown Study", "year": 2021,
                                 "doi": "10.9/aso",
                                 "authors": [{"family": "Shao", "given": "X"}]},
                                file_path=md)
            assert r["record"]["content_state"] == "full"
            await bib.cmd_text(_args(r["record"]["citekey"]), store)
        return asyncio.run(_())

    go()
    out = capsys.readouterr()
    # Default returns a bounded excerpt: the head marker is in it, the tail is not.
    assert _MARKER in out.out
    assert _TAIL_MARKER not in out.out
    assert len(out.out.rstrip("\n")) == bib._DEFAULT_TEXT_CHARS
    # Size note goes to stderr (so a pipe stays pure) and flags that more remains.
    assert "stored text:" in out.err
    assert "--all" in out.err
    assert _MARKER not in out.err


def test_text_all_dumps_everything(store, tmp_path, capsys):
    def go():
        async def _():
            md = tmp_path / "paper.md"
            md.write_text(_BODY)
            r = await store.add({"title": "ASO Knockdown Study", "year": 2021,
                                 "doi": "10.9/asoall"}, file_path=md)
            await bib.cmd_text(_args(r["record"]["citekey"], all=True), store)
        return asyncio.run(_())

    go()
    out = capsys.readouterr()
    assert _TAIL_MARKER in out.out          # only --all reaches the end
    assert out.out.rstrip("\n") == _BODY
    assert "--all" not in out.err           # nothing more to fetch


def test_text_offset_and_chars_window(store, tmp_path, capsys):
    def go():
        async def _():
            md = tmp_path / "paper.md"
            md.write_text(_BODY)
            r = await store.add({"title": "ASO Knockdown Study", "year": 2021,
                                 "doi": "10.9/aso2"}, file_path=md)
            ck = r["record"]["citekey"]
            await bib.cmd_text(_args(ck, offset=2, chars=20), store)
            return ck
        return asyncio.run(_())

    go()
    out = capsys.readouterr()
    body = out.out.rstrip("\n")
    assert body == _BODY[2:22]
    assert "showing 3–22" in out.err  # 1-based span in the note


def test_text_json_reports_total(store, tmp_path, capsys):
    def go():
        async def _():
            md = tmp_path / "paper.md"
            md.write_text(_BODY)
            r = await store.add({"title": "ASO Knockdown Study", "year": 2021,
                                 "doi": "10.9/aso3"}, file_path=md)
            ck = r["record"]["citekey"]
            await bib.cmd_text(_args(ck, chars=10, json=True), store)
            return ck
        return asyncio.run(_())

    import json as _json
    go()
    payload = _json.loads(capsys.readouterr().out)
    assert payload["mode"] == "fulltext"
    assert payload["content_chars"] == 10
    assert payload["content_total"] == len(_BODY)
    assert payload["text"] == _BODY[:10]


def test_text_stub_falls_back_to_abstract(store, capsys):
    def go():
        async def _():
            r = await store.add({"title": "Citation Only Paper", "year": 2015,
                                 "doi": "10.9/stub",
                                 "abstract": "Only an abstract is stored here."})
            assert r["record"]["content_state"] == "stub"
            ck = r["record"]["citekey"]
            await bib.cmd_text(_args(ck), store)
            return ck
        return asyncio.run(_())

    go()
    out = capsys.readouterr()
    assert "Only an abstract is stored here." in out.out
    assert "citation-only stub" in out.err


def test_text_unknown_citekey_dies(store, capsys):
    with pytest.raises(SystemExit):
        _run(store, lambda: bib.cmd_text(_args("nope2099none"), store))
