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
sci meta K1-000000                           # show experiment.yml structured metadata
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
to it, and never *reads* it for you** — deciding what an experiment's CRO/assays/model/status are is
reading comprehension, which you do directly. Unknown fields / bad status raise a clear error on save.

### Author `experiment.yml` from the README

`sci meta <exp>` shows the current sidecar (or reports there's none). To write/refresh it:

1. **Read** `<exp>/README.md` (and any IDs table). Decide each field by judgment — don't pattern-match:
   `title`, `cro`, `model`, `status`, plus which `assays`/`asos` the work actually used. The README's
   *own* IDs table is authoritative for `cro_study_ids`; a predecessor id mentioned only in prose is
   **not** this experiment's id.
2. **Canonicalize the tokens you picked** so cross-referencing stays consistent (`sci entity show
   "ASO-7"` must match regardless of how the README spelled it). The deterministic normalizers live in
   `scientist.store._extract`; call them rather than hand-normalizing:

   ```python
   from scientist.store import _extract as X
   cro_vocab, id_pats = X.load_vocab(home)        # generic defaults + your private vocab.yml
   X.match_vocab(readme_text, cro_vocab)          # full vendor name  -> canonical, e.g. ["Vendor A"]
   X.match_vocab(readme_text, X.ASSAY_VOCAB)      # "RT-qPCR","Luminex" -> ["qPCR","Luminex"]
   X.match_vocab(readme_text, X.MODEL_VOCAB)      # species/strain      -> canonical model
   X.find_asos(readme_text)                       # "ASO 7"/"ASO007"    -> ["ASO-7"]
   X.find_study_ids(ids_table_cell, id_pats)      # validate id shapes (don't scrape free prose)
   X.find_related(readme_text, exclude=exp_id)    # cross-referenced K1- ids, self excluded
   ```

   `match_vocab` only *folds a name you supply onto its canonical form*; you still choose which
   assays/CRO apply — it won't invent them. `status` must come from an explicit lifecycle field, never
   from prose like "failed to deliver" (`X.STATUS_HINTS` has the accepted values).
3. **Write** the sidecar with the schema gate, then index:

   ```python
   from scientist import provenance as P
   P.write_sidecar(exp_dir, P.validate(meta))     # raises on unknown field / bad status
   ```
   then `sci index <exp>`.

**Private vocabulary (your real CRO names).** The public repo ships only generic placeholders
(`Vendor A`, …); keep real vendor names + vendor-specific study-id shapes in a private `vocab.yml` at
your data-folder root (or `$SCIENTIST_VOCAB`). `load_vocab(home)` merges it over the defaults. See
[vocab.example.yml](vocab.example.yml).

## Scaffold an experiment / file a delivery

```bash
sci new K1-000003 "Rat IT Chronic Tox" --cro "Vendor A" --study-id V9999001 --model "Sprague-Dawley rats"
sci intake K1-000003 ~/Downloads/V9999001_delivery          # dry-run: show the placement plan
sci intake K1-000003 ~/Downloads/V9999001_delivery \
    --route "Final Report.docx=reports" --route "SoW2.pdf=protocol" --commit   # route + copy + index
```
`new` creates the folder skeleton (`raw/ data/ protocol/ reports/ analysis/` + README template) and
indexes it.

**Filing a delivery — you classify the documents, intake does the mechanics.** `intake` handles the
deterministic part: it preserves any `raw/Run 2/…` substructure the delivery already has, routes
format-fixed binaries (`.eds/.pzfx/.bam/…`) to `raw`, flags collisions, skips OS cruft, copies (never
moves), and reindexes. What it does **not** do is guess a *document's* role — whether a PDF/DOCX/PPTX
is a protocol, a report, or a raw deliverable depends on what it contains, which is your call:

1. Run the **dry-run** (no `--commit`). Each file shows its planned subfolder; any that fell back to
   the `raw` default is marked `? unreviewed default`.
2. **Read** those flagged documents enough to place them per LAYOUT.md (`protocol` = SOWs/protocols/
   amendments, `reports` = CRO reports/decks/telecons/interpretation, `raw` = original measurements,
   `data` only for tidy extracted CSVs the recipe produces — rarely an intake target).
3. Re-run with a `--route "NAME=subdir"` for each document you're moving off the default (repeatable;
   `NAME` is the file's basename), then `--commit`. Binaries and already-organised files need no route.

`--json` emits the plan (each entry carries `routed_by`: `path`/`agent`/`ext`/`default`) so you can
drive routing programmatically. **Always review the dry-run before committing.**

## Good habits
- **Index is idempotent** — re-running after files change replaces affected records (keyed by
  `exp_id`/path); unchanged files are cheap cache hits.
- **`search` is metadata, `query` is content** — reach for `query` when the answer lives in a
  summary/report/claim, not an id/tag.
- **Surface the `exp_id`** — it's the stable handle for an experiment.

Changes land as reviewable PRs (`sci pr "title" <paths>`): the data folder is a git repo with a
private remote; edits are made in the working tree, then branched/committed/pushed/opened as a PR.
The libkit store (`.scientist/`) is gitignored. See [review-audit.md](review-audit.md) for `pr`.
