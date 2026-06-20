"""Offline tests for the OpenAlex citation-graph normalizers and the `bib refs`/
`bib gaps`/`bib cluster` commands.

The pure normalizers (`_openalex_refs`, `_openalex_label`) and `enrich_references`
run offline (the OpenAlex fetcher is monkeypatched). The command tests reuse the
fake-embedder store pattern from test_refresh.py — libkit, no network.
"""

import asyncio
import hashlib
import struct
import types
from datetime import datetime, timezone

import pytest

import bib
from bibliographer import resolvers as R


# --------------------------------------------------------------------------- #
# pure normalizers
# --------------------------------------------------------------------------- #
def test_openalex_refs_strips_url_prefixes():
    work = {
        "id": "https://openalex.org/W42",
        "referenced_works": [
            "https://openalex.org/W1", "https://openalex.org/W2", "",
        ],
    }
    out = R._openalex_refs(work)
    assert out["openalex_id"] == "W42"
    assert out["references"] == ["W1", "W2"]               # empties dropped


def test_openalex_refs_handles_no_references():
    out = R._openalex_refs({"id": "https://openalex.org/W42"})
    assert out == {"openalex_id": "W42", "references": []}


def test_openalex_label_compacts_work():
    work = {
        "id": "https://openalex.org/W7",
        "display_name": "A Title",
        "publication_year": 2021,
        "cited_by_count": 99,
        "doi": "https://doi.org/10.1/x",
    }
    lbl = R._openalex_label(work)
    assert lbl == {"work_id": "W7", "title": "A Title", "year": 2021,
                   "cited_by_count": 99, "doi": "10.1/x"}


# --------------------------------------------------------------------------- #
# enrich_references — stamps references + as_of, backfills openalex_id
# --------------------------------------------------------------------------- #
def test_enrich_references_stamps(monkeypatch):
    async def _fetch(client, *, openalex_id=None, doi=None, pmid=None, refresh=False):
        return {"openalex_id": "W42", "references": ["W1", "W2"]}

    monkeypatch.setattr(R, "fetch_openalex_references", _fetch)
    rec = {"doi": "10.1/x"}
    ok = asyncio.run(R.enrich_references(rec, object()))
    assert ok is True
    assert rec["references"] == ["W1", "W2"]
    assert rec["openalex_id"] == "W42"                    # backfilled from the work
    assert rec["references_as_of"] == datetime.now(timezone.utc).date().isoformat()


def test_enrich_references_empty_list_is_success(monkeypatch):
    async def _fetch(client, **kw):
        return {"openalex_id": "W42", "references": []}

    monkeypatch.setattr(R, "fetch_openalex_references", _fetch)
    rec = {"doi": "10.1/x"}
    assert asyncio.run(R.enrich_references(rec, object())) is True
    assert rec["references"] == []                         # still stamped, not retried forever


def test_enrich_references_no_match(monkeypatch):
    async def _fetch(client, **kw):
        return None

    monkeypatch.setattr(R, "fetch_openalex_references", _fetch)
    rec = {"doi": "10.1/x"}
    assert asyncio.run(R.enrich_references(rec, object())) is False
    assert "references" not in rec


# --------------------------------------------------------------------------- #
# commands — need libkit (fake embedder, no network)
# --------------------------------------------------------------------------- #
libkit = pytest.importorskip("libkit")

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


