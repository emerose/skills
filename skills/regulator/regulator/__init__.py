"""regulator — a libkit-backed library of FDA regulatory documents.

This package is the importable core behind the ``reg`` CLI (``scripts/reg.py``).
It manages a collection of FDA regulatory artifacts — published guidance,
Drugs@FDA approval-package documents (medical / clin-pharm / statistical reviews,
approval letters, labels), advisory-committee materials, and biographical
dossiers on FDA staff — fetched from public sources, organized on disk in a
human-readable tree, and stored in a libkit library that also gives semantic +
full-text search over the documents' contents.

Two ways to use it:

* **CLI** — ``reg <command>`` (or ``uv run scripts/reg.py``); zero-install via the
  script's PEP-723 header. Add ``--json`` for a structured payload.
* **Python** — ``from regulator import RegStore`` and call the store directly,
  e.g. for composition over many records without a subprocess per call::

    import asyncio
    from pathlib import Path
    from regulator import RegStore

    async def main():
        store = RegStore.open(Path.home() / ".regulator", read_only=True)
        try:
            recs = await store.all_records({"doc_type": "guidance"})
            hits = await store.query("accelerated approval surrogate endpoint")
        finally:
            await store.close()

    asyncio.run(main())

Submodules: ``meta`` (the document model + citekeys), ``store`` (the libkit
wrapper), ``fileorg`` (the on-disk tree), ``viewer`` (HTML viewer), and
``sources/`` (one ingester per FDA data source: ``drugsfda``, ``guidance``,
``adcomm``, ``personnel``).
"""

from __future__ import annotations

from .store import RegStore, EmbedderConfigError

__all__ = ["RegStore", "EmbedderConfigError"]
