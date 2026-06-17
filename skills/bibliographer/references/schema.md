# Data model: bibliographer records in libkit

There is **no separate bibliographer database**. The store is a libkit library
at `<library>/catalog.duckdb`; each paper is one libkit *document*. This note
documents how a bibliographer *record* maps onto a libkit document.

## libkit document fields

libkit promotes four keys to real columns; everything else a caller passes lives
in a free-form `metadata` JSON column (see libkit's own schema, v2):

| libkit field | source in bibliographer |
|--------------|-------------------------|
| `document_id` | SHA-256 of the file bytes (libkit assigns it; this is byte identity) |
| `content_hash` | same SHA-256 |
| `title` | the paper title (promoted from our metadata) |
| `date` | publication date when known, else file mtime (libkit fills it) |
| `source_url` | DOI URL / arXiv abs URL / file URI |
| `content_type` | `application/pdf`, `text/markdown`, … |
| `metadata` (JSON) | **all bibliographic fields below** |
| `page_count`, `chunk_count`, `ingested_at` | libkit bookkeeping |

## Keys bibliographer writes into `metadata`

| key | notes |
|-----|-------|
| `citekey` | `<authorYearWord>`, e.g. `vaswani2017attention` — the stable handle; unique per library |
| `title` | also promoted to the libkit column |
| `authors` | list of `{"family", "given"}` |
| `authors_text` | `"Family, Given; …"` — denormalized for display/search |
| `year` | integer |
| `venue` | journal / conference / "arXiv preprint" |
| `doi`, `arxiv_id`, `pmid`, `pmcid`, `s2_id` | identifiers (used for dedup; filterable) |
| `bibtex_type` | `article`, `inproceedings`, `misc`, … |
| `abstract` | JATS/HTML stripped |
| `volume`, `issue`, `pages`, `publisher` | when available |
| `tags` | list of strings. Two conventions: `topic:<slug>` (from the source folder at import) and `type:<kind>` for non-article documents (`type:genereview`, `type:statpearls`, `type:techdoc`, `type:supplement`, …) |
| `file_path` | path of the organized file, relative to the library home (absent for stubs) |
| `original_path`, `original_paths` | where the file came from; a list when copies were merged |
| `legacy_id` | a pre-existing `NNN` id parsed from an imported filename, if any |
| `content_state` | `full` (a real file ingested) or `stub` (citation-only Markdown) |
| `source` | `crossref` \| `arxiv` \| `semantic_scholar` \| `unpaywall_pdf` \| `pdf` \| `file` |
| `sniffed_from` | the identifier recovered from a PDF, e.g. `doi:10.1/x` |
| `enriched_from` | set to `unverified` when `enrich` upgraded a file-only record to resolved metadata |
| `oa_pdf_url` | open-access PDF URL from Unpaywall, if any |
| `added_at`, `updated_at` | ISO timestamps |

An **unverified** record (`source` = `pdf`/`file`, no identifier recovered) is
filed under `papers/Unknown/` with an `anon…` citekey. `bib enrich` upgrades these:
it queries Crossref from the original `author_year_title` filename, **verifies the
candidate against the document's parsed content**, and on a match rewrites the
metadata, regenerates the citekey, and re-files the PDF. The content check is not
optional: filenames in a real pile can be wrong (a file named for one paper may
contain another), and matching the filename alone would silently attach incorrect
metadata. Records that aren't journal articles (GeneReviews/StatPearls/bulletins/
supplements) won't resolve and are kept, tagged `type:<kind>`.

Because libkit's `query(filters=…)` and `list_documents(filters=…)` match
`metadata` keys by scalar equality **or** list membership, lookups like "all
papers with this DOI" or "everything tagged `topic:nlp`" are direct filters; no
SQL and no full table scan in the common case.

## Identity & dedup (two layers)

- **Byte identity (libkit):** `document_id` = SHA-256 of the file. Byte-identical
  copies collapse automatically on ingest (e.g. the same PDF filed under several
  folders) — `ingest` reports `already_existed`, and bibliographer merges the
  new tags / original paths into the existing record.
- **Paper identity (bibliographer):** a candidate is a duplicate of an existing
  record if any of DOI, arXiv id, PMCID, PMID, S2 id matches, or normalized-title
  + year matches. `find_duplicate` powers `add`'s refusal and `import`'s tag-merge;
  `dedupe` reports groups across the whole library. Normalization lowercases,
  strips to `[a-z0-9 ]`, and collapses whitespace.

A known limitation: an unverified record with **no identifier and no year**
(e.g. a scan whose text yielded nothing) can't be paper-deduped; only a
byte-identical copy of it will merge. `check` flags such records.

## Mutations

libkit's `update_metadata(metadata=…)` **replaces the JSON wholesale**, so every
field change is a read-modify-write (`BiblioStore._merge_metadata`). Adding a new
bibliographic key needs no schema change — it's just another key in `metadata`.

## Stubs (citation-only records)

A paper with no file is stored by ingesting a deterministic Markdown stub
(`_meta.stub_markdown`): a heading, the authors, identifier facts, and the
abstract. Determinism (sorted fields, no timestamps in the body) makes re-adding
idempotent — same bytes → same `document_id` → a no-op ingest. When a real PDF
later arrives, ingest it and remove the stub (tie them together by DOI/arXiv id).
