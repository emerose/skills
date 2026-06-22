"""Bug 2: one id's resolve error must not abort a whole ``bib add`` batch.

The original failure was that ``resolve_target`` turned a ``ResolveError`` into
``die()`` -> ``SystemExit`` (a ``BaseException``), which ``cmd_add``'s
``except Exception`` did not catch — so a single S2 429 aborted the remaining
ids. These tests pin both the root cause (``resolve_target`` raises
``ResolveError``, not ``SystemExit``) and the batch behaviour (skip + continue),
with the store and ingest faked so no libkit/network is needed.
"""

import asyncio
from types import SimpleNamespace

import pytest

import bib
from bibliographer import resolvers as R


def _args(identifiers, **over):
    base = dict(
        identifiers=identifiers, pdf=None, tags=None, no_network=False,
        force=False, json=False, move=False, no_fetch=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeStore:
    async def find_duplicate(self, rec):
        return None


def test_resolve_target_raises_resolve_error_not_systemexit(monkeypatch):
    """The root cause: a failed identifier resolve raises ResolveError (catchable),
    never SystemExit (which slipped past the batch's ``except Exception``)."""
    async def boom(value, client=None):
        raise R.ResolveError(f"Semantic Scholar 429 for {value}")

    monkeypatch.setattr(R, "resolve", boom)
    with pytest.raises(R.ResolveError):
        asyncio.run(bib.resolve_target("PMID:8627575"))
    # And it is NOT a SystemExit (the regression).
    try:
        asyncio.run(bib.resolve_target("PMID:8627575"))
    except SystemExit:  # pragma: no cover
        pytest.fail("resolve_target raised SystemExit — batch would abort")
    except R.ResolveError:
        pass


def test_no_network_identifier_raises_resolve_error(monkeypatch):
    with pytest.raises(R.ResolveError):
        asyncio.run(bib.resolve_target("10.1/x", no_network=True))


def test_batch_add_skips_failed_id_and_banks_the_rest(monkeypatch, capsys):
    """A batch mixing a throttled id with good ones banks the good ones and
    reports the bad one, instead of aborting."""
    async def fake_resolve_target(ident, *, pdf_override=None, no_network=False, client=None):
        if ident == "PMID:bad":
            raise R.ResolveError("Semantic Scholar 429 for PMID:bad")
        return {"title": f"Paper {ident}", "source": "pubmed"}, None

    ingested = []

    async def fake_ingest(store, rec, **kw):
        ingested.append(rec["title"])
        return {"status": "added", "record": {**rec, "citekey": rec["title"].lower()}}

    async def fake_write_index(store):
        return None

    monkeypatch.setattr(bib, "resolve_target", fake_resolve_target)
    monkeypatch.setattr(bib, "ingest_record", fake_ingest)
    monkeypatch.setattr(bib, "write_index", fake_write_index)

    args = _args(["PMID:good1", "PMID:bad", "PMID:good2"])
    asyncio.run(bib.cmd_add(args, _FakeStore()))

    # Both good ids banked despite the bad one in the middle.
    assert ingested == ["Paper PMID:good1", "Paper PMID:good2"]
    out = capsys.readouterr()
    assert "1 failed" in out.out
    assert "PMID:bad" in out.err  # the per-id error line


def test_single_bad_id_is_a_clean_error_not_a_traceback(monkeypatch):
    """A single failing add exits cleanly via die() (SystemExit), not a raw
    ResolveError traceback bubbling out of asyncio.run."""
    async def fake_resolve_target(ident, *, pdf_override=None, no_network=False, client=None):
        raise R.ResolveError("nope")

    monkeypatch.setattr(bib, "resolve_target", fake_resolve_target)
    with pytest.raises(SystemExit):
        asyncio.run(bib.cmd_add(_args(["PMID:bad"]), _FakeStore()))
