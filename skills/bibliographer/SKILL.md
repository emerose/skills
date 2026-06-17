---
name: bibliographer
description: >-
  Manage a personal collection of academic articles — add papers from a DOI,
  arXiv ID, PMID/PMCID, Semantic Scholar ID, or a PDF (auto-fetching metadata
  from Crossref/arXiv/PubMed/Semantic Scholar), keep PDFs organized on disk in a
  human-readable author tree, search and browse the library, run semantic search
  inside the papers, generate BibTeX, bulk-import a folder of PDFs, and run
  dedupe/integrity checks. Use this skill whenever the user wants to save, file,
  organize, look up, or tidy research papers, build or maintain a bibliography or
  reading list, import a folder of PDFs into their paper collection, find
  duplicate papers, recover or fix metadata for scanned/untitled PDFs, search the
  contents of their papers, or export citations — even if they don't say
  "bibliographer." Triggers include "add this paper," "save this arXiv link,"
  "what papers do I have on X," "import these PDFs," "make a bibliography,"
  "find the DOI/metadata for these PDFs," "search my papers for X," "check my
  library for duplicates," or "export BibTeX." For a tree of internal scientific
  experiments (raw lab/CRO data, extracted measurements, analysis, grounded claims),
  use the scientist skill instead.
---

# Bibliographer

Bibliographer manages a collection of academic articles: it fetches
bibliographic metadata from public sources, organizes the PDFs on disk in a
human-readable tree, and stores everything in a **libkit** library that also
gives you semantic + full-text search over the papers' contents. All of this is
driven by one bundled command-line tool, `scripts/bib.py`.

## The store: libkit (no separate database)

libkit (≥ 0.2.2) **is** the store — there is no separate bibliographer database.
Each paper is one libkit *document*; every bibliographic field (DOI, arXiv id,
authors, venue, year, abstract, tags, citekey, file path, …) lives in that
document's free-form `metadata`. Bibliographer adds the three things libkit
deliberately doesn't do: **fetch metadata** (resolvers), **organize files on
disk** (the author tree), and **paper-level identity** (citekeys + dedup by
DOI/arXiv/PMCID, layered over libkit's byte-level identity). Requires **libkit ≥ 0.2.2**.

A **library** is a directory (default `~/.bibliographer`, override with `--home`
or `BIBLIOGRAPHER_HOME`) containing:

- `catalog.duckdb` — the libkit store (documents, chunks, embeddings, FTS index)
- `papers/` — the organized originals: `papers/<First Author, Given>/<Authors> (<Year>) - <Title>.pdf`
- `index.html` — a self-contained HTML viewer of the whole library, regenerated on
  every change; open it in a browser for a prominent search box plus a sidebar to
  browse by author / topic / type / publication / year (with live counts), and
  click through to each PDF (makes the folder self-describing, no server needed)

Each article has a **citekey** (e.g. `vaswani2017attention`) generated from the
first author, year, and first significant title word. It's the stable handle for
`show`, `tag`, `rm`, and `export`; the on-disk filename is a human-facing
convenience and can change without breaking anything.

A paper with no file yet (a citation-only record) is stored as a deterministic
Markdown **stub** (`content_state: stub`), so it's still searchable and carries
full metadata; it's upgraded to `full` when a PDF arrives.

## Setup: keys and the embedding backend

Opening the library always constructs an embedder (libkit fixes the store's
vector dimension at creation), so **every command needs an embedding backend**.
Put keys in `~/.env` (the tool loads it automatically; see `.env.example`):

- **`DEEPINFRA_API_KEY`** + `BIBLIOGRAPHER_EMBEDDING=remote` — recommended: remote
  embeddings (Qwen3-Embedding-0.6B, dim 1024), so **no local model download**.
  Alternatively install `libkit[fancychunk-torch]` (or `[fancychunk-mlx]` on
  Apple Silicon) and use `BIBLIOGRAPHER_EMBEDDING=local`. The model/dimension
  must stay consistent across runs.
- **`DATALAB_API_KEY`** — only for PDF import: high-quality parse + OCR for
  scanned PDFs. Without it, libkit falls back to a local PDF reader (weaker on scans).
- **`BIBLIOGRAPHER_MAILTO`** — your email, for the Crossref/NCBI/Unpaywall polite pools.
- **`S2_API_KEY`** — optional; raises the Semantic Scholar rate limit so abstract
  enrichment is reliable. Without it S2 is skipped gracefully (Crossref/arXiv
  cover most abstracts).

Resolver responses are cached on disk (the same `diskcache` libkit uses), so
re-running `add`/`import` hits the network zero times for identifiers already
seen — and never waits on the Semantic Scholar throttle. Tune with
`BIBLIOGRAPHER_CACHE_DIR` / `BIBLIOGRAPHER_CACHE_TTL`, or `BIBLIOGRAPHER_NO_CACHE=1`.

Two more things to know about opening a library:

- **Embedder identity is enforced.** libkit records which embedder built the
  library and refuses to open it with a different one (mixing models in one vector
  space is silent corruption). If you change `BIBLIOGRAPHER_EMBEDDING` /
  `BIBLIOGRAPHER_EMBED_MODEL`, you'll get a clear error telling you to match the
  original or set `BIBLIOGRAPHER_ALLOW_EMBEDDER_MISMATCH=1` (only when you *know*
  the two are vector-compatible).
- **Parse/embed reuse the libkit cache.** libkit caches parses (keyed by file +
  loader) and embeddings (keyed by embedder + chunk text) in its shared,
  content-addressed cache, so re-ingesting a document — or one already processed
  by another libkit tool — skips the expensive work. Relocate it with libkit's own
  `LIBKIT_CACHE_DIR` if needed.

## Running the tool

It's a self-contained PEP-723 `uv` script (it declares its own deps: `libkit`,
`httpx`, `pypdf`, `diskcache`, `platformdirs`), so it runs with no install. The
always-works form — **use this in scripts and as an agent** — is:

