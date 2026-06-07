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
  7," "file this CRO report," "update the experiment summary," or "is this
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
numbers," "show me everything we ran with ASO 7," "is this summary still true."

## The store: libkit (no separate database)

libkit (≥ 0.2.3) **is** the store — there is no separate archivist database. The
managed folder holds a libkit library under `<home>/.archivist/catalog.duckdb`
(gitignored), which indexes every file and tracks all metadata. Three *kinds* of
libkit document live there, distinguished by the `kind` metadata key:

- **`experiment`** — a card per experiment folder (keyed by its internal id, e.g.
  `K1-000000`). Its structured fields (CRO, study IDs, status, species/model, assays,
  ASOs, related) come **only from that experiment's `experiment.yml` sidecar** (see
  below) — never scraped from prose. Embedded, so an experiment is searchable as a unit.
- **`file`** — one per real file (keyed by relative path). *Narrative* files
  (README/protocol/report/analysis) are ingested whole so their text is embedded;
  *tabular* files (csv/xlsx/pzfx) get a generated **schema + preview** card so they
  are discoverable; *binary* files (instrument output, genomics) get a metadata-only
  descriptor. Every file record stores the real `path` and `sha256` so an agent can
  **open the file directly to pull exact numbers**.
- **`entity`** — only *curated* notes about an ASO/CRO/assay/model that a query
  can't reconstruct (aliases, selection rationale, caveats). Purely-derivable entity
  facts are answered by a **live query**, not stored — see *Entities* below.

## Structured metadata lives in `experiment.yml` (not in the prose)

Each experiment folder has a tracked, schema'd **`experiment.yml`** sidecar next to
its `README.md`. It is the *single source of truth* for structured metadata, and the
tool reads it deterministically (validated YAML — a stray pipe or odd layout can't
corrupt anything). The **`README.md` stays purely human/agent prose; archivist never
writes to it.** Don't mechanically parse READMEs for metadata — populate the sidecar.

```yaml
exp_id: K1-000000
cro: Vendor A
cro_study_ids: ["V1234567"]
status: complete            # planned|active|complete|terminated|failed|superseded|draft
model: Sprague-Dawley rats
assays: [QuantiGene, Luminex, LC-MS/MS]
asos: [ASO-7]
related: [K1-000001, K1-000002]
provenance:                 # written by `arx review`, read by `arx audit`
  - artifact: README.md
    artifact_sha256: sha256-of-README-at-review
    reviewed_at: 2026-06-03
    inputs:                 # the exact files the prose was verified against, each versioned
      - { path: "K1-000000 - Rat IT Dose-Response (V1234567)/data/quantigene_expression.csv", sha256: … }
      - { path: "Shared/Vendor A/SOW2/…/report.pptx", sha256: … }   # inputs may live outside the folder
```

Populate it yourself or start from `arx meta <exp> --suggest` (a heuristic *draft*
from the README that you review — never authoritative). Unknown fields, wrong types,
or bad status values raise a clear error rather than silently vanishing.