def _refs_args(**kw):
    base = dict(citekeys=[], tag=None, limit=500, dry_run=False, json=True, all=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_update_references_persists_and_fills_openalex_id(store):
    def go():
        async def inner():
            await store.add({"title": "Base", "year": 2024, "doi": "10.1/a",
                             "authors": [{"family": "Ng", "given": "N"}]})
            await store.update_references("ng2024base", ["W1", "W2"], "2026-06-20", "W42")
            return await store.get_by_citekey("ng2024base")
        return inner()

    rec = _run(store, go)
    assert rec["references"] == ["W1", "W2"]
    assert rec["references_as_of"] == "2026-06-20"
    assert rec["openalex_id"] == "W42"


def test_cmd_refs_backfills(store, monkeypatch, capsys):
    async def _enrich(rec, client, *, refresh=False):
        rec["references"] = ["W1", "W2", "W3"]
        rec["references_as_of"] = "2026-06-20"
        rec["openalex_id"] = "W42"
        return True

    monkeypatch.setattr(R, "enrich_references", _enrich)

    def go():
        async def inner():
            await store.add({"title": "Cite Me", "year": 2024, "doi": "10.1/c",
                             "authors": [{"family": "Lo", "given": "L"}]})
            await bib.cmd_refs(_refs_args(), store)
            return await store.get_by_citekey("lo2024cite")
        return inner()

    rec = _run(store, go)
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["checked"] == 1
    assert payload["updated"][0] == {"citekey": "lo2024cite", "references": 3}
    assert rec["references"] == ["W1", "W2", "W3"]
    assert rec["openalex_id"] == "W42"


def test_cmd_refs_reports_ineligible(store, monkeypatch, capsys):
    async def _enrich(rec, client, *, refresh=False):  # should not be called
        raise AssertionError("no eligible record should be enriched")

    monkeypatch.setattr(R, "enrich_references", _enrich)

    def go():
        async def inner():
            await store.add({"title": "No Ids", "year": 2024,
                             "authors": [{"family": "Doe", "given": "D"}]})
            await bib.cmd_refs(_refs_args(), store)
        return inner()

    _run(store, go)
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["checked"] == 0
    assert payload["ineligible"] == 1


def _gaps_args(**kw):
    base = dict(tag=None, min_citing=2, limit=30, no_network=True, json=True)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_cmd_gaps_offline(store, capsys):
    def go():
        async def inner():
            await store.add({"title": "P1", "year": 2024, "doi": "10.1/1",
                             "references": ["W9", "W8"],
                             "authors": [{"family": "A", "given": "A"}]})
            await store.add({"title": "P2", "year": 2024, "doi": "10.1/2",
                             "references": ["W9"],
                             "authors": [{"family": "B", "given": "B"}]})
            await bib.cmd_gaps(_gaps_args(), store)
        return inner()

    _run(store, go)
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidates"][0]["work_id"] == "W9"
    assert payload["candidates"][0]["citing_count"] == 2


def test_cmd_gaps_no_reference_data(store, capsys):
    def go():
        async def inner():
            await store.add({"title": "P1", "year": 2024, "doi": "10.1/1",
                             "authors": [{"family": "A", "given": "A"}]})
            await bib.cmd_gaps(_gaps_args(), store)
        return inner()

    _run(store, go)
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidates"] == []
    assert "bib refs" in payload["note"]


def _cluster_args(**kw):
    base = dict(tag=None, min_shared=2, write_tags=False, json=True)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_cmd_cluster_write_tags(store, monkeypatch, capsys):
    monkeypatch.setattr(bib, "write_index", lambda store: asyncio.sleep(0))

    def go():
        async def inner():
            await store.add({"title": "P1", "year": 2024, "doi": "10.1/1",
                             "references": ["W1", "W2"],
                             "authors": [{"family": "A", "given": "A"}]})
            await store.add({"title": "P2", "year": 2024, "doi": "10.1/2",
                             "references": ["W1", "W2"],
                             "authors": [{"family": "B", "given": "B"}]})
            await bib.cmd_cluster(_cluster_args(write_tags=True), store)
            return [await store.get_by_citekey("a2024p1"),
                    await store.get_by_citekey("b2024p2")]
        return inner()

    recs = _run(store, go)
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["clusters"][0]["size"] == 2
    assert payload["tags_written"] == 2
    for rec in recs:
        assert "cluster:1" in (rec.get("tags") or [])


def _outliers_args(**kw):
    base = dict(tag=None, min_shared=2, all=False, json=True)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_cmd_outliers_flags_isolated(store, capsys):
    def go():
        async def inner():
            await store.add({"title": "P1", "year": 2024, "doi": "10.1/1",
                             "openalex_id": "WA", "references": ["W1", "W2"],
                             "authors": [{"family": "A", "given": "A"}]})
            await store.add({"title": "P2", "year": 2024, "doi": "10.1/2",
                             "openalex_id": "WB", "references": ["W1", "W2"],
                             "authors": [{"family": "B", "given": "B"}]})
            await store.add({"title": "Lone", "year": 2024, "doi": "10.1/3",
                             "openalex_id": "WL", "references": ["W500", "W501"],
                             "authors": [{"family": "C", "given": "C"}]})
            await bib.cmd_outliers(_outliers_args(), store)
        return inner()

    _run(store, go)
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["checked"] == 3
    assert [e["citekey"] for e in payload["isolated"]] == ["c2024lone"]
