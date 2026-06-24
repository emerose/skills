# Commands

`reg <verb>` for library-wide commands; `reg <source> <verb>` for the four source
groups (covered in their own references). Global flag: `--home <dir>` (or
`REGULATOR_HOME`, default `~/.regulator`). Most read commands take `--json`.

## Library commands

### `reg init`
Create the library directory, the libkit store, and the HTML viewer. Run once
(ingest commands also create the store on demand).

### `reg list [--type T] [--json]`
List documents, optionally filtered to one `doc_type`
(`guidance`/`drugsfda`/`adcomm`/`personnel`). Human output is one line per doc;
`--json` emits full records.

### `reg show <citekey> [--json]`
Show one document's fields by citekey.

### `reg search <query> [--type T] [--json]`
**Substring metadata search** over the catalog — matches all query terms across
title, sponsor, brand, ingredient, FDA org, topic, docket, committee, name,
review_type, guidance_type. Fast; no embedder. Use this to *find a document*.

### `reg query <text> [--limit N] [--fts] [--json]`
**Semantic / full-text search _inside_ the documents** (libkit hybrid query) —
use this to *answer a question from the content*. Needs an embedder for semantic
mode; `--fts` forces keyword (BM25), and it auto-falls-back to FTS (with a loud
`[FTS-only]` warning) if no usable embedder is configured. Each hit prints the
citekey, doc_type, score, and a snippet of the matching chunk.

### `reg text <citekey>`
Print one document's stored text (what `query` searches). Handy to confirm a
scanned PDF OCR'd correctly.

### `reg tag <citekey> [--add ...] [--remove ...]`
Add/remove free tags.

### `reg rm <citekey> [--delete-file]`
Remove a document from the catalog; `--delete-file` also deletes the on-disk PDF
and prunes the now-empty folder.

### `reg viewer`
(Re)build the self-contained `index.html` browser viewer (filter + free-text
search, grouped by doc_type). Auto-regenerated after any mutating command.

### `reg add <url|file> [--type T] [--title …] [--program …] [--tag …]`
Ingest **one** document the source ingesters don't cover — an FDA PFDD "Voice of
the Patient" report, a patient-experience report, a hand-written landscape note,
any FDA PDF. A URL is downloaded into the tree; a local file is ingested in
place. `--type` defaults to `other`. Use this for the disease-/modality-level
context docs that matter for unapproved programs (FDA publishes no review
packages pre-approval, so there's nothing program-specific to fetch).

### `reg check [--json]`
Integrity check: missing files, documents with no `doc_type`, duplicate
citekeys. Reports a worklist; changes nothing.

### `reg import [dir] [--dry-run] [--include-docs]`
Index an existing folder of regulatory documents **in place** — for an archive
the user already curated, rather than fetched. Walks `dir` (default: the library
home) for ingestible files and ingests each into the libkit store *without
moving it*, preserving the human-organised folder structure. Classification is
best-effort from the filename/path:

- Names matching the accessdata Drugs@FDA convention (`206488Orig1s000MedR.pdf`)
  become `drugsfda` records with the application number, submission, and review
  type parsed out, and the brand/ingredient pulled from a `NN_Drug_Brand`
  program folder.
- Everything else becomes an `other` record titled from the filename, tagged
  with its program folder.

`--dry-run` previews the classification (no ingest/embed — free and offline).
The managed `docs/` tree is skipped by default (those files are indexed when
fetched); `--include-docs` re-walks it. Re-running is idempotent (libkit
byte-dedup + per-type natural keys). **Caveat:** an imported review's
`application_number` is the bare digits (the NDA/BLA prefix isn't in the
accessdata filename), so it won't dedup against a later canonical
`reg drugsfda add NDA…` of the same review — treat import as "make the existing
archive searchable," and prefer `drugsfda add` when you want canonical metadata.

## Source commands (see per-source references)

- `reg drugsfda search|add` → [drugs-at-fda](drugs-at-fda.md)
- `reg guidance sync|search|add` → [guidance](guidance.md)
- `reg adcomm sync` → [advisory-committees](advisory-committees.md)
- `reg personnel build` → [personnel](personnel.md)

## Python API

`RegStore` is the structured surface; its methods return the same dicts the CLI
prints under `--json`. Open read-only for concurrent readers (no embedder needed
for reads/FTS):

```python
import asyncio
from pathlib import Path
from regulator import RegStore

async def main():
    store = RegStore.open(Path.home() / ".regulator", read_only=True)
    try:
        guidances = await store.all_records({"doc_type": "guidance"})
        rec = await store.get_by_citekey("NDA205834-s000-medical")
        # semantic search needs want_semantic=True at open time:
    finally:
        await store.close()

asyncio.run(main())

# semantic query:
store = RegStore.open(Path.home() / ".regulator", read_only=True, want_semantic=True)
hits = await store.query("accelerated approval surrogate endpoint", limit=8)
```

The source ingesters are importable too and depend only on stdlib + httpx
(no libkit), so their parsers are usable/testable standalone:

```python
from regulator.sources import drugsfda, guidance, adcomm, personnel
summary, docs = await drugsfda.gather_docs("NDA205834")     # enumerate PDFs
recs = guidance.parse_rows(feed_json)                        # parse the corpus feed
mats = adcomm.extract_materials(page_html, page_url=url)     # scrape a meeting page
sigs = personnel.extract_signatures(review_text)            # harvest signatures
```
