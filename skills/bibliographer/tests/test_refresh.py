"""Tests for the citation-metrics ``as_of`` stamp and the `bib refresh` command.

The pure staleness helper and the ``enrich_openalex`` stamping/overwrite logic
run offline (the OpenAlex fetchers are monkeypatched). The command test reuses
the fake-embedder store from test_store.py with a monkeypatched enricher, so it
needs libkit but no network.
"""

import asyncio
import hashlib
import struct
import types
from datetime import datetime, timedelta, timezone

import pytest

import bib


def _iso_days_ago(n: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=n)).isoformat()


# --------------------------------------------------------------------------- #
# _metrics_stale (pure)
# --------------------------------------------------------------------------- #
def test_metrics_stale_missing_metrics_is_not_stale():
    # No metrics at all is a *backfill*, handled separately — not "stale".
    assert bib._metrics_stale({}, 30) is False
    assert bib._metrics_stale({"metrics": {}}, 30) is False


def test_metrics_without_as_of_counts_as_stale():
    assert bib._metrics_stale({"metrics": {"fwci": 1.0}}, 30) is True


def test_metrics_fresh_vs_old():
    fresh = {"metrics": {"as_of": _iso_days_ago(5)}}
    old = {"metrics": {"as_of": _iso_days_ago(400)}}
    assert bib._metrics_stale(fresh, 30) is False
    assert bib._metrics_stale(old, 30) is True


def test_metrics_unparseable_as_of_counts_as_stale():
    assert bib._metrics_stale({"metrics": {"as_of": "garbage"}}, 30) is True


# --------------------------------------------------------------------------- #
# enrich_openalex — as_of stamp + cited_by_count overwrite-on-refresh
# --------------------------------------------------------------------------- #
def _patch_openalex(monkeypatch, work):
    from bibliographer import resolvers as R

    async def _work(client, *, doi=None, pmid=None, refresh=False):
        return work

    async def _source(source_id, client, *, refresh=False):
        return None

    monkeypatch.setattr(R, "fetch_openalex_work", _work)
    monkeypatch.setattr(R, "fetch_openalex_source", _source)


def test_enrich_stamps_as_of(monkeypatch):
    from bibliographer import resolvers as R

    _patch_openalex(monkeypatch, {"id": "https://openalex.org/W1", "fwci": 1.5,
                                  "cited_by_count": 100, "type": "article"})
    rec = {"doi": "10.1/x"}
    ok = asyncio.run(R.enrich_openalex(rec, object()))
    assert ok is True
    assert rec["metrics"]["as_of"] == datetime.now(timezone.utc).date().isoformat()
    assert rec["cited_by_count"] == 100        # backfilled when absent


def test_refresh_overwrites_existing_cited_by_count(monkeypatch):
    from bibliographer import resolvers as R

    _patch_openalex(monkeypatch, {"id": "https://openalex.org/W1",
                                  "cited_by_count": 100, "type": "article"})
    rec = {"doi": "10.1/x", "cited_by_count": 5}
    # Without refresh, an existing count is preserved...
    asyncio.run(R.enrich_openalex(rec, object()))
    assert rec["cited_by_count"] == 5
    # ...with refresh, OpenAlex wins (it's the canonical source we're refreshing).
    asyncio.run(R.enrich_openalex(rec, object(), refresh=True))
    assert rec["cited_by_count"] == 100


# --------------------------------------------------------------------------- #
# command — needs libkit (fake embedder, no network)
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


def _args(**kw):
    base = dict(tag=None, limit=500, dry_run=False, json=True, all=False, stale=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_refresh_backfills_missing_metrics(store, monkeypatch, capsys):
    """A record with a DOI but no metrics gets a stamped metrics block written."""
    from bibliographer import resolvers as _resolvers

    async def _enrich(rec, client, *, refresh=False):
        rec["metrics"] = {"source": "openalex", "fwci": 1.2, "as_of": "2026-06-19"}
        rec["cited_by_count"] = 42
        return True

    monkeypatch.setattr(_resolvers, "enrich_openalex", _enrich)
    monkeypatch.setattr(bib, "write_index", lambda store: asyncio.sleep(0))

    def go():
        async def inner():
            await store.add({"title": "Counted", "year": 2024, "doi": "10.9/x",
                             "authors": [{"family": "Roe", "given": "R"}]})
            await bib.cmd_refresh(_args(), store)
            return await store.get_by_citekey("roe2024counted")
        return inner()

    rec = _run(store, go)
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["checked"] == 1
    assert payload["updated"][0]["citekey"] == "roe2024counted"
    assert payload["updated"][0]["cited_by_count"] == 42
    # persisted on the record
    assert rec["cited_by_count"] == 42
    assert rec["metrics"]["as_of"] == "2026-06-19"


def test_refresh_reports_ineligible_and_skips_present(store, monkeypatch, capsys):
    """No-DOI/PMID records are ineligible; records that already have metrics are
    left alone by the default (backfill-only) scope."""
    from bibliographer import resolvers as _resolvers

    async def _enrich(rec, client, *, refresh=False):  # should not be called
        raise AssertionError("enrich must not run in default scope here")

    monkeypatch.setattr(_resolvers, "enrich_openalex", _enrich)

    def go():
        async def inner():
            await store.add({"title": "No Ids", "year": 2024,
                             "authors": [{"family": "Doe", "given": "D"}]})
            await store.add({"title": "Has Metrics", "year": 2023, "doi": "10.1/y",
                             "metrics": {"source": "openalex", "as_of": "2026-01-01"},
                             "authors": [{"family": "Lee", "given": "L"}]})
            await bib.cmd_refresh(_args(), store)
        return inner()

    _run(store, go)
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["checked"] == 0
    assert payload["ineligible"] == 1
