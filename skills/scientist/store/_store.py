"""The libkit-backed store for archivist.

libkit is the single store: there is no separate archivist database. Each
experiment, file, and curated-entity note is one libkit *document*; all archivist
fields live in the document's free-form ``metadata`` JSON (see :mod:`_meta`).
This module wraps ``libkit.Library`` with the operations libkit deliberately does
not provide — logical identity keyed by ``exp_id`` / file ``path``, experiment
card upserts, and kind-scoped listing.

Two libkit facts shape everything:

* ``document_id`` is the SHA-256 of the ingested bytes, so re-ingesting the same
  file (or the same deterministic card) is a no-op (``already_existed=True``).
* ``update_metadata(metadata=…)`` REPLACES the JSON wholesale, so each mutation
  is a read-modify-write (see :meth:`_merge_metadata`).
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from provenance import env_first

from . import _meta

STORE_DIRNAME = ".scientist"
LEGACY_STORE_DIRNAME = ".archivist"
DB_FILENAME = "catalog.duckdb"


def resolve_store_dirname(home: Path) -> str:
    """The store dir name to use under ``home``. New stores use ``.scientist/``,
    but an existing legacy ``.archivist/`` store is reused **in place** — the
    rename is forward-looking, not a re-embed (reindexing an existing corpus would
    be needless cost; the store can be large). Prefer ``.scientist/`` if both exist."""
    if (home / STORE_DIRNAME).exists():
        return STORE_DIRNAME
    if (home / LEGACY_STORE_DIRNAME).exists():
        return LEGACY_STORE_DIRNAME
    return STORE_DIRNAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EmbedderConfigError(RuntimeError):
    """The configured embedder doesn't match how the library was built."""


