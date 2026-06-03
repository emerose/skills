"""The libkit-backed store for bibliographer.

libkit (>=0.2.0) is the single store: there is no separate bibliographer
database. Each paper is one libkit document; all bibliographic fields live in
the document's free-form ``metadata`` JSON. This module wraps ``libkit.Library``
with the paper-level operations libkit deliberately does not provide —
citekeys, dedup by DOI/arXiv/PMCID, tag merges, and citation-only stubs.

Two libkit facts shape everything here:

* ``document_id`` is the SHA-256 of the file bytes, so byte-identical copies
  collapse on ingest (``already_existed=True``); *paper*-level identity is ours.
* ``update_metadata(metadata=...)`` REPLACES the JSON wholesale, so every
  mutation is a read-modify-write (see :meth:`_merge_metadata`).
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import _meta


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EmbedderConfigError(RuntimeError):
    """The configured embedder doesn't match how the library was built."""


class BiblioStore:
    """Async wrapper over a libkit ``Library`` scoped to one library directory."""

    def __init__(self, home: Path, lib: Any) -> None:
        self.home = home
        self.lib = lib

    # ---- lifecycle ----------------------------------------------------------
    @classmethod
    def open(
        cls,
        home: Path,
        *,
        embedding: str | None = None,
        model: str | None = None,
    ) -> "BiblioStore":
        """Open (creating if needed) the libkit library under ``home``.

        ``embedding``/``model`` must be *consistent across runs* — libkit fixes
        the store's vector dimension from the embedder at creation, so opening
        later with a different-dimension embedder fails. Defaults come from
        ``BIBLIOGRAPHER_EMBEDDING`` (default ``local``) and
        ``BIBLIOGRAPHER_EMBED_MODEL`` (default ``qwen3_600m``); the bulk import
        overrides ``embedding=remote`` to reuse the warm cache, but the model —
        hence the dimension — stays the same.
        """
        from libkit import Library
        from libkit.errors import EmbedderMismatch

        home.mkdir(parents=True, exist_ok=True)
        (home / "papers").mkdir(exist_ok=True)
        embedding = embedding or os.environ.get("BIBLIOGRAPHER_EMBEDDING", "local")
        model = model or os.environ.get("BIBLIOGRAPHER_EMBED_MODEL", "qwen3_600m")
        allow_mismatch = os.environ.get("BIBLIOGRAPHER_ALLOW_EMBEDDER_MISMATCH", "").lower() in (
            "1", "true", "yes",
        )
        # Use libkit's default cache (shared, content-addressed): a document
        # parsed/embedded by any libkit tool — or a prior run — is reused, which
        # is the whole point of the cache. Relocate it with libkit's own
        # LIBKIT_CACHE_DIR if desired.
        try:
            lib = Library.open(
                home / "catalog.duckdb",
                embedding=embedding,
                model=model,
                allow_embedder_mismatch=allow_mismatch,
            )
        except EmbedderMismatch as e:
            # libkit (>=0.2.1) refuses to mix vectors from different embedders in
            # one library. Translate its error into actionable guidance.
            raise EmbedderConfigError(
                "this library was built with a different embedding backend than the "
                "one configured now:\n"
                f"  stored : {e.observed}\n"
                f"  current: {e.expected}\n"
                "Set BIBLIOGRAPHER_EMBEDDING / BIBLIOGRAPHER_EMBED_MODEL to match how "
                "the library was created, or set BIBLIOGRAPHER_ALLOW_EMBEDDER_MISMATCH=1 "
                "to override (only if you know the two are vector-compatible)."
            ) from e
        return cls(home, lib)

    async def close(self) -> None:
        await self.lib.close()

    # ---- reads --------------------------------------------------------------
    async def all_records(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        docs = await self.lib.list_documents(filters=filters)
        return [_meta.document_to_record(d) for d in docs]

    async def get_by_citekey(self, citekey: str) -> dict[str, Any] | None:
        docs = await self.lib.list_documents(filters={"citekey": citekey})
        return _meta.document_to_record(docs[0]) if docs else None

    async def find_duplicate(self, rec: dict[str, Any]) -> dict[str, Any] | None:
        """Return an existing record the candidate would duplicate, or None.

        Checks strong identifiers first (DOI/arXiv/PMCID/PMID/S2), then falls
        back to normalised-title + year. This is *paper*-level dedup, layered
        over libkit's byte-level identity.
        """
        for key in _meta.IDENTIFIER_KEYS:
            value = rec.get(key)
            if value:
                docs = await self.lib.list_documents(filters={key: str(value)})
                if docs:
                    return _meta.document_to_record(docs[0])
        if rec.get("title") and rec.get("year"):
            want = _meta.norm_title(rec["title"])
            for d in await self.lib.list_documents(filters={"year": str(rec["year"])}):
                cand = _meta.document_to_record(d)
                if _meta.norm_title(cand.get("title")) == want:
                    return cand
        return None

    async def unique_citekey(self, base: str) -> str:
        base = base or "untitled"
        if not await self.get_by_citekey(base):
            return base
        for suffix in "abcdefghijklmnopqrstuvwxyz":
            if not await self.get_by_citekey(base + suffix):
                return base + suffix
        i = 2
        while await self.get_by_citekey(f"{base}-{i}"):
            i += 1
        return f"{base}-{i}"

    # ---- writes -------------------------------------------------------------
    async def add(
        self,
        rec: dict[str, Any],
        *,
        file_path: Path | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Add a paper to the library.

        ``file_path`` is the *final* on-disk location to ingest (the caller has
        already organised it); ``None`` ingests a deterministic Markdown stub
        for a citation-only record. Returns a result dict with ``status`` one of
        ``added`` | ``duplicate`` | ``merged`` and the stored ``record``.
        """
        if not force:
            dup = await self.find_duplicate(rec)
            if dup is not None:
                return {"status": "duplicate", "record": dup}

        rec = dict(rec)
        if rec.get("authors") and not rec.get("authors_text"):
            rec["authors_text"] = _meta.authors_text(rec["authors"])
        if not rec.get("citekey"):
            rec["citekey"] = await self.unique_citekey(_meta.make_citekey(rec))
        rec["added_at"] = rec.get("added_at") or _now_iso()
        rec["updated_at"] = _now_iso()
        rec["content_state"] = "full" if file_path else "stub"
        if file_path is not None:
            rec["file_path"] = self._relpath(file_path)

        if file_path is not None:
            result = await self.lib.ingest(file_path, metadata=_meta.record_to_metadata(rec))
        else:
            result = await self._ingest_stub(rec)

        if result.already_existed:
            # A byte-identical document is already stored (e.g. a cross-filed
            # duplicate copy, or a re-run). Merge this record's tags/identifiers
            # and original path into the existing entry rather than duplicating.
            merged = await self._merge_into_existing(result.document_id, rec)
            return {"status": "merged", "record": merged}

        stored = await self._record_for_id(result.document_id)
        return {"status": "added", "record": stored}

    async def merge_duplicate(self, existing: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
        """Fold ``rec`` (a paper-level duplicate) into the ``existing`` record:
        union tags, fill missing fields, track the extra original path. Used by
        import so cross-filed copies become one record with all their topics."""
        return await self._merge_into_existing(existing["document_id"], rec)

    async def attach_pdf(self, citekey: str, pdf_path: Path, *, move: bool) -> dict[str, Any]:
        """Attach a real PDF to an existing record (upgrade a stub to full).

        The stub and the PDF have different byte-hashes, so this ingests the PDF
        as a new libkit document carrying the record's metadata, files it into the
        author tree, and deletes the old stub document. The citekey is preserved.
        """
        import _fileorg

        rec = await self.get_by_citekey(citekey)
        if rec is None:
            raise KeyError(citekey)
        old_id = rec["document_id"]
        new = {
            k: v for k, v in rec.items()
            if k not in ("document_id", "content_hash", "file_path", "content_state",
                         "_page_count", "_chunk_count")
        }
        if new.get("authors") and not new.get("authors_text"):
            new["authors_text"] = _meta.authors_text(new["authors"])
        new["content_state"] = "full"
        new["updated_at"] = _now_iso()
        dest = _fileorg.place(self.home, new, Path(pdf_path), move=move)
        new["file_path"] = self._relpath(dest)
        result = await self.lib.ingest(dest, metadata=_meta.record_to_metadata(new))
        if result.document_id != old_id:
            await self.lib.delete(old_id)
        return await self._record_for_id(result.document_id)

    async def set_tags(self, citekey: str, *, add: list[str], remove: list[str]) -> dict[str, Any]:
        rec = await self.get_by_citekey(citekey)
        if rec is None:
            raise KeyError(citekey)
        tags = set(rec.get("tags") or [])
        tags.update(t.strip() for t in add if t.strip())
        tags.difference_update(t.strip() for t in remove)
        await self._merge_metadata(rec["document_id"], {"tags": sorted(tags), "updated_at": _now_iso()})
        return await self._record_for_id(rec["document_id"])

    async def remove(self, citekey: str, *, delete_file: bool) -> dict[str, Any]:
        rec = await self.get_by_citekey(citekey)
        if rec is None:
            raise KeyError(citekey)
        if delete_file and rec.get("file_path"):
            fp = self.home / rec["file_path"]
            if fp.exists():
                fp.unlink()
                self._rmdir_if_empty(fp.parent)  # drop the folder if that was its last file
        await self.lib.delete(rec["document_id"])
        return rec

    async def query(self, text: str, *, limit: int = 8) -> list[Any]:
        """Semantic / full-text search *inside* the papers (libkit hybrid query)."""
        return await self.lib.query(text, limit=limit)

    async def leading_text(self, document_id: str, chunks: int = 2) -> str:
        """First few chunks of a document's parsed content (for match verification)."""
        from libkit.errors import ChunkNotFound

        parts = []
        for i in range(chunks):
            try:
                parts.append((await self.lib.get_chunk(document_id, i)).text)
            except ChunkNotFound:
                break
        return " ".join(parts)

    async def reenrich(self, citekey: str, new_rec: dict[str, Any], *, refile: bool) -> dict[str, Any]:
        """Replace an unverified record's metadata with resolved metadata.

        Keeps tags / original-path provenance, regenerates the citekey from the
        real authors+year, and (if ``refile``) moves the PDF out of Unknown/
        into the proper author folder. The libkit document_id is unchanged
        (same bytes), so this is a metadata update + an on-disk move.
        """
        import _fileorg

        rec = await self.get_by_citekey(citekey)
        if rec is None:
            raise KeyError(citekey)
        doc_id = rec["document_id"]

        merged = dict(new_rec)
        if merged.get("authors") and not merged.get("authors_text"):
            merged["authors_text"] = _meta.authors_text(merged["authors"])
        # preserve bibliographer-side provenance from the old record
        merged["tags"] = rec.get("tags") or []
        for k in ("original_path", "original_paths", "legacy_id", "added_at"):
            if rec.get(k) is not None:
                merged[k] = rec[k]
        merged["content_state"] = rec.get("content_state", "full")
        merged["enriched_from"] = "unverified"
        merged["updated_at"] = _now_iso()
        merged["citekey"] = await self.unique_citekey(_meta.make_citekey(merged))

        if refile and rec.get("file_path"):
            old = self.home / rec["file_path"]
            if old.exists():
                dest = _fileorg.plan_path(self.home, merged, old.suffix.lower() or ".pdf")
                if dest.resolve() != old.resolve():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    old.rename(dest)
                    self._rmdir_if_empty(old.parent)  # drop the now-empty old folder
                merged["file_path"] = self._relpath(dest)

        fields: dict[str, Any] = {"metadata": merged}
        if merged.get("title"):
            fields["title"] = merged["title"]
        if merged.get("source_url"):
            fields["source_url"] = merged["source_url"]
        await self.lib.update_metadata(doc_id, **fields)
        return await self._record_for_id(doc_id)

    # ---- internals ----------------------------------------------------------
    @staticmethod
    def _dir_effectively_empty(d: Path) -> bool:
        """A dir with nothing but a .DS_Store counts as empty."""
        return not any(p.name != ".DS_Store" for p in d.iterdir())

    def _rmdir_if_empty(self, folder: Path) -> None:
        """Remove a now-empty author folder (e.g. after a file moves out on
        re-file, or is deleted). Never touches papers/ itself; ignores errors."""
        papers = self.home / "papers"
        try:
            if folder != papers and folder.is_dir() and folder.parent == papers \
                    and self._dir_effectively_empty(folder):
                for junk in folder.iterdir():
                    junk.unlink()
                folder.rmdir()
        except OSError:
            pass

    def prune_empty_dirs(self) -> int:
        """Remove every empty folder under papers/ (a managed-library invariant).

        Runs after each command. Bottom-up so nested empties collapse; a folder
        whose only content is a .DS_Store counts as empty.
        """
        papers = self.home / "papers"
        if not papers.is_dir():
            return 0
        removed = 0
        dirs = sorted((p for p in papers.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
        for d in dirs:
            try:
                if self._dir_effectively_empty(d):
                    for junk in d.iterdir():
                        junk.unlink()
                    d.rmdir()
                    removed += 1
            except OSError:
                pass
        return removed

    def _relpath(self, path: Path) -> str:
        path = path.resolve()
        try:
            return str(path.relative_to(self.home.resolve()))
        except ValueError:
            return str(path)

    async def _ingest_stub(self, rec: dict[str, Any]) -> Any:
        md = _meta.stub_markdown(rec)
        stub_dir = self.home / ".stubs"
        stub_dir.mkdir(exist_ok=True)
        # Deterministic name keeps the temp file tidy; bytes (not name) decide
        # libkit's document_id, so re-ingesting the same record is a no-op.
        tmp = Path(tempfile.mkstemp(suffix=".md", dir=stub_dir)[1])
        try:
            tmp.write_text(md, encoding="utf-8")
            return await self.lib.ingest(tmp, metadata=_meta.record_to_metadata(rec))
        finally:
            tmp.unlink(missing_ok=True)

    async def _record_for_id(self, document_id: str) -> dict[str, Any]:
        return _meta.document_to_record(await self.lib.get_document(document_id))

    async def _merge_metadata(self, document_id: str, changes: dict[str, Any]) -> None:
        """Read-modify-write the metadata JSON (libkit replaces it wholesale)."""
        doc = await self.lib.get_document(document_id)
        merged = dict(doc.metadata or {})
        merged.update(changes)
        await self.lib.update_metadata(document_id, metadata=merged)

    async def _merge_into_existing(self, document_id: str, rec: dict[str, Any]) -> dict[str, Any]:
        doc = await self.lib.get_document(document_id)
        existing = dict(doc.metadata or {})
        changes: dict[str, Any] = {}

        tags = set(existing.get("tags") or []) | set(rec.get("tags") or [])
        if tags != set(existing.get("tags") or []):
            changes["tags"] = sorted(tags)

        # Fill any identifier/bibliographic field the existing entry lacks.
        for key in (*_meta.IDENTIFIER_KEYS, "venue", "year", "abstract", "authors", "authors_text"):
            if rec.get(key) and not existing.get(key):
                changes[key] = rec[key]

        # Track every original location a duplicate copy came from.
        if rec.get("original_path"):
            origins = list(existing.get("original_paths") or [])
            if existing.get("original_path") and existing["original_path"] not in origins:
                origins.append(existing["original_path"])
            if rec["original_path"] not in origins:
                origins.append(rec["original_path"])
            changes["original_paths"] = origins

        if changes:
            changes["updated_at"] = _now_iso()
            await self._merge_metadata(document_id, changes)
        return await self._record_for_id(document_id)