```bash
uv run /path/to/skills/bibliographer/scripts/bib.py <command> [args]
```

The examples below write `bib` for brevity. To get a real `bib` on your PATH for
interactive use, symlink the script — its `#!/usr/bin/env -S uv run --script`
shebang resolves dependencies on each run:

```bash
chmod +x /path/to/skills/bibliographer/scripts/bib.py
ln -s /path/to/skills/bibliographer/scripts/bib.py ~/.local/bin/bib   # then: bib add 10.1038/nphys1170
```

(The symlink needs no packaging. A future option, once the repo is published, is
packaging it for `uv tool install` / `uvx`.) Run `bib init` once per library
before first use.

## Workflow

**Adding a paper** is the most common task. Give it a DOI, arXiv id, PMID, PMCID,
Semantic Scholar id, an arxiv.org/doi.org URL, or a PDF path — `add` figures out which:

```bash
bib add 10.1038/nphys1170                  # DOI       -> Crossref
bib add arXiv:1706.03762                    # arXiv id  -> arXiv API
bib add PMC9283931                          # PMCID     -> NCBI -> Crossref
bib add https://arxiv.org/abs/1810.04805    # URL is parsed for you
bib add ~/Downloads/paper.pdf               # PDF: sniffs DOI/arXiv/PMC id from filename+text
bib add 10.1038/nphys1170 --pdf paper.pdf   # metadata from DOI, attach this PDF
bib add arXiv:1706.03762 --tags transformers,nlp
```

Notes that matter when adding:

- `add` **refuses likely duplicates** (matching DOI, arXiv id, PMCID, or normalized
  title + year) and names the existing entry. Pass `--force` only if it's genuinely different.
- A bare identifier with no PDF becomes a citation-only stub. `add` first tries to
  **fetch an open-access PDF automatically** — arXiv, Europe PMC, bioRxiv/medRxiv,
  Unpaywall, and Semantic Scholar — and ingests it if found; pass `--no-fetch` to
  skip. For a stub that had no OA copy, `bib fetch <citekey>` retries those
  sources later, and when they fail it points you to the manual routes
  (institutional browser access, or an authorized peer source); attach a
  hand-obtained PDF with `bib fetch <citekey> --pdf <file>`. See
  [references/getting-pdfs.md](references/getting-pdfs.md) for the full ladder.
- For a local file, `add` **copies** it into the author tree (`--move` to move
  instead) and tries to recover an identifier from the PDF; if nothing resolves
  it falls back to the PDF's embedded metadata and marks the record unverified —
  tell the user, and consider supplying the DOI/arXiv id to enrich it.