class ArchivistStore:
    """Async wrapper over a libkit ``Library`` scoped to one data folder.

    ``home`` is the managed scientific-data folder; the libkit store lives at
    ``home/.scientist/catalog.duckdb`` (gitignored). All file paths in metadata
    are stored relative to ``home`` so the catalog is portable.
    """

    def __init__(self, home: Path, lib: Any) -> None:
        self.home = home
        self.lib = lib
        self._store_dirname = resolve_store_dirname(home)

    # ---- lifecycle ----------------------------------------------------------
    @classmethod
    def open(
        cls,
        home: Path,
        *,
        embedding: str | None = None,
        model: str | None = None,
    ) -> "ArchivistStore":
        """Open (creating if needed) the libkit library under ``home/.scientist``.

        ``embedding``/``model`` must stay consistent across runs — libkit fixes
        the store's vector dimension from the embedder at creation and refuses to
        reopen with a different one. Defaults come from ``SCIENTIST_EMBEDDING``
        (fallback ``ARCHIVIST_EMBEDDING``; default ``remote`` — DeepInfra, no local
        model download) and ``SCIENTIST_EMBED_MODEL`` (fallback
        ``ARCHIVIST_EMBED_MODEL``; default ``qwen3_600m``, dim 1024).
        """
        from libkit import Library
        from libkit.errors import EmbedderMismatch

        store_dir = home / resolve_store_dirname(home)
        store_dir.mkdir(parents=True, exist_ok=True)
        embedding = embedding or env_first("SCIENTIST_EMBEDDING", "ARCHIVIST_EMBEDDING",
                                           default="remote")
        model = model or env_first("SCIENTIST_EMBED_MODEL", "ARCHIVIST_EMBED_MODEL",
                                   default="qwen3_600m")
        allow_mismatch = (env_first("SCIENTIST_ALLOW_EMBEDDER_MISMATCH",
                                    "ARCHIVIST_ALLOW_EMBEDDER_MISMATCH", default="").lower()
                          in ("1", "true", "yes"))
        try:
            lib = Library.open(
                store_dir / DB_FILENAME,
                embedding=embedding,
                model=model,
                allow_embedder_mismatch=allow_mismatch,
            )
        except EmbedderMismatch as e:
            raise EmbedderConfigError(
                "this library was built with a different embedding backend than the "
                "one configured now:\n"
                f"  stored : {e.observed}\n"
                f"  current: {e.expected}\n"
                "Set SCIENTIST_EMBEDDING / SCIENTIST_EMBED_MODEL to match how the "
                "library was created, or set SCIENTIST_ALLOW_EMBEDDER_MISMATCH=1 to "
                "override (only if you know the two are vector-compatible)."
            ) from e
        return cls(home, lib)

    async def close(self) -> None:
        await self.lib.close()

    # ---- reads --------------------------------------------------------------
    async def all_records(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        docs = await self.lib.list_documents(filters=filters)
        return [_meta.document_to_record(d) for d in docs]

    async def experiments(self) -> list[dict[str, Any]]:
        return await self.all_records({"kind": "experiment"})

    async def files(self, exp_id: str | None = None) -> list[dict[str, Any]]:
        filters: dict[str, Any] = {"kind": "file"}
        if exp_id:
            filters["exp_id"] = exp_id
        return await self.all_records(filters)

    async def get_experiment(self, exp_id: str) -> dict[str, Any] | None:
        docs = await self.lib.list_documents(filters={"kind": "experiment", "exp_id": exp_id})
        return _meta.document_to_record(docs[0]) if docs else None

    async def get_file(self, path: str) -> dict[str, Any] | None:
        """Find a file record by its primary ``path`` or any of its ``other_paths``
        (a byte-identical duplicate copy filed elsewhere)."""
        docs = await self.lib.list_documents(filters={"kind": "file", "path": path})
        if not docs:
            docs = await self.lib.list_documents(filters={"kind": "file", "other_paths": path})
        return _meta.document_to_record(docs[0]) if docs else None

    async def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        docs = await self.lib.list_documents(filters={"kind": "entity", "entity_id": entity_id})
        return _meta.document_to_record(docs[0]) if docs else None

    async def get_claim(self, claim_id: str) -> dict[str, Any] | None:
        docs = await self.lib.list_documents(filters={"kind": "claim", "claim_id": claim_id})
        return _meta.document_to_record(docs[0]) if docs else None

    async def claims(self, exp_id: str | None = None) -> list[dict[str, Any]]:
        filters: dict[str, Any] = {"kind": "claim"}
        if exp_id:
            filters["exp_id"] = exp_id
        return await self.all_records(filters)

    async def query(self, text: str, *, limit: int = 8,
                    filters: dict[str, Any] | None = None) -> list[Any]:
        """Semantic + full-text search inside the indexed content (libkit hybrid)."""
        return await self.lib.query(text, limit=limit, filters=filters)

    async def leading_text(self, document_id: str, chunks: int = 3) -> str:
        from libkit.errors import ChunkNotFound

        parts = []
        for i in range(chunks):
            try:
                parts.append((await self.lib.get_chunk(document_id, i)).text)
            except ChunkNotFound:
                break
        return " ".join(parts)

    # ---- writes -------------------------------------------------------------
    async def upsert_experiment(self, rec: dict[str, Any]) -> dict[str, Any]:
        """Create or update an experiment card, keyed by ``exp_id``.

        The card's bytes are derived from its metadata, so any change yields a new
        ``document_id``. We ingest the new card and delete the prior one (if its id
        differs), preserving the logical ``exp_id`` identity and ``added_at``.
        """
        exp_id = rec.get("exp_id")
        if not exp_id:
            raise ValueError("experiment record needs an exp_id")
        rec = dict(rec)
        rec["kind"] = "experiment"
        existing = await self.get_experiment(exp_id)
        rec["added_at"] = (existing or {}).get("added_at") or rec.get("added_at") or _now_iso()
        rec["updated_at"] = _now_iso()

        result = await self._ingest_card(_meta.experiment_card_markdown(rec), rec)
        if existing and existing.get("document_id") and existing["document_id"] != result.document_id:
            await self.lib.delete(existing["document_id"])
        return await self._record_for_id(result.document_id)

    async def add_file(
        self,
        rec: dict[str, Any],
        *,
        ingest_path: Path | None = None,
        card_markdown: str | None = None,
    ) -> dict[str, Any]:
        """Add (or refresh) a file record, keyed by its relative ``path``.

        Exactly one of ``ingest_path`` (ingest the real narrative file so its text
        is embedded) or ``card_markdown`` (ingest a generated schema/descriptor
        card for tabular/binary files) must be given. If a record for the same
        ``path`` exists with a different ``document_id`` (the file or card changed),
        the old document is replaced.
        """
        if (ingest_path is None) == (card_markdown is None):
            raise ValueError("pass exactly one of ingest_path or card_markdown")
        rec = dict(rec)
        rec["kind"] = "file"
        existing = await self.get_file(rec.get("path", ""))
        rec["added_at"] = (existing or {}).get("added_at") or _now_iso()
        rec["updated_at"] = _now_iso()

        if ingest_path is not None:
            result = await self.lib.ingest(ingest_path, metadata=_meta.record_to_metadata(rec))
        else:
            assert card_markdown is not None  # guaranteed by the xor check above
            result = await self._ingest_card(card_markdown, rec)

        # Reconcile whatever record this path previously pointed at, if its content
        # changed (different document_id now).
        if existing and existing.get("document_id") and existing["document_id"] != result.document_id:
            if existing.get("path") == rec["path"]:
                await self.lib.delete(existing["document_id"])      # primary path, new content
            else:
                await self._set_other_paths(existing["document_id"], drop=rec["path"])  # was a dup

        if result.already_existed:
            # Byte-identical content is already stored under some path. libkit keys
            # documents by content hash, so we DON'T re-ingest; instead we make sure
            # this path is represented. If it's the primary path, refresh metadata;
            # if it's a new location of the same bytes (a duplicate copy filed
            # elsewhere), record it in other_paths rather than clobbering the record.
            doc = await self.lib.get_document(result.document_id)
            meta = dict(doc.metadata or {})
            primary = meta.get("path")
            if rec["path"] == primary:
                await self._merge_metadata(result.document_id, _meta.record_to_metadata(rec))
            elif rec["path"] not in (meta.get("other_paths") or []):
                await self._set_other_paths(result.document_id, add=rec["path"])
        return await self._record_for_id(result.document_id)

    async def _set_other_paths(self, document_id: str, *, add: str | None = None,
                               drop: str | None = None) -> None:
        """Add/remove a duplicate-copy path on a file record's ``other_paths`` list
        (the primary ``path`` never appears in it)."""
        doc = await self.lib.get_document(document_id)
        meta = dict(doc.metadata or {})
        others = set(meta.get("other_paths") or [])
        if add:
            others.add(add)
        if drop:
            others.discard(drop)
        others.discard(meta.get("path"))
        await self._merge_metadata(document_id,
                                   {"other_paths": sorted(others), "updated_at": _now_iso()})

    async def upsert_entity(self, rec: dict[str, Any]) -> dict[str, Any]:
        """Store a *curated* entity note, keyed by ``entity_id``.

        Only call this for entities carrying non-derivable text (aliases, an ASO's
        selection rationale, a CRO's quirks). Purely-derivable entity facts should
        be answered by a live filter query, not stored here.
        """
        entity_id = rec.get("entity_id")
        if not entity_id:
            raise ValueError("entity record needs an entity_id")
        rec = dict(rec)
        rec["kind"] = "entity"
        existing = await self.get_entity(entity_id)
        rec["added_at"] = (existing or {}).get("added_at") or _now_iso()
        rec["updated_at"] = _now_iso()
        body = rec.get("note") or rec.get("title") or entity_id
        md = f"# {rec.get('title') or entity_id}\n\n{body}\n"
        result = await self._ingest_card(md, rec)
        if existing and existing.get("document_id") and existing["document_id"] != result.document_id:
            await self.lib.delete(existing["document_id"])
        return await self._record_for_id(result.document_id)

    async def upsert_claim(self, rec: dict[str, Any]) -> dict[str, Any]:
        """Create or update a grounded-claim card, keyed by a stable ``claim_id``.

        Mirrors :meth:`upsert_experiment`: the card's bytes derive from the claim's
        content (statement/outcome/strength/evidence), so any change yields a new
        ``document_id``; we ingest the new card and delete the prior one (if its id
        differs), preserving the logical ``claim_id`` identity and ``added_at``.
        """
        claim_id = rec.get("claim_id")
        if not claim_id:
            raise ValueError("claim record needs a claim_id")
        exp_id = rec.get("exp_id")
        if not exp_id:
            raise ValueError("claim record needs an exp_id")
        rec = dict(rec)
        rec["kind"] = "claim"
        existing = await self.get_claim(claim_id)
        rec["added_at"] = (existing or {}).get("added_at") or rec.get("added_at") or _now_iso()
        rec["updated_at"] = _now_iso()

        card = _meta.claim_card_markdown(
            {"statement": rec.get("statement"), "outcome": rec.get("outcome"),
             "strength": rec.get("strength"), "kind": rec.get("claim_kind"),
             "caveats": rec.get("caveats"),
             "evidence": _claim_evidence_dict(rec.get("evidence_json"))},
            exp_id)
        result = await self._ingest_card(card, rec)
        if existing and existing.get("document_id") and existing["document_id"] != result.document_id:
            await self.lib.delete(existing["document_id"])
        elif result.already_existed:
            # Byte-identical card already stored (same statement/outcome/…). Refresh
            # the logical metadata (outcome/strength/inputs may carry fresh shas).
            await self._merge_metadata(result.document_id, _meta.record_to_metadata(rec))
        return await self._record_for_id(result.document_id)

    async def replace_experiment_claims(self, exp_id: str, claim_ids: list[str]) -> int:
        """Prune ``kind=claim`` docs for ``exp_id`` whose ``claim_id`` is NOT in the
        current set — so claims dropped from the grounding report don't linger.

        Returns the number of stale claim documents deleted. Honors the
        rebuildable-store principle: the store reflects exactly the latest report.
        """
        keep = set(claim_ids)
        existing = await self.lib.list_documents(filters={"kind": "claim", "exp_id": exp_id})
        pruned = 0
        for doc in existing:
            cid = (doc.metadata or {}).get("claim_id")
            if cid not in keep:
                await self.lib.delete(doc.document_id)
                pruned += 1
        return pruned

    async def set_tags(self, *, kind: str, key_field: str, key: str,
                       add: list[str], remove: list[str]) -> dict[str, Any]:
        docs = await self.lib.list_documents(filters={"kind": kind, key_field: key})
        if not docs:
            raise KeyError(key)
        doc = docs[0]
        rec = _meta.document_to_record(doc)
        tags = set(rec.get("tags") or [])
        tags.update(t.strip() for t in add if t.strip())
        tags.difference_update(t.strip() for t in remove)
        await self._merge_metadata(rec["document_id"],
                                   {"tags": sorted(tags), "updated_at": _now_iso()})
        return await self._record_for_id(rec["document_id"])

    async def remove(self, document_id: str) -> None:
        await self.lib.delete(document_id)

    # ---- internals ----------------------------------------------------------
    def relpath(self, path: Path) -> str:
        path = path.resolve()
        try:
            return str(path.relative_to(self.home.resolve()))
        except ValueError:
            return str(path)

    async def _ingest_card(self, markdown: str, rec: dict[str, Any]) -> Any:
        """Ingest a deterministic Markdown card. The card's bytes (not its temp
        filename) decide libkit's ``document_id``, so identical cards collapse."""
        card_dir = self.home / self._store_dirname / "cards"
        card_dir.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkstemp(suffix=".md", dir=card_dir)[1])
        try:
            tmp.write_text(markdown, encoding="utf-8")
            return await self.lib.ingest(tmp, metadata=_meta.record_to_metadata(rec))
        finally:
            tmp.unlink(missing_ok=True)

    async def _record_for_id(self, document_id: str) -> dict[str, Any]:
        return _meta.document_to_record(await self.lib.get_document(document_id))

    async def _merge_metadata(self, document_id: str, changes: dict[str, Any]) -> None:
        doc = await self.lib.get_document(document_id)
        merged = dict(doc.metadata or {})
        merged.update(changes)
        await self.lib.update_metadata(document_id, metadata=merged)


def _claim_evidence_dict(evidence_json: Any) -> dict[str, Any]:
    """Parse the stored ``evidence_json`` (a JSON string) back to a dict for the card
    body. Tolerant: a non-string or unparseable value yields an empty dict."""
    import json

    if isinstance(evidence_json, dict):
        return evidence_json
    if not isinstance(evidence_json, str) or not evidence_json:
        return {}
    try:
        val = json.loads(evidence_json)
    except (ValueError, TypeError):
        return {}
    return val if isinstance(val, dict) else {}
