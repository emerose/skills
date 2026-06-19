"""bibliographer — a libkit-backed library of academic articles.

This package is the importable core behind the ``bib`` CLI (``scripts/bib.py``).
Library code lives here as proper modules so it can be used two ways:

* **CLI** — ``bib <command>`` (or ``uv run scripts/bib.py``); zero-install via the
  script's PEP-723 header. Best for one-shot lookups; add ``--json`` for a
  structured payload instead of the human table.
* **Python** — ``from bibliographer import BiblioStore`` and call the store
  directly. Best for composition (loops/joins over records) without paying a
  subprocess + cold-start per call. Needs the package importable, e.g.
  ``uv run --with-editable skills/bibliographer python …`` or installed.

``BiblioStore`` IS the structured API: its methods return plain dicts/lists, the
same payloads the CLI prints under ``--json``. Open read-only for concurrent
readers::

    import asyncio
    from pathlib import Path
    from bibliographer import BiblioStore

    async def main():
        store = BiblioStore.open(Path.home() / ".bibliographer", read_only=True)
        try:
            recs = await store.all_records()                 # list[dict]
            hits = await store.query("ube3a dosage", limit=8) # semantic search
            one  = await store.get_by_citekey("ni2016reciprocal")
        finally:
            await store.close()

    asyncio.run(main())

Submodules (``meta``, ``resolvers``, ``discovery``, ``fileorg``, ``viewer``) hold
the metadata mapping, identifier resolvers, cross-API discovery, the on-disk
file organiser, and the HTML viewer respectively.
"""

from __future__ import annotations

from .store import BiblioStore, EmbedderConfigError

__all__ = ["BiblioStore", "EmbedderConfigError"]
