"""Integration tests for Store against real libkit, using a fake embedder
+ Markdown loader + trivial chunker — so no model download and no API keys.

Skipped automatically if libkit isn't installed. Needs network the first time
DuckDB fetches its vss/fts extensions (cached thereafter).
"""

import asyncio
import hashlib
import struct

import pytest

libkit = pytest.importorskip("libkit")

from store._store import Store  # noqa: E402
from store import _meta  # noqa: E402
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
    (tmp_path / ".scientist").mkdir()
    cfg = LibraryConfig(
        db_path=tmp_path / ".scientist" / "catalog.duckdb",
        embedder=_FakeEmbedder(),
        chunker=_FakeChunker(),
        loaders={".md": MarkdownLoader(), ".txt": MarkdownLoader()},
        cache_enabled=False,
    )
    return Store(tmp_path, Library(cfg))


def _run(store, coro_factory):
    async def main():
        try:
            return await coro_factory()
        finally:
            await store.close()
    return asyncio.run(main())


def test_upsert_experiment_keyed_by_exp_id(store):
    async def go():
        r = await store.upsert_experiment({"exp_id": "K1-000000", "name": "Dose-Response",
                                           "cro_study_ids": ["V1234567"]})
        assert r["kind"] == "experiment"
        assert r["exp_id"] == "K1-000000"
        added = r["added_at"]
        # update: new content -> new document_id, but still one experiment record
        r2 = await store.upsert_experiment({"exp_id": "K1-000000", "name": "Dose-Response",
                                            "cro_study_ids": ["V1234567"], "status": "complete"})
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
        await store.upsert_entity({"entity_id": "aso-7", "title": "ASO 7",
                                   "note": "Lead candidate selected from the 10-ASO rat IT screen."})
        e = await store.get_entity("aso-7")
        assert e is not None and "Lead candidate" in e["note"]
    _run(store, go)


# --------------------------------------------------------------------------- #
# claims (kind=claim) — index grounded claims so semantic search returns evidence
# --------------------------------------------------------------------------- #
import json  # noqa: E402


def _claim_rec(exp_id, nodeid, *, statement, outcome, strength, kind, caveats=None,
               evidence=None, inputs=None):
    """Mirror cli.cmd_index_claims's per-claim record shape from a report entry."""
    return {
        "exp_id": exp_id,
        "claim_id": _meta.claim_id_for(exp_id, nodeid),
        "statement": statement,
        "outcome": outcome,
        "strength": strength,
        "claim_kind": kind,
        "caveats": caveats,
        "evidence_json": json.dumps(evidence or {}, sort_keys=True),
        "inputs": [{"path": i["path"], "sha256": i["sha256"]} for i in (inputs or [])],
        "source": nodeid,
    }


# A synthetic grounding_report.json: one strong/passed result, one weak/xfail
# (contradicted), one moderate skipped.
_GROUNDED = "/abs/K1-000000 - Dose/analysis/claims/test_kd.py::test_pos_ctrl_strong"
_CONTRA = "/abs/K1-000000 - Dose/analysis/claims/test_kd.py::test_high_dose_xfail"
_SKIP = "/abs/K1-000000 - Dose/analysis/claims/test_kd.py::test_unverifiable_skip"


def _index_synthetic(store, exp_id="K1-000000"):
    return [
        _claim_rec(exp_id, _GROUNDED,
                   statement="Lumbar knockdown reached 78% at the 100 nM top dose.",
                   outcome="passed", strength="strong", kind="result",
                   evidence={"kd_pct": 78.0, "criterion_pct": 60},
                   inputs=[{"path": "K1-000000/data/02_assay.csv", "sha256": "deadbeef"}]),
        _claim_rec(exp_id, _CONTRA,
                   statement="The high-dose group showed dose-dependent gait deficits.",
                   outcome="xfail", strength="weak", kind="result",
                   caveats="single series; n=2 wells at the top dose",
                   evidence={"observed": "no effect"}),
        _claim_rec(exp_id, _SKIP,
                   statement="Off-target liver expression cannot be assessed from this data.",
                   outcome="skipped", strength="unverifiable", kind="interpretive"),
    ]


