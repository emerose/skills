# How bibliographer uses libkit as its store

libkit is not optional and not a sidecar — it **is** bibliographer's store.
`scripts/bib.py` opens a `libkit.Library` at `<home>/catalog.duckdb` and treats
each paper as one document (see [schema.md](schema.md) for the metadata mapping).
This note covers the libkit-specific operational details.

## Opening the library

`BiblioStore.open(home)` calls `libkit.Library.open(home/"catalog.duckdb",
embedding=…, model=…)`. Because libkit builds the embedder eagerly (to fix the
store's vector dimension), **every command needs a working embedder**:

- `BIBLIOGRAPHER_EMBEDDING` — `remote` (DeepInfra; no local model), `local`
  (needs `libkit[fancychunk-torch]`/`[fancychunk-mlx]`), or `auto`.
- `BIBLIOGRAPHER_EMBED_MODEL` — default `qwen3_600m` (Qwen3-Embedding-0.6B, dim 1024).

The model — hence the dimension — **must be consistent across runs**. libkit
records the embedder's identity in the library and **enforces it on open**:
reopening with a different embedder raises an error rather than silently
mixing two models' vectors in one space. `BiblioStore.open` translates that into a
clear message (match `BIBLIOGRAPHER_EMBEDDING`/`BIBLIOGRAPHER_EMBED_MODEL`, or set
`BIBLIOGRAPHER_ALLOW_EMBEDDER_MISMATCH=1` to override). Local and DeepInfra
vectors for the same Qwen3 model match to cosine ≈0.9999 and are *vector*-
compatible, but they carry **different cache namespaces**, so switching placement
trips the check — use the override knowingly if you must.

## Ingest always embeds

libkit has no metadata-only ingest: `ingest(path, metadata=…)` parses → chunks →
embeds → stores. Consequences bibliographer is built around:

- A citation-only record is given a Markdown **stub** to ingest, so even
  "metadata-only" papers become real, searchable documents.
- PDFs are parsed by libkit's loader: **Datalab** when `DATALAB_API_KEY` is set
  (high quality, OCRs scans), else a local fallback. Office/Markdown are also
  supported (`.md`, `.docx`, …).
- `document_id` is the byte hash, so re-ingesting identical bytes is a no-op
  (`already_existed=True`) — bibliographer uses that to merge duplicate copies.

## Two kinds of search

- **`bib search`** — bibliographer-side metadata lookup over the records
  (`Library.list_documents(filters=…)` + a Python pass). Instant; no embedding of
  the query. Use for "papers by X", "tagged Y", title/abstract substring.
- **`bib query`** — `Library.query(text, filters, limit)`: embeds the query and
  runs libkit's hybrid vector + BM25 search over the papers' **chunked contents**,
  returning ranked passages. Use for concepts/passages not in the title/abstract.

## Caching and the bulk import

libkit keeps a **shared, content-addressed** cache: parsed documents keyed by file
hash + loader namespace, embeddings keyed by embedder namespace + chunk text.
Bibliographer uses that default cache (it does not isolate its own), so a document
parsed/embedded by any libkit tool — or a prior run — is reused. For a large
`import` the expensive **Datalab parse** is paid once per unique file and reused on
re-runs; embeddings likewise. The parse cache hits regardless of chunker settings
(parsing precedes chunking), so reuse is robust even if the chunker config differs
from a prior run — only re-embedding would re-run, which is cheap. Relocate the
cache with libkit's own `LIBKIT_CACHE_DIR` if you want it elsewhere.

## libkit version

Requires **libkit ≥ 0.2.2** (pinned in `bib.py`'s `uv` header). bibliographer
depends on libkit's metadata-filtered `list_documents(filters=…)` (for lookup and
dedup without a search string) and its per-library embedder-identity enforcement.
