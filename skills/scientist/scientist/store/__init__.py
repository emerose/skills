"""The store layer of the scientist skill: a libkit-backed index/search/catalog
over a tree of scientific experiments.

libkit IS the store — there is no separate database. Each experiment, file, and
curated-entity note is one libkit document; all fields live in the document's
free-form ``metadata`` JSON. The store dir is ``<home>/.scientist/catalog.duckdb``
(gitignored, rebuildable from a ``sci reindex``).

Structured ``experiment.yml`` access (read/validate/write the sidecar, record
provenance, compute staleness, resolve review inputs) is NOT duplicated here — it
routes through the shared :mod:`provenance` core. This package keeps the libkit
glue (:mod:`_store`), the record/card model (:mod:`_meta`), file walking/schema
(:mod:`_files`), controlled-vocabulary metadata normalizers (:mod:`_extract`), intake planning
(:mod:`_intake`), structural audit (:mod:`_audit`), view rendering (:mod:`_generate`),
and the PR plumbing (:mod:`_pr`). The CLI handlers live in :mod:`store.cli`.
"""

from __future__ import annotations

from . import _audit, _extract, _files, _generate, _intake, _meta, _pr, _store
from ._store import STORE_DIRNAME, Store, EmbedderConfigError

__all__ = [
    "Store", "EmbedderConfigError", "STORE_DIRNAME",
    "_store", "_meta", "_files", "_extract", "_intake", "_audit", "_generate", "_pr",
]
