"""The libkit-backed store for regulator.

libkit (>=0.5.0) **is** the store — there is no separate regulator database.
Each regulatory document is one libkit *document*; every field (doc_type, title,
FDA org, application number, status, …) lives in that document's free-form
``metadata`` JSON. This module wraps ``libkit.Library`` with the document-level
operations libkit deliberately does not provide — readable citekeys, dedup by a
type-specific natural key, tag merges, and citation-only stubs (a document we
know about from a source index but haven't downloaded the PDF for yet).

Adapted from the bibliographer skill's ``BiblioStore`` (same libkit mechanics,
same embedder-resilience contract); the differences are the document model
(``doc_type`` instead of papers) and dedup by natural key instead of DOI/arXiv.

Two libkit facts shape everything here:

* ``document_id`` is the SHA-256 of the file bytes, so byte-identical copies
  collapse on ingest (``already_existed=True``); *document*-level identity is ours.
* ``update_metadata(metadata=...)`` REPLACES the JSON wholesale, so every
  mutation is a read-modify-write (see :meth:`_merge_metadata`).
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import meta as _meta


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EmbedderConfigError(RuntimeError):
    """The configured embedder doesn't match how the library was built."""


class RegStore:
    """Async wrapper over a libkit ``Library`` scoped to one regulator library dir."""

    def __init__(self, home: Path, lib: Any) -> None:
        self.home = home
        self.lib = lib
        self.semantic_available: bool = bool(getattr(lib, "has_embedder", True))
        self.embedder_reason: str | None = None

    # ---- lifecycle ----------------------------------------------------------
    @staticmethod
    def _build_embedder(embedding: str, model: str) -> tuple[Any, str | None]:
        from libkit.embedders import default_embedder

        try:
            return default_embedder(embedding=embedding, model=model), None
        except (RuntimeError, ValueError) as e:
            return None, str(e)

    @classmethod
    def open(
        cls,
        home: Path,
        *,
        embedding: str | None = None,
        model: str | None = None,
        read_only: bool = False,
        want_semantic: bool = False,
    ) -> "RegStore":
        """Open (creating if needed) the libkit library under ``home``.

        Mirrors bibliographer's embedder-resilience contract: reads and full-text
        search never embed, so a read-only open goes through
        ``Library.open_reader`` (FTS-only) and works with no embedding backend
        configured. ``want_semantic=True`` (only ``reg query``) additionally
        builds the embedder; a writable open always needs one (ingest embeds).
        Defaults come from ``REGULATOR_EMBEDDING`` (default ``local``) and
        ``REGULATOR_EMBED_MODEL`` (default ``qwen3_600m``).
        """
        from libkit import Library
        from libkit.errors import EmbedderMismatch

        home.mkdir(parents=True, exist_ok=True)
        (home / "docs").mkdir(exist_ok=True)
        embedding = embedding or os.environ.get("REGULATOR_EMBEDDING", "local")
        model = model or os.environ.get("REGULATOR_EMBED_MODEL", "qwen3_600m")
        allow_mismatch = os.environ.get("REGULATOR_ALLOW_EMBEDDER_MISMATCH", "").lower() in (
            "1", "true", "yes",
        )
        db_path = home / "catalog.duckdb"
        if read_only and not db_path.exists():
            raise FileNotFoundError(
                f"no regulator library at {home} (catalog.duckdb missing) — "
                "run `reg init` or an ingest command (e.g. `reg drugsfda add …`) first."
            )
        if read_only:
            from libkit.errors import EmbedderDimMismatch

            embedder, reason = (None, None)
            if want_semantic:
                embedder, reason = cls._build_embedder(embedding, model)
            if embedder is not None:
                try:
                    lib = Library.open_reader(db_path, embedder=embedder)
                except (EmbedderMismatch, EmbedderDimMismatch) as e:
                    embedder = None
                    reason = (
                        "the configured embedder does not match this library "
                        f"(stored {getattr(e, 'observed', '?')}, configured "
                        f"{getattr(e, 'expected', '?')}); set REGULATOR_EMBEDDING / "
                        "REGULATOR_EMBED_MODEL to the model the library was built with"
                    )
                    lib = Library.open_reader(db_path, embedder=None)
            else:
                lib = Library.open_reader(db_path, embedder=None)
            store = cls(home, lib)
            store.semantic_available = embedder is not None
            store.embedder_reason = None if embedder is not None else reason
            return store

        from libkit.errors import EmbedderDimMismatch

        try:
            lib = Library.open(
                db_path,
                embedding=embedding,
                model=model,
                allow_embedder_mismatch=allow_mismatch,
                read_only=False,
            )
        except (EmbedderMismatch, EmbedderDimMismatch) as e:
            raise cls._mismatch_error(e) from e
        except (RuntimeError, ValueError) as e:
            raise EmbedderConfigError(
                "no embedding backend is available, so the library cannot be opened for "
                "writes (ingesting a document has to embed its text).\n"
                f"  reason: {e}\n"
                "Install a local model — libkit[fancychunk-torch] (or [fancychunk-mlx] "
                "on Apple Silicon) — or set REGULATOR_EMBEDDING=remote with "
                "DEEPINFRA_API_KEY. Read-only commands (text/list/search/show) work "
                "without an embedder."
            ) from e
        store = cls(home, lib)
        store.semantic_available = True
        return store

    @staticmethod
    def _mismatch_error(e: Any) -> "EmbedderConfigError":
        return EmbedderConfigError(
            "this library was built with a different embedding backend than the "
            "one configured now:\n"
            f"  stored : {e.observed}\n"
            f"  current: {e.expected}\n"
            "Set REGULATOR_EMBEDDING / REGULATOR_EMBED_MODEL to match how the library "
            "was created, or set REGULATOR_ALLOW_EMBEDDER_MISMATCH=1 to override (only "
            "if you know the two are vector-compatible)."
        )

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

        Checks the doc_type's natural keys first (e.g. an accessdata PDF URL for
        drugsfda, the guidance_id for guidance), then a citekey match, then a
        normalised-title + doc_type fallback. This is *document*-level dedup,
        layered over libkit's byte-level identity.
        """
        dt = rec.get("doc_type")
        for key in _meta.NATURAL_KEYS.get(dt, ()):  # type: ignore[arg-type]
            value = rec.get(key)
            if value:
                docs = await self.lib.list_documents(filters={key: str(value)})
                if docs:
                    return _meta.document_to_record(docs[0])
        if rec.get("citekey"):
            hit = await self.get_by_citekey(rec["citekey"])
            if hit:
                return hit
        if rec.get("title") and dt:
            want = _meta.norm_title(rec["title"])
            for d in await self.lib.list_documents(filters={"doc_type": dt}):
                cand = _meta.document_to_record(d)
                if _meta.norm_title(cand.get("title")) == want:
                    return cand
        return None

    async def unique_citekey(self, base: str) -> str:
        base = base or "doc"
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
        """Add a regulatory document to the library.

        ``file_path`` is the *final* on-disk location to ingest (the caller has
        already organised it via :mod:`fileorg`); ``None`` ingests a deterministic
        Markdown stub for a citation-only record (a document known from a source
        index but not yet downloaded). Returns a result dict with ``status`` one
        of ``added`` | ``duplicate`` | ``merged`` and the stored ``record``.
        """
        if not force:
            dup = await self.find_duplicate(rec)
            if dup is not None:
                return {"status": "duplicate", "record": dup}

        rec = dict(rec)
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
            merged = await self._merge_into_existing(result.document_id, rec)
            return {"status": "merged", "record": merged}

        stored = await self._record_for_id(result.document_id)
        return {"status": "added", "record": stored}

    async def add_document(
        self,
        rec: dict[str, Any],
        *,
        src: Path | None = None,
        move: bool = True,
        force: bool = False,
    ) -> dict[str, Any]:
        """Place ``src`` into the on-disk tree (if given) and add the record.

        The one entry point the source ingesters call: it organises the file via
        :mod:`fileorg` then ingests it, or ingests a citation-only stub when
        ``src`` is None. Dedup (by the doc_type's natural key) happens in
        :meth:`add`, so re-running an ingest is idempotent.
        """
        from . import fileorg as _fileorg

        if src is not None:
            if not force:
                dup = await self.find_duplicate(rec)
                if dup is not None:
                    return {"status": "duplicate", "record": dup}
            dest = _fileorg.place(self.home, rec, Path(src), move=move)
            return await self.add(rec, file_path=dest, force=True)
        return await self.add(rec, force=force)

    async def attach_pdf(self, citekey: str, pdf_path: Path, *, move: bool) -> dict[str, Any]:
        """Attach a real PDF to an existing stub record (upgrade stub -> full)."""
        from . import fileorg as _fileorg

        rec = await self.get_by_citekey(citekey)
        if rec is None:
            raise KeyError(citekey)
        old_id = rec["document_id"]
        new = {
            k: v for k, v in rec.items()
            if k not in ("document_id", "content_hash", "file_path", "content_state",
                         "_page_count", "_chunk_count")
        }
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
                self._rmdir_if_empty(fp.parent)
        await self.lib.delete(rec["document_id"])
        return rec

    async def query(self, text: str, *, limit: int = 8, fts_only: bool = False) -> list[Any]:
        """Semantic / full-text search *inside* the documents (libkit hybrid query)."""
        return await self.lib.query(text, limit=limit, fts_only=fts_only)

    async def leading_text(self, document_id: str, chunks: int = 2) -> str:
        from libkit.errors import ChunkNotFound

        parts = []
        for i in range(chunks):
            try:
                parts.append((await self.lib.get_chunk(document_id, i)).text)
            except ChunkNotFound:
                break
        return " ".join(parts)

    async def document_text(self, document_id: str, max_chunks: int = 10_000) -> str:
        """Concatenate a document's stored chunks (the text `reg text` prints)."""
        from libkit.errors import ChunkNotFound

        parts = []
        for i in range(max_chunks):
            try:
                parts.append((await self.lib.get_chunk(document_id, i)).text)
            except ChunkNotFound:
                break
        return "\n\n".join(parts)

    # ---- internals ----------------------------------------------------------
    @staticmethod
    def _dir_effectively_empty(d: Path) -> bool:
        return not any(p.name != ".DS_Store" for p in d.iterdir())

    def _rmdir_if_empty(self, folder: Path) -> None:
        docs = self.home / "docs"
        try:
            if folder != docs and folder.is_dir() and docs in folder.parents \
                    and self._dir_effectively_empty(folder):
                for junk in folder.iterdir():
                    junk.unlink()
                folder.rmdir()
                self._rmdir_if_empty(folder.parent)
        except OSError:
            pass

    def prune_empty_dirs(self) -> int:
        docs = self.home / "docs"
        if not docs.is_dir():
            return 0
        removed = 0
        dirs = sorted((p for p in docs.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
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
        md = stub_markdown(rec)
        stub_dir = self.home / ".stubs"
        stub_dir.mkdir(exist_ok=True)
        tmp = Path(tempfile.mkstemp(suffix=".md", dir=stub_dir)[1])
        try:
            tmp.write_text(md, encoding="utf-8")
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

    async def _merge_into_existing(self, document_id: str, rec: dict[str, Any]) -> dict[str, Any]:
        doc = await self.lib.get_document(document_id)
        existing = dict(doc.metadata or {})
        changes: dict[str, Any] = {}

        tags = set(existing.get("tags") or []) | set(rec.get("tags") or [])
        if tags != set(existing.get("tags") or []):
            changes["tags"] = sorted(tags)

        # Fill any field the existing entry lacks (don't clobber).
        for key, value in rec.items():
            if value and not existing.get(key) and key not in ("added_at", "updated_at", "tags"):
                changes[key] = value

        if changes:
            changes["updated_at"] = _now_iso()
            await self._merge_metadata(document_id, changes)
        return await self._record_for_id(document_id)


def stub_markdown(rec: dict[str, Any]) -> str:
    """A deterministic Markdown rendering of a citation-only record.

    Ingested into libkit when a document has no file yet (e.g. a guidance known
    from the corpus index but not downloaded): the summary becomes real
    searchable text and the document carries full metadata. Determinism (sorted
    fields, no timestamps) makes re-ingest idempotent.
    """
    lines = [f"# {rec.get('title') or '(untitled)'}", ""]
    facts = []
    for label, key in (
        ("Type", "doc_type"), ("FDA org", "fda_org"), ("Center", "center"),
        ("Status", "status"), ("Issue date", "issue_date"), ("Docket", "docket_number"),
        ("Guidance type", "guidance_type"), ("Topic", "topic"),
        ("Application", "application_number"), ("Sponsor", "sponsor_name"),
        ("Brand", "brand_name"), ("Active ingredient", "active_ingredient"),
        ("Review type", "review_type"), ("Approval date", "approval_date"),
        ("Committee", "committee"), ("Meeting date", "meeting_date"),
        ("Material", "material_type"), ("URL", "source_url"),
    ):
        if rec.get(key):
            facts.append(f"- **{label}:** {rec[key]}")
    if facts:
        lines += sorted(facts) + [""]
    if rec.get("summary"):
        lines += ["## Summary", "", str(rec["summary"]).strip(), ""]
    return "\n".join(lines) + "\n"
