---
name: archivist
description: >-
  Organize, index, and search a tree of scientific experiments (raw lab/CRO data,
  protocols, reports, notebooks, and internal summaries) kept as one folder per
  experiment. Index every file for full-text + semantic search inside a libkit
  store, catalog experiments with their CRO study IDs / assays / ASOs / models,
  cross-reference related studies, pull exact numbers out of source spreadsheets,
  and keep internal README/summary write-ups current when the underlying data
  changes. Use this skill whenever the user wants to file a new lab or CRO data
  delivery, index or search their scientific/experimental records, find which
  experiment or file has a given result, look up everything done with a given ASO
  or assay or at a given CRO, scaffold a new experiment folder, regenerate an
  experiment README or a cross-experiment scientific summary, or audit the data
  folder for stale summaries or layout problems — even if they don't say
  "archivist." Triggers include "index my experiments," "search the lab data for
  X," "which study has the Day-29 knockdown numbers," "what have we run with ASO
  154," "file this CRO report," "update the experiment summary," or "is this
  summary still accurate."
---

# Archivist

Archivist manages a tree of scientific experiments — the kind of folder a lab or
biotech keeps, with one subfolder per experiment containing raw measurements,
cleaned data, lab protocols, CRO reports, analysis notebooks, and an internal
(usually AI-written) summary. It indexes everything for full-text + semantic
search, catalogs experiments and the entities they involve (CROs, study IDs,
assays, ASOs, models), cross-references related studies, and keeps the internal
write-ups current as the underlying data changes. It's driven by one bundled tool,
`scripts/arx.py`, backed by a **libkit** library.

The primary consumer is an LLM agent. Archivist's job is to make a sprawling,
heterogeneous data folder *answerable*: "which file has the lumbar-cord knockdown
numbers," "show me everything we ran with ASO 154," "is this summary still true."

## The store: libkit (no separate database)

libkit (≥ 0.2.3) **is** the store — there is no separate archivist database. The
managed folder holds a libkit library under `<home>/.archivist/catalog.duckdb`
(gitignored), which indexes every file and tracks all metadata. Three *kinds* of
libkit document live there, distinguished by the `kind` metadata key:

- **`experiment`** — a generated card per experiment folder (keyed by its internal
  id, e.g. `K1-230901`): CRO study IDs, CRO, status, species/model, assays, ASOs,
  related experiments. Embedded, so an experiment is searchable as a unit.
- **`file`** — one per real file (keyed by relative path). *Narrative* files
  (README/protocol/report/analysis) are ingested whole so their text is embedded;
  *tabular* files (csv/xlsx/pzfx) get a generated **schema + preview** card so they
  are discoverable; *binary* files (instrument output, genomics) get a metadata-only
  descriptor. Every file record stores the real `path` and `sha256` so an agent can
  **open the file directly to pull exact numbers**.
- **`entity`** — only *curated* notes about an ASO/CRO/assay/model that a query
  can't reconstruct (aliases, selection rationale, caveats). Purely-derivable entity
  facts are answered by a **live query**, not stored — see *Entities* below.

## Setup: keys and the embedding backend

Opening the library always constructs an embedder (libkit fixes the vector
dimension at creation), so **every command needs an embedding backend**. Put keys
in `~/.env` (loaded automatically; same pattern as bibliographer):

- **`DEEPINFRA_API_KEY`** (default backend) — remote embeddings (Qwen3-Embedding-0.6B,
  dim 1024), no local model download. `ARCHIVIST_EMBEDDING` defaults to `remote`;
  set it to `local` with `libkit[fancychunk-*]` if you prefer on-device.
- **`DATALAB_API_KEY`** — optional; high-quality PDF parse + OCR for scanned reports
  (without it libkit uses a weaker local PDF reader).
- The embedder identity is enforced on open — don't switch `ARCHIVIST_EMBEDDING` /
  `ARCHIVIST_EMBED_MODEL` on an existing library without
  `ARCHIVIST_ALLOW_EMBEDDER_MISMATCH=1` (mixing models in one vector space is silent
  corruption). libkit's content-addressed cache makes re-indexing cheap.