**Private vocabulary (your real CRO names).** The `--suggest` heuristic canonicalizes
CRO names and recognizes study-id formats from a controlled vocabulary. This public
repo ships only **generic placeholders** (`Vendor A`, …). Your real vendor names and
vendor-specific study-id shapes are program-specific — keep them in a private
`vocab.yml` at your data-folder root (or `$ARCHIVIST_VOCAB`), which `arx` merges over
the defaults. See [references/vocab.example.yml](references/vocab.example.yml).

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
arx index "K1-000000"                       # index one experiment (by id or path)
arx reindex                                 # (re)index every experiment folder
arx list                                    # experiments, with file counts
arx list --kind file --experiment K1-000000 # files in one experiment
arx show K1-000000                           # one experiment + its files
arx search "V1234567"                        # metadata search (ids/assays/ASOs/paths/tags)
arx query "lumbar spinal cord knockdown"     # SEMANTIC + full-text search INSIDE the content
arx file "K1-000000/data/quantigene_expression.csv"   # one file's record (path, sha256, schema)
arx read "K1-000000/data/quantigene_expression.csv"   # dump a csv/tsv/xlsx to pull exact values
arx entity list                              # derived registry of ASOs / assays / CROs
arx entity show "ASO-7"                    # every experiment involving ASO 7
arx catalog                                  # export CATALOG.md + .archivist/catalog.json
arx meta K1-000000                            # show the experiment.yml metadata (--suggest for a draft)
arx review K1-000000                          # stamp provenance after verifying the README vs the data
arx fingerprint K1-000000 --manifest          # the evidence fingerprint + exactly what's hashed
arx check                                    # structural integrity (missing sidecar/unindexed/layout/redundant zips)
arx audit                                    # provenance staleness + a semantic-pass worklist
arx pr "title" path…                         # package working-tree changes into a review PR
```

**Scaffolding a new experiment** creates the folder skeleton (`raw/ data/ protocol/
reports/ analysis/` + a README template following the folder's convention) and
indexes it:

```bash
arx new K1-000003 "Rat IT Chronic Tox" --cro "Vendor A" --study-id V9999001 --model "Sprague-Dawley rats"
```

**Filing a new delivery** (a CRO/vendor data package) routes each file to the right
subfolder per LAYOUT.md — protocols to `protocol/`, reports/decks to `reports/`,
instrument output and raw measurements to `raw/`. It **copies** (never moves) and is
**dry-run by default** — review the plan, then `--commit`:

```bash
arx intake K1-000003 ~/Downloads/V9999001_delivery       # dry-run: show the placement plan
arx intake K1-000003 ~/Downloads/V9999001_delivery --commit   # copy in + reindex
```

`intake` flags destination collisions, skips OS cruft, and preserves any existing
`raw/Run 2/…` substructure in the source. Review the dry-run before committing.

**How `index` enriches an experiment:** it reads the folder's `experiment.yml`
(validated YAML) for the structured fields, indexes every file, and indexes the
README prose for search. It does **not** parse prose for metadata — if a sidecar is
missing or invalid, the experiment is indexed with minimal metadata and `check`/`audit`
flag it. Re-running `index` after the sidecar or files change refreshes the card.

**Two kinds of search, and the difference matters:**

- **`arx search`** — fast metadata lookup over experiment/file records (study IDs,
  CRO, assays, ASOs, paths, tags). Use for "the V1234567 study", "files tagged X".
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
records when their facts are derivable; `arx entity show "ASO-7"` runs a live
query over experiment metadata and is therefore **always current, never stale**.
Only *curated, non-derivable* notes about an entity (why an ASO was selected, a
CRO's quirks, an alias) are stored as `kind=entity` documents and embedded.

## Keeping write-ups current (provenance + staleness)

Archivist writes no prose. What it *does* maintain is the link between a README's
narrative and the data it describes, via `experiment.yml`'s `provenance` block — an
**explicit, versioned list of the input files** the prose was verified against (not an
opaque hash), so review and drift are inspectable file by file:

- **`arx review <exp>`** — run after you've verified the README still matches the data.
  It records, per artifact (the README), each input file with its `sha256`, plus the
  README's own `sha256` and the date. Inputs = the experiment's in-folder data files
  (everything except a root `README.*` and the sidecar) **plus** any external
  dependency you declare with `--input <repo-relative path>` (repeatable; e.g. CRO
  slides under `Shared/`). External inputs are preserved across re-reviews.
- **`arx audit`** — re-hashes every recorded input and the README and reports
  `up-to-date`, `stale` (naming each input that **changed** / went **missing** /
  was **added**, and whether the README itself was edited since review),
  `no-provenance` (never reviewed), or `no-/invalid-experiment-yml`. `--json` adds a
  per-experiment `source_files` worklist for the **semantic pass**: fan out an agent
  per experiment to read the data and verify the prose — the authoritative content
  check (see [references/auditing.md](references/auditing.md)).
- **`arx fingerprint <exp>`** — prints the input files (+ each current `sha256`) that
  `review` would record right now, so you can see exactly what provenance tracks.
- **`arx check`** — structural integrity: missing `README.md`/`experiment.yml`,
  on-disk files not indexed, layout drift, thin metadata, and **redundant archives**
  (a zip whose members are already extracted in-folder — the `raw.zip` case). Reports
  a worklist; never mutates.

## Changes land as reviewable PRs

Archivist treats the data folder as a git repo with a **private GitHub remote** and
never writes silently to `main`. Edits (a new/updated `experiment.yml`, a regenerated
`CATALOG.md`) are made in the working tree; `arx pr "title" <paths>` branches, commits,
pushes, and opens a **pull request for you to review and merge** (`--dry-run` shows the
git/gh steps first). The libkit store (`.archivist/`) is gitignored.

## Command reference

`init` · `index`/`reindex` · `list` · `show` · `search` · `query` · `file` · `read` ·
`entity` · `new` (scaffold) · `intake` (file a delivery) · `meta` (show/suggest
`experiment.yml`) · `review` (stamp provenance) · `fingerprint` · `catalog` (export) ·
`check` (structural) · `audit` (staleness + semantic worklist) · `pr` (review PR). Most
read commands take `--json`.

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