def test_upsert_claim_keyed_and_metadata_roundtrips(store):
    async def go():
        for rec in _index_synthetic(store):
            await store.upsert_claim(rec)
        claims = await store.claims("K1-000000")
        assert len(claims) == 3
        by_stmt = {c["statement"]: c for c in claims}
        strong = by_stmt["Lumbar knockdown reached 78% at the 100 nM top dose."]
        assert strong["kind"] == "claim"
        assert strong["outcome"] == "passed" and strong["strength"] == "strong"
        assert strong["exp_id"] == "K1-000000"
        assert strong["claim_id"] == "K1-000000::test_kd.py::test_pos_ctrl_strong"
        assert strong["claim_kind"] == "result"
        assert json.loads(strong["evidence_json"])["kd_pct"] == 78.0
        assert strong["inputs"][0]["sha256"] == "deadbeef"
        # the contradicted claim carries its honest xfail/weak status
        contra = by_stmt["The high-dose group showed dose-dependent gait deficits."]
        assert contra["outcome"] == "xfail" and contra["strength"] == "weak"
        assert contra["caveats"]
    _run(store, go)


def test_claim_stable_id_idempotent_across_cwd(store):
    """The stable claim_id strips the invocation path, so re-indexing the same claim
    from a different absolute path collapses to one record (no duplicate)."""
    async def go():
        rec = _claim_rec("K1-000000",
                         "/abs/A/K1-000000 - Dose/analysis/claims/test_kd.py::test_x",
                         statement="A claim.", outcome="passed", strength="moderate", kind="result")
        await store.upsert_claim(rec)
        # same claim, different invocation path -> same claim_id
        rec2 = _claim_rec("K1-000000",
                          "/somewhere/else/K1-000000 - Dose/analysis/claims/test_kd.py::test_x",
                          statement="A claim.", outcome="passed", strength="moderate", kind="result")
        assert rec2["claim_id"] == rec["claim_id"]
        await store.upsert_claim(rec2)
        assert len(await store.claims("K1-000000")) == 1
    _run(store, go)


def test_claim_queryable_by_statement_with_kind_filter(store):
    async def go():
        for rec in _index_synthetic(store):
            await store.upsert_claim(rec)
        hits = await store.query("lumbar knockdown top dose", limit=8,
                                 filters={"kind": "claim"})
        assert hits, "claim should be retrievable via kind=claim query"
        assert all((h.chunk.metadata or {}).get("kind") == "claim" for h in hits)
        assert any("knockdown" in h.chunk.text.lower() for h in hits)
        # the matched claim's metadata carries its honest judgment
        top = hits[0].chunk.metadata
        assert top.get("outcome") and top.get("strength")
    _run(store, go)


def test_replace_experiment_claims_prunes_removed(store):
    async def go():
        recs = _index_synthetic(store)
        for rec in recs:
            await store.upsert_claim(rec)
        all_ids = [r["claim_id"] for r in recs]
        # full set kept -> nothing pruned
        assert await store.replace_experiment_claims("K1-000000", all_ids) == 0
        assert len(await store.claims("K1-000000")) == 3
        # drop the contradicted claim from the report -> it's pruned
        kept = [r["claim_id"] for r in recs if r["claim_id"] != _meta.claim_id_for("K1-000000", _CONTRA)]
        pruned = await store.replace_experiment_claims("K1-000000", kept)
        assert pruned == 1
        remaining = await store.claims("K1-000000")
        assert len(remaining) == 2
        assert all(c["claim_id"] != _meta.claim_id_for("K1-000000", _CONTRA) for c in remaining)
    _run(store, go)


def test_replace_experiment_claims_scoped_to_exp(store):
    """Pruning one experiment's claims never touches another experiment's claims."""
    async def go():
        await store.upsert_claim(_claim_rec("K1-000000", "/x/K1-000000 - A/analysis/claims/t.py::a",
                                            statement="exp0 claim", outcome="passed",
                                            strength="strong", kind="result"))
        await store.upsert_claim(_claim_rec("K1-111111", "/x/K1-111111 - B/analysis/claims/t.py::b",
                                            statement="exp1 claim", outcome="passed",
                                            strength="strong", kind="result"))
        # prune everything from K1-000000; K1-111111 untouched
        assert await store.replace_experiment_claims("K1-000000", []) == 1
        assert len(await store.claims("K1-000000")) == 0
        assert len(await store.claims("K1-111111")) == 1
    _run(store, go)