## Running the tool

A self-contained PEP-723 `uv` script (declares its own deps), so it runs with no
install. **Use this form as an agent:**

```bash
uv run /path/to/skills/archivist/scripts/arx.py --home "<data folder>" <command> [args]
```

The managed folder is `--home`, or `$ARCHIVIST_HOME`, or the current directory.
Examples below write `arx` for brevity. Run `arx init` once per folder.

## Workflow

```bash
arx init                                   # create the store + .gitignore entry
arx index "K1-230901"                       # index one experiment (by id or path)
arx reindex                                 # (re)index every experiment folder
arx list                                    # experiments, with file counts
arx list --kind file --experiment K1-230901 # files in one experiment
arx show K1-230901                           # one experiment + its files
arx search "C0790222"                        # metadata search (ids/assays/ASOs/paths/tags)
arx query "lumbar spinal cord knockdown"     # SEMANTIC + full-text search INSIDE the content
arx file "K1-230901/data/quantigene_expression.csv"   # one file's record (path, sha256, schema)
arx read "K1-230901/data/quantigene_expression.csv"   # dump a csv/tsv/xlsx to pull exact values
arx entity list                              # derived registry of ASOs / assays / CROs
arx entity show "ASO-154"                    # every experiment involving ASO 154
arx catalog                                  # export CATALOG.md + .archivist/catalog.json
arx check                                    # structural integrity (missing/unindexed/layout/redundant zips)
arx audit                                    # staleness of generated docs + a semantic-pass worklist
arx readme K1-230901                          # refresh an experiment README's managed blocks
arx summary                                  # refresh the top-level SUMMARY.md
arx pr "title" path…                         # package working-tree changes into a review PR
```

