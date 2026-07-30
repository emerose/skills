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
  (`Library.list_documents(filters=…)` + a Python pass). Instant; no embedding and
  no ranking — the query is one **literal substring** tested against
  `title+authors+venue+abstract+citekey+identifiers+tags`, so multi-word queries need verbatim adjacent
  wording. Use it for "papers by X", "tagged Y", or a single distinctive token; see
  [commands.md](commands.md#bib-search-is-a-substring-matcher--feed-it-one-word).
- **`bib query`** — `Library.query(text, filters, limit)`: embeds the query and
  runs libkit's hybrid vector + BM25 search over the papers' **chunked contents**,
  returning ranked passages. Use for concepts/passages not in the title/abstract.
  When no embedder is available it runs `Library.query(text, fts_only=True)`
  (BM25-only) under a loud `[FTS-only]` banner rather than silently degrading.

## Opening without an embedder (FTS-only reads)

Reads and full-text search never embed, so `BiblioStore.open(..., read_only=True)`
opens via libkit's **`Library.open_reader`**, which constructs **no** embedder —
keyless/offline reads (`bib text`, the scientist grounding readers) work with no
local model and no API key. Only `bib query` passes `want_semantic=True`, which
*tries* to build the embedder and, on failure, opens FTS-only with
`store.semantic_available = False` and `store.embedder_reason` set (so the CLI can
warn with the specific cause + fix). Writable opens still build the embedder
(ingest embeds) and raise an actionable `EmbedderConfigError` when none exists.
This FTS-only / no-embedder open path is a libkit ≥0.5.0 capability: the
eager-embedder behavior was fixed *in libkit* (the generic fix — any read-only
consumer benefits — belongs upstream), not worked around here.

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

## Catalog disk bloat and `bib compact`

The whole library — documents, chunks, the VSS **HNSW** index
(`chunks_vector_hnsw` on `chunks`), and the FTS snapshot (`fts_main_chunks`) —
lives in one DuckDB file. Two DuckDB facts make that file grow far past its
logical size under normal use:

- **DuckDB never shrinks a database file in place.** Deletes/updates free blocks
  *inside* the file (reused for later writes) but never return them to the OS, so
  the file stays at its high-water mark. Confirmed directly: delete every row,
  then `VACUUM` + `CHECKPOINT` → file size unchanged.
- The **experimental persistent HNSW index** rewrites/appends on every
  `CHECKPOINT`; under heavy add/delete/update churn its on-disk footprint
  balloons, and because of the point above that growth is sticky.

Observed in production: **225 GB for ~1,700 papers** (logical data ~1 GB), with
`PRAGMA database_size` showing `used_blocks ≈ total_blocks`, `free_blocks ≈ 0` —
the bloat is *live* index pages, not free space, so `VACUUM`/`CHECKPOINT` reclaim
~nothing.

### What actually reclaims the space (measured)

Measured on a churned throwaway library (real libkit, fake embedder; see
`tests/test_compact.py` and the methodology in `bibliographer/compact.py`):

| method | reclaims file space? | notes |
|---|---|---|
| `PRAGMA hnsw_compact_index('chunks_vector_hnsw')` | **no** | runs without error, file size unchanged — it tidies the in-memory index structure but, because DuckDB won't shrink in place, the file doesn't drop |
| `CHECKPOINT` + `VACUUM` | **no** | in-place; never returns blocks to the OS |
| `DROP INDEX` + recreate HNSW, then `CHECKPOINT` | **no** | freed blocks stay in the file (same in-place limitation) — and you've paid a full index rebuild for nothing |
| **`COPY FROM DATABASE` → fresh file** | **yes (≈3×+)** | only a fresh file omits the freed blocks; rebuilds a compact HNSW + FTS index in the process |

**Conclusion: a full COPY-rewrite into a new file is the only mechanism that
shrinks the catalog**, because it is the only one that produces a file without the
freed/over-grown blocks. The cheaper-looking options (in-place compaction, VACUUM,
drop+recreate) cannot help while DuckDB lacks in-place file shrink. This is what
`bib compact` does (`bibliographer/compact.py`).

### The COPY-rewrite recipe (and its gotchas)

Use an **in-memory orchestrator** connection with VSS loaded, attach the old file
read-only and a fresh destination, `COPY FROM DATABASE`, detach:

```python
import duckdb                                 # libkit's pinned duckdb (1.5.4) — storage compat
con = duckdb.connect()                        # in-memory orchestrator
con.execute("INSTALL vss; LOAD vss;")         # WITHOUT this the COPY fails: unknown index type 'HNSW'
con.execute("SET hnsw_enable_experimental_persistence=true;")
con.execute("ATTACH '<home>/catalog.duckdb' AS src (READ_ONLY)")
con.execute("ATTACH '<newfile>' AS dst")
con.execute("COPY FROM DATABASE src TO dst")  # rebuilds a compact HNSW + FTS index
con.execute("DETACH src"); con.execute("DETACH dst")
```

- **`LOAD vss` is mandatory** or the index copy errors with `unknown index type
  'HNSW'`.
- **Stale WAL gotcha:** a leftover `catalog.duckdb.wal` next to the new file will
  be mis-replayed onto it → corruption. `compact` moves any `catalog.duckdb.wal*`
  aside during the swap.
- **Verify before swapping:** `compact` opens the rebuilt file and asserts the
  document/chunk counts match the source, the HNSW index + `fts_main_chunks`
  schema exist, and a sample vector query returns, *before* renaming the old file
  to `catalog.duckdb.bloated-bak` and moving the new one in. The backup is kept
  until the swap succeeds (`--keep-backup` keeps it after).
- **Exclusive access:** it must run with the library closed (it rewrites the file
  out from under libkit), so it refuses while a writer holds libkit's
  `<db>.writelock`, and takes that lock itself for the duration.

### Long-term fix — belongs upstream in libkit (follow-up)

`bib compact` is the right *operational* tool, but it is a periodic clean-up, not
a cure: a single tag-write was observed to grow a freshly-compacted file 609 MB →
802 MB, so the library re-bloats with use. The durable fix belongs **in libkit**,
where the HNSW lifecycle lives. Options, roughly in order of leverage:

1. **Rebuild the HNSW index instead of incrementally updating it** past a churn
   threshold (drop + `CREATE INDEX` inside libkit's writer), and/or compact the
   catalog on close or after N writes. libkit already `CHECKPOINT`s on close and
   owns the writer connection, so it is the natural place to amortize this.
2. Track row-version / index-growth and trigger a COPY-rewrite-on-close when the
   `used_blocks` high-water-mark outruns live data by some factor.
3. Reconsider the experimental persistent HNSW (or its checkpoint cadence) — its
   per-CHECKPOINT re-append is the root of the growth.

Recommendation: file this upstream against libkit (an `vacuum()`/`compact()`
method on `Library`, or automatic compaction on close/after-N-writes) so every
libkit consumer benefits and bibliographer can eventually call that instead of
operating on the file directly. Until then, `bib compact` is the workaround —
run it periodically and after big churn.

## libkit version

Requires **libkit ≥ 0.2.2** (pinned in `bib.py`'s `uv` header). bibliographer
depends on libkit's metadata-filtered `list_documents(filters=…)` (for lookup and
dedup without a search string) and its per-library embedder-identity enforcement.