**Bulk-importing a folder** (e.g. a Downloads dir or an existing pile). Always
dry-run first:

```bash
bib import ~/papers --dry-run                 # resolve + plan the tree + report coverage; moves nothing
bib import ~/papers                           # after review: move files in, ingest, embed
bib import ~/papers --copy                    # copy instead of move
bib import ~/papers --exclude 17_other_drugs  # skip files whose path contains this (repeatable)
bib import ~/papers --limit 8 --dry-run       # preview just the first few
```

`import` **moves by default** (`--copy` to keep originals), walks the tree,
resolves each file (sniffing identifiers), turns each file's top-level folder into
a provisional `topic:<slug>` tag, captures any leading `NNN` id as `legacy_id`,
merges cross-filed duplicates into one record (their topics union as tags), and is
resilient (one bad file won't abort the run). **Review the dry-run** before
committing — moving is one-way.

**Recovering metadata for unverified records.** Files with no extractable
identifier (old scans, junk filenames) land as *unverified* records — filed under
`papers/Unknown/` with an `anon…` citekey. `enrich` recovers their real metadata:

```bash
bib enrich --dry-run                         # show proposed matches for all unverified, change nothing
bib enrich                                   # auto-apply verified matches, list the rest to review
bib enrich vaswani2017attention --doi 10.x/y # force a specific id for one record (also accepts s2:<id> / pmid:<id>)
```

For each unverified record `enrich` builds a Crossref bibliographic query from the
original `author_year_title` filename, then **verifies the candidate against the
document's actual parsed content** before applying — this is essential because
**filenames in a pile can lie** (a file named `…punt_2022…` may actually contain a
different paper). A verified match updates the record with real metadata,
regenerates the citekey, and re-files the PDF into the proper author folder
(`--no-refile` to skip the move). Anything that doesn't verify is listed for you
to resolve with `--doi` (look it up via Semantic Scholar / PubMed when Crossref's
free-text search misses, or for papers that genuinely predate DOIs). Documents
that aren't journal articles at all (GeneReviews, StatPearls, technical bulletins,
supplements) won't resolve — tag them by kind instead, e.g.
`bib tag <citekey> --add type:genereview`.

**Finding things:**

```bash
bib list                       # everything
bib search transformer         # metadata search over title/authors/venue/abstract/tags
bib search --author hinton --year 2015
bib search --tag topic:nlp
bib query "why do transformers scale"   # SEMANTIC + full-text search INSIDE the papers (libkit)
bib show vaswani2017attention            # full record
bib show vaswani2017attention --bibtex   # one BibTeX entry
```

Use `search` for fast metadata lookup; use `query` when the user wants to find
*passages/concepts inside* the papers (it embeds the query and runs libkit's
hybrid vector + BM25 search).

**Organizing and exporting:**

```bash
bib tag vaswani2017attention --add to-read --add transformers
bib tag vaswani2017attention --remove to-read
bib rm olddraft2019            # remove from catalog (keeps the file)
bib rm olddraft2019 --delete-file
bib export                     # BibTeX for the whole library (on demand, to stdout)
bib export vaswani2017attention devlin2018bert > refs.bib
bib viewer                     # (re)generate the index.html viewer and print its path
```

The library's `index.html` viewer is regenerated automatically on every change;
`bib viewer` just forces a rebuild (and is run by `init`). Open it in a browser to
search by title/author/venue/tag/year and click straight through to each PDF.

**Keeping the library healthy:**

```bash
bib dedupe     # report probable duplicate groups (review, then `bib rm`)
bib check      # missing files, changed file bytes, orphan files, citation-only/unverified records
bib audit      # deeper review: misfiling, thin metadata, content-vs-title mismatch (a worklist)
bib audit --json   # structured worklist to drive fixes (incl. a parallel-agent pass)
```

`dedupe`, `check`, and `audit` only report; they never delete. Run `audit`
periodically (especially after a big import) as a hygiene step — see
[references/auditing.md](references/auditing.md) for the full procedure, including
fanning out parallel agents to verify each document's *content* against its stored
metadata. Empty folders under `papers/` are pruned automatically after every command.

## Machine-readable output

`list`, `search`, `show`, `add`, `import`, `enrich`, `query`, `dedupe`, `check`,
and `audit` take `--json`. Prefer it when you need to parse results, count, or
feed another step.

