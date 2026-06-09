# Search & index — make the tree answerable

Index every file for full-text + semantic search inside a **libkit** store, catalog experiments
with their CRO study IDs / assays / ASOs / models, cross-reference related studies, file new
deliveries, and scaffold new experiments. The "find / answer / organize" face of the scientist.

Deep reference: [auditing.md](auditing.md).

## The store: libkit (no separate database)

libkit (≥ 0.2.3) **is** the store — `<home>/.scientist/catalog.duckdb` (gitignored), indexing every
file + metadata. Four document *kinds*, distinguished by the `kind` metadata key:

- **`experiment`** — one card per experiment folder (keyed by `exp_id`, e.g. `K1-000000`). Structured
  fields come **only from `experiment.yml`** (never scraped from prose). Embedded, so an experiment
  is searchable as a unit.
- **`file`** — one per real file (keyed by relative path). Narrative files (README/protocol/report/
  analysis) ingested whole so their text embeds; tabular files (csv/xlsx/pzfx) get a **schema +
  preview card**; binaries get a metadata-only descriptor. Every record stores real `path` + `sha256`
  so an agent can **open the file to pull exact numbers**.
- **`entity`** — only *curated* non-derivable notes about an ASO/CRO/assay/model (aliases, rationale,
  caveats). Derivable facts are answered by a **live query**, not stored.
- **`claim`** — each grounded claim from the pytest harness, embedded on its **statement** and
  carrying its **outcome + strength + claim kind** so a hit is honest (a contradicted `xfail` or
  weak claim never reads as plain positive evidence). Keyed by a stable `claim_id`
  (`<exp_id>::<test-file>::<node>`, reproducible across runs/machines). Index with
  **`sci index-claims <exp>`** (reads `grounding_report.json`); search with
  **`sci query "…" --kind claim`**. The highest-value searchable evidence (see
  [derive-claims.md](derive-claims.md) and [review-audit.md](review-audit.md)).

## Setup: keys & the embedding backend

Opening the library constructs an embedder (libkit fixes the vector dimension at creation), so
**every command needs an embedding backend**. Keys in `~/.env`:
- **`DEEPINFRA_API_KEY`** (default) — remote embeddings (Qwen3-Embedding-0.6B, dim 1024), no local
  download. `SCIENTIST_EMBEDDING` defaults to `remote`; set `local` with `libkit[fancychunk-*]` for on-device.
- **`DATALAB_API_KEY`** — optional; high-quality PDF parse + OCR for scanned reports.
- The embedder identity is enforced on open — don't switch model on an existing library (silent
  vector corruption); libkit's content-addressed cache makes re-indexing cheap.

## Running the tool

```bash
uv run skills/scientist/scripts/sci.py <command> [args] --home "<data folder>"
```
The managed folder is `--home`, `$SCIENTIST_HOME` (one var drives the store + the `experiments`
root), or cwd. Run `init` once per folder.

```bash
sci init                                    # create the store + .gitignore entry
sci index "K1-000000"                        # index one experiment (by id or path)
sci reindex                                 # (re)index every experiment folder
sci index-claims "K1-000000"                 # index grounded claims from grounding_report.json
sci list [--kind file|claim --experiment K1-000000]
sci show K1-000000                           # one experiment + its files
sci search "V1234567"                        # metadata search (ids/assays/ASOs/paths/tags)
sci query "lumbar spinal cord knockdown"     # SEMANTIC + full-text search INSIDE the content
sci query "gait deficit" --kind claim        # grounded claims only (shows outcome + strength)
sci file  "K1-000000/data/quantigene.csv"    # one file's record (path, sha256, schema)
sci read  "K1-000000/data/quantigene.csv"    # dump a csv/tsv/xlsx to pull exact values
sci entity list | show "ASO-7"               # derived registry / every experiment involving ASO 7
sci catalog                                 # export CATALOG.md + .scientist/catalog.json
sci meta K1-000000 [--suggest]               # show experiment.yml metadata (--suggest = a draft)
```

**Two kinds of search, and the difference matters:**
- **`sci search`** — fast metadata lookup over experiment/file records (study IDs, CRO, assays, ASOs,
  paths, tags). For "the V1234567 study", "files tagged X".
- **`sci query`** — libkit hybrid vector + BM25 *inside the indexed content* (summaries, protocols,
  reports, tabular schemas, **and claims**). For concepts and results — "where's the dose-dependent
  gait effect". **Internal summaries/READMEs and grounded claims are the highest-value hits.** Add
  `--kind experiment|file|claim` to scope.

**Pulling exact numbers.** `query`/`search` *find* the right file; to read precise values, open it
(`sci read <path>`, or the recorded `path` from `sci file <path>`).

## Structured metadata lives in `experiment.yml` (not the prose)

Each experiment folder has a tracked, schema'd `experiment.yml` sidecar — the single source of truth
for structured metadata (`exp_id`, `cro`, `cro_study_ids`, `status`, `model`, `assays`, `asos`,
`related`, and the `provenance` list). The **`README.md` stays purely prose; scientist never writes
to it.** Populate the sidecar yourself or start from `sci meta <exp> --suggest` (a heuristic *draft*
you review). Unknown fields / bad status raise a clear error.

**Private vocabulary (your real CRO names).** `--suggest` canonicalizes CRO names + study-id formats
from a controlled vocabulary. The public repo ships only generic placeholders (`Vendor A`, …); keep
real vendor names in a private `vocab.yml` at your data-folder root (or `$SCIENTIST_VOCAB`). See
[vocab.example.yml](vocab.example.yml).

## Scaffold an experiment / file a delivery

```bash
sci new K1-000003 "Rat IT Chronic Tox" --cro "Vendor A" --study-id V9999001 --model "Sprague-Dawley rats"
sci intake K1-000003 ~/Downloads/V9999001_delivery          # dry-run: show the placement plan
sci intake K1-000003 ~/Downloads/V9999001_delivery --commit  # copy in (never move) + reindex
```
`new` creates the folder skeleton (`raw/ data/ protocol/ reports/ analysis/` + README template) and
indexes it. `intake` routes each file to the right subfolder per LAYOUT.md, flags collisions, skips
OS cruft, preserves any `raw/Run 2/…` substructure. **Review the dry-run before committing.**

## Good habits
- **Index is idempotent** — re-running after files change replaces affected records (keyed by
  `exp_id`/path); unchanged files are cheap cache hits.
- **`search` is metadata, `query` is content** — reach for `query` when the answer lives in a
  summary/report/claim, not an id/tag.
- **Surface the `exp_id`** — it's the stable handle for an experiment.

Changes land as reviewable PRs (`sci pr "title" <paths>`): the data folder is a git repo with a
private remote; edits are made in the working tree, then branched/committed/pushed/opened as a PR.
The libkit store (`.scientist/`) is gitignored. See [review-audit.md](review-audit.md) for `pr`.