**Scaffolding a new experiment** creates the folder skeleton (`raw/ data/ protocol/
reports/ analysis/` + a README template following the folder's convention) and
indexes it:

```bash
arx new K1-260601 "Rat IT Chronic Tox" --cro "Charles River" --study-id C9999001 --model "Sprague-Dawley rats"
```

**Filing a new delivery** (a CRO/vendor data package) routes each file to the right
subfolder per LAYOUT.md — protocols to `protocol/`, reports/decks to `reports/`,
instrument output and raw measurements to `raw/`. It **copies** (never moves) and is
**dry-run by default** — review the plan, then `--commit`:

```bash
arx intake K1-260601 ~/Downloads/C9999001_delivery       # dry-run: show the placement plan
arx intake K1-260601 ~/Downloads/C9999001_delivery --commit   # copy in + reindex
```

`intake` flags destination collisions, skips OS cruft, and preserves any existing
`raw/Run 2/…` substructure in the source. Review the dry-run before committing.

**How `index` enriches an experiment:** it reads the folder's `README.md` and
extracts — conservatively — the CRO, external study IDs, assays, ASOs, species/model,
status, and related experiments (controlled vocabularies + tight patterns; *own*
study IDs and lifecycle status come only from the README's table/title or the folder
name, never scraped from prose). Those populate the experiment card and the derived
entity registry. Re-running `index` after the README or files change refreshes it.

**Two kinds of search, and the difference matters:**

- **`arx search`** — fast metadata lookup over experiment/file records (study IDs,
  CRO, assays, ASOs, paths, tags). Use for "the C0790222 study", "files tagged X".
- **`arx query`** — libkit hybrid vector + BM25 search *inside the indexed content*
  (summaries, protocols, reports, and tabular schemas). Use for concepts and
  results — "where's the dose-dependent gait effect" — not just metadata. The
  **internal summaries/READMEs are the highest-value hits**, since they're the
  human-written narrative. Add `--kind experiment|file` to scope.

**Pulling exact numbers.** `query`/`search` *find* the right file; to read precise
values, open it. `arx read <path>` dumps csv/tsv/xlsx as text; for other formats use
the recorded `path` from `arx file <path>` and open it directly.

## Entities (registry + live query)

Entities — ASOs, assays, CROs, study IDs, models — are **not** stored as standing
records when their facts are derivable; `arx entity show "ASO-154"` runs a live
query over experiment metadata and is therefore **always current, never stale**.
Only *curated, non-derivable* notes about an entity (why an ASO was selected, a
CRO's quirks, an alias) are stored as `kind=entity` documents and embedded.

## Generating & maintaining the write-ups

`arx readme <exp>` and `arx summary` keep the **mechanical** parts of an experiment
README and the top-level `SUMMARY.md` current — they (re)write only the
archivist-**managed regions** (the Files-on-disk table, the experiment index) and the
**dependency block**, and leave all human/agent narrative (synopsis, key findings,
caveats) untouched. The interpretive prose is yours to write; archivist never
flattens it.

Each generated doc carries an explicit **dependency block** — an
`<!-- archivist:deps … -->` comment listing the evidence files it was built from with
each file's `sha256` at generation time. This drives staleness detection:

- **`arx check`** — deterministic structural integrity: missing/relocated files,
  on-disk files not yet indexed, layout drift, thin metadata, and **redundant
  archives** (a zip whose members are already extracted in-folder — the `raw.zip`
  case). Reports a worklist; never mutates.
- **`arx audit`** — re-hashes each doc's dependency block to flag it `STALE` when an
  input changed or went missing (or `no-deps-block` when it can't judge that way),
  and `--json` emits a per-experiment worklist (`source_files`) for the **semantic
  pass**: fan out an agent per experiment to read the data and verify the README's
  claims. This is the authoritative content check — see
  [references/auditing.md](references/auditing.md).

## Changes land as reviewable PRs

Archivist treats the data folder as a git repo with a **private GitHub remote**, and
never writes content silently to `main`. `readme`/`summary` regenerate files in the
working tree; `arx pr "title" <paths>` (or `arx readme … --pr` / `arx summary --pr`)
branches, commits, pushes, and opens a **pull request for the user to review and
merge** (`--dry-run` shows the git/gh steps first). The libkit store (`.archivist/`)
is gitignored.

## Command reference

`init` · `index`/`reindex` · `list` · `show` · `search` · `query` · `file` · `read` ·
`entity` (registry + curated notes) · `new` (scaffold) · `intake` (file a delivery) ·
`catalog` (export) · `readme`/`summary` (generate, preserving narrative) · `check`
(structural) · `audit` (staleness + semantic worklist) · `pr` (open a review PR). All
of `list`/`search`/`show`/`query`/`check`/`audit`/`catalog`/`index`/`intake` take
`--json` for machine-readable output.

## Good habits

- **Index is idempotent**: re-running `index`/`reindex` after files change replaces
  the affected records (keyed by `exp_id` / file path); unchanged files are cheap
  cache hits.
- **`search` is metadata, `query` is content** — reach for `query` when the answer
  lives in a summary/report, not in an id or tag.
- **Don't trust a filename for what a file contains** — verify against indexed
  content; the same lesson bit bibliographer hard.
- **Surface the `exp_id`** — it's the stable handle for an experiment.

## Maintaining this skill (for agents working ON archivist)

Read the repo-wide [AGENTS.md](../../AGENTS.md) first — improve-as-you-go, push rote
work into code, **PR your skill changes back to the skills repo**, contribute generic
fixes upstream to libkit by PR, and verify changes. Archivist-specific notes:

- **libkit is the upstream** for store/embedding/cache fixes — issue + PR there, not a
  local workaround (this is how bibliographer drove several libkit features).
- **Run the tests**: `uv run --with pytest --with openpyxl pytest skills/archivist/tests/ -q`
  runs the pure helpers in well under a second; add `--with "libkit>=0.2.3" --with
  platformdirs` to include the store integration test (fake embedder + Markdown
  loader — no model or keys). Add a test when you add behavior.
- **Never hand-edit `.archivist/catalog.duckdb` or move files manually** — go through
  `arx`, and put repeated manual operations into a new command or `_*.py` helper.

For the periodic correctness/hygiene procedure (structural `check`, deps-staleness
`audit`, and the parallel-agent semantic pass), see
[references/auditing.md](references/auditing.md).