## Good habits

- **Dry-run imports first** and summarize coverage (resolved-online vs unverified,
  sniffed, duplicates) before moving the user's files. Same for `enrich`.
- **Surface the citekey** you assigned — it's how the user (and you) refer to the paper.
- **Confirm destructive actions**: `rm --delete-file`, `--move`, `--force`, and a
  real (non-dry-run) `import` change or relocate the user's data.
- **Flag unverified records** and offer `enrich`; never trust a filename's
  author/title without the content backing it up.
- **Be polite to the APIs**: set `BIBLIOGRAPHER_MAILTO`; Semantic Scholar is throttled to ≤1 req/s.
- **Verify before deleting "duplicates."** Confirm a file's bytes match a
  *cataloged* file (hash → `document_id`) before removing it; an "orphan" that
  isn't a true byte-dup is usually a real paper that failed to ingest, not junk.

## Gotchas (learned the hard way)

- **Library location is a real trade-off.** A library inside a cloud-synced folder
  (Google Drive, etc.) is browsable everywhere, but **moving hundreds of files
  into it triggers a heavy one-time cascade** — the cloud client re-syncs every
  move, Spotlight re-indexes each PDF, and backup tools re-copy them. That can peg
  CPU (often showing as kernel/system time from file-provider I/O) even though the
  import itself is light. `check` may also briefly report "missing file" for files
  mid-sync — re-run it once sync settles. A local, non-indexed folder avoids all
  of this.
- **Every `add`/`import` embeds.** libkit has no metadata-only ingest, so each file
  is parsed + embedded. The parse (Datalab) is the expensive step; its cache is
  reused across runs, so re-imports are cheap, but the *first* parse of a large
  pile takes real time/cost.
- **Don't switch embedding placement on an existing library** (e.g. remote→local)
  without `BIBLIOGRAPHER_ALLOW_EMBEDDER_MISMATCH=1` — libkit will (correctly)
  refuse it.
- **A DOI in a PDF can be a *citation*, not the paper.** Sniffing identifiers from
  PDF text can grab a DOI from the **reference list** (a cited work) and mislabel
  the file as that paper — this really happened. `import` guards against it (it
  trusts ids from the filename, embedded metadata, and pre-"References" text, and
  content-verifies any id found only in the bibliography), but a deterministic
  title-overlap check is **fooled** when the cited title's own words sit in the
  references. The authoritative content check is the **semantic audit** (parallel
  agents that actually read each paper) — see
  [references/auditing.md](references/auditing.md). Don't trust a low/high overlap
  score as proof; have an agent read the document.

## Maintaining this skill (for agents working ON bibliographer)

Read the repo-wide [AGENTS.md](../../AGENTS.md) first — improve-as-you-go, push
rote work into code, **PR your skill changes back to the skills repo**, contribute
generic dependency fixes upstream by PR, and verify changes. Those principles apply here (and `enrich`, `audit`, the resolver cache, and empty-dir
pruning all began as repeated manual steps that got codified). Bibliographer-
specific notes:

- **libkit is the upstream to push generic fixes to.** Bug or missing capability in
  the store/embedding/cache layer → issue + PR on libkit, not a local workaround.
  This skill's needs have already driven several upstream libkit fixes.
- **Run the tests** (`tests/`): `uv run --with pytest --with httpx pytest
  skills/bibliographer/tests/ -q` runs the pure helper tests in well under a second
  (add `--with "libkit>=0.2.2" --with diskcache --with platformdirs` to include the
  store integration test, which uses a fake embedder + Markdown loader — no model
  or keys). Add a test when you add behavior; run network-touching changes against
  a throwaway `--home`.
- **Never hand-edit `catalog.duckdb` or move files manually** — go through `bib`.

For getting a PDF when there's no open-access copy (preprint servers, PMC, the
institutional browser, and authorized peer sources), see
[references/getting-pdfs.md](references/getting-pdfs.md). For the metadata model
and the libkit mapping, see
[references/schema.md](references/schema.md). For how bibliographer uses libkit
as its store (embedding, search, caching, the warm-cache migration), see
[references/libkit-integration.md](references/libkit-integration.md). For the
periodic correctness/hygiene procedure (and the parallel-agent audit), see
[references/auditing.md](references/auditing.md).
