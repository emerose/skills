---
name: bibliographer
description: >-
  Manage a personal collection of academic articles — add papers from a DOI,
  arXiv ID, PMID/PMCID, Semantic Scholar ID, or a PDF (auto-fetching metadata
  from Crossref/arXiv/PubMed/Semantic Scholar), keep PDFs organized on disk in a
  human-readable author tree, search and browse the library, run semantic search
  inside the papers, generate BibTeX, bulk-import a folder of PDFs, and run
  dedupe/integrity checks. It also **discovers new papers** on a topic across many
  scholarly search APIs (OpenAlex, Semantic Scholar, Europe PMC, PubMed, Crossref,
  arXiv) and banks them into the library. It can also analyze the **citation graph**
  of the collection: find papers it's probably missing (works it cites a lot but
  doesn't contain), cluster papers into topic areas by shared references, and flag
  off-topic papers added by mistake. Use this skill whenever the user wants to
  save, file, organize, look up, or tidy research papers, build or maintain a
  bibliography or reading list, run a literature search / find papers on a topic,
  import a folder of PDFs into their paper collection, find
  duplicate papers, recover or fix metadata for scanned/untitled PDFs, search the
  contents of their papers, export citations, or mine citations to find missing /
  off-topic / topically-clustered papers — even if they don't say
  "bibliographer." Triggers include "add this paper," "save this arXiv link,"
  "what papers do I have on X," "find papers on X," "do a literature search,"
  "what's published on X," "import these PDFs," "make a bibliography,"
  "find the DOI/metadata for these PDFs," "search my papers for X," "check my
  library for duplicates," "export BibTeX," "what papers am I missing,"
  "what topics/clusters are in my library," or "are any of these papers off-topic."
  For a tree of internal scientific
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

An embedding backend is needed to **add/ingest papers** (ingest embeds their text)
and to run **semantic `bib query`**. It is **not** needed to read or full-text
search a library that already exists: `text`, `list`, `search`, `show`, `export`,
`dedupe`, `check`, `audit`, `gaps`, `outliers` — and the scientist grounding
readers — open the store **FTS-only** (no embedder) and work with no key or local
model configured. Only a *writable* command (`add`/`import`/`fetch`/…) errors when
no backend exists, and only `bib query` degrades: with no usable embedder it prints
a loud `[FTS-only]` warning to stderr and runs a keyword (BM25) search instead of
silently pretending the results are semantic. Put keys in `~/.env` (the tool loads
it automatically; see `.env.example`):

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
  library and refuses to open it for **writes** with a different one (mixing models
  in one vector space is silent corruption). If you change `BIBLIOGRAPHER_EMBEDDING` /
  `BIBLIOGRAPHER_EMBED_MODEL`, a write gives a clear error telling you to match the
  original or set `BIBLIOGRAPHER_ALLOW_EMBEDDER_MISMATCH=1` (only when you *know*
  the two are vector-compatible). A read-only `bib query` whose embedder doesn't
  match the library does **not** crash — it warns loudly and falls back to FTS-only.
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

The examples below write `bib` for brevity. To get a real `bib` on your PATH so you
never type the absolute `uv run …` form, the skill ships a launcher shim at
[`bin/bib`](bin/bib) — add its `bin/` to PATH, or symlink the shim once:

```bash
export PATH="/path/to/skills/bibliographer/bin:$PATH"        # then: bib add 10.1038/nphys1170
# …or, to put just the one command on an existing PATH dir:
ln -s /path/to/skills/bibliographer/bin/bib ~/.local/bin/bib
```

The shim resolves the real script relative to itself and execs it; the script's
`#!/usr/bin/env -S uv run --script` shebang resolves dependencies on each run, so no
packaging is needed. (Symlinking the script directly still works too.) Run `bib init`
once per library before first use.

**Library home** (`--home` / `$BIBLIOGRAPHER_HOME`, default `~/.bibliographer`): you no
longer need to `source ~/.env` first. When neither `--home` nor `$BIBLIOGRAPHER_HOME` is
set, the CLI loads `~/.env` (and a cwd/repo `.env`) *before* picking the default home, so
a `BIBLIOGRAPHER_HOME=` line in `~/.env` is honoured. An explicit `--home` or an
already-set env var always wins.

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
- When a record has a DOI/PMID, `add`/`import` also stamp a
  **`metrics`** sub-dict from OpenAlex (best-effort): a `cited_by_count`,
  field-weighted citation impact + percentile, a Retraction-Watch `is_retracted`
  flag, OA status, journal-trust signals (DOAJ membership, Scopus indexing,
  impact, h-index), and an `as_of` date. `bib show` surfaces them; `--no-network`
  skips them. Citation counts drift, so they carry that `as_of` stamp and can be
  topped up later with `bib refresh` (below). See
  [references/schema.md](references/schema.md).
- `add` takes **several identifiers in one call** — `bib add <DOI> <PMID> …` —
  which is how you bank the keepers from a `discover` sweep. An already-present
  paper is skipped and reported, not an error, so sweep overlap never aborts the
  batch.

**Discovering papers on a topic.** `add` needs an identifier you already have;
`discover` is the other direction — give it a research question and it finds
candidate papers across many scholarly search APIs, merges and de-duplicates
them, and flags which are already in your library. It is a **recall pass** and
banks **nothing**: you judge the candidates (it prints each one's venue, citation
percentile, FWCI, and cross-source corroboration) and bank the keepers with
`bib add`:

```bash
bib discover "UBE3A antisense oligonucleotide Angelman syndrome"     # ranked candidates (nothing banked)
bib discover "ASO CNS biodistribution" --year-min 2018 --open-access # filter by year / OA
bib discover "transferrin receptor ASO delivery" --sources openalex,pubmed,europepmc
bib add 10.1038/s41586-020-2649-2 33301246                           # bank the keepers you judged (one call, many ids)
bib discover "ASO CNS biodistribution" --json                        # structured candidates + per-source report
```

Give it **keyword terms, not a full-sentence question** — the sources match terms,
not meaning, so a natural-language question can return junk or nothing; run a few
term variations to widen recall (see
[references/literature-search.md](references/literature-search.md)).

It fans out concurrently over **OpenAlex, Semantic Scholar, Europe PMC, PubMed,
Crossref, and arXiv** (choose a subset with `--sources`; `--limit` is per-source,
default 25). A paper surfaced by several sources is corroborated and ranks higher;
the merged record carries `found_in` (which sources), `source_count`, and
`cited_by_count` where available. **One source failing never sinks the sweep** —
its error is reported on the per-source line and the rest still merge (e.g.
Semantic Scholar 429s when keyless and busy; set `S2_API_KEY` to avoid it).

Discovery is *not* byte-reproducible (relevance engines drift, new papers appear)
— the point is a uniform, source-broad, **re-runnable** sweep: re-running shows
what's now `✓in-library` vs. still net-new, so a later run is a diff, not a fresh
guess. Discovery is wide for *recall*; **what you bank is a narrower, deliberate
choice** — a candidate earns a place only if it's *responsive* to the task at hand
or *germane to the program overall and highly ranked* (top venue / highly cited /
leading group), never just on-topic-by-keyword, because every marginal paper
banked dilutes future search. `discover` can't make that call (it has only keyword
relevance and the rank signals it prints); you judge the candidates and bank the
keepers with `bib add`. See [references/literature-search.md](references/literature-search.md)
step 3. Banking creates fast citation-only **stubs** (abstract still searchable);
`bib fetch` pulls the full text when you need it.

For a *thorough* review — decomposing a question into sub-topics, sweeping each
broadly, reaching for other sources when the six-source backbone misses, and
recording the sweep so it re-runs as a diff — follow the standard pattern in
[references/literature-search.md](references/literature-search.md).

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
bib text shao2021antisense               # a bounded excerpt of the stored text (default, token-safe)
bib text shao2021antisense --offset 8000 # page to a later window (each ~4000 chars by default)
bib text shao2021antisense --chars 1000  # a smaller window from --offset
bib text shao2021antisense --all         # the ENTIRE stored text (opt-in; ~20k tokens for a full paper)
bib text shao2021antisense --all | grep -in "knockdown"   # locate a phrase (note goes to stderr)
```

Use `search` for fast metadata lookup; use `query` when the user wants to find
*passages/concepts inside* the papers (it embeds the query and runs libkit's
hybrid vector + BM25 search). With no usable embedder, `query` falls back to
BM25-only and says so loudly (`[FTS-only]` on stdout, a warning on stderr, and
`"mode":"fts","semantic":false` in `--json` — whose results are under a `results`
key); it never returns keyword matches dressed up as semantic ones.

`bib text` prints one paper's **stored library text** — the exact string a scientist
`[lit:]` quote-check reads (`source(citekey, quote=...)`). Use it to pick a real
verbatim phrase before authoring a literature claim, instead of guessing and re-running
the grounding pytest. **By default it prints a bounded excerpt** (~4000 chars) so a
naive call never dumps a whole paper (~20k tokens) into context; the **stderr** size
note reports the full length and flags when more remains. `--offset`/`--chars` page
through it; `--all` prints the whole text (the clean-pipe path, e.g. `bib text K --all |
grep`); `--json` returns the window plus `content_total`. A citation-only **stub** has
no full body — `bib text` prints its metadata + abstract and flags that quotes can only
come from the abstract. Caveat: `bib text … | grep` is a *coarse locator*, not the
verdict — shell `grep` does not fold unicode dashes / markdown emphasis / split
whitespace the way the quote-check does, so a grep miss is not authoritative;
`source(... quote=...)` stays the authority.

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

**Backfilling full text for citation-only stubs.** A library grown by banking
sweep keepers (`bib add` without a PDF) accumulates **stubs** — abstract
searchable, no full text. `bib
backfill` is the bulk counterpart to `fetch`: it finds every stub, runs the
keyless open-access ladder over each, attaches each PDF it finds, and prints a
**worklist** of the stubs that have no OA copy (citekey, identifiers, a resolvable
URL). That worklist is the interactive part — escalate each via the browser
(institutional access) or, only with the user's explicit authorization, a peer
source, then `bib fetch <ck> --pdf <file>`. `backfill` does the mechanical OA
sweep but never drives the browser or chooses a peer source on its own.

```bash
bib backfill                  # attach OA PDFs to all stubs; list the rest for manual fetch
bib backfill --dry-run        # list the stubs that would be attempted; fetch nothing
bib backfill --tag topic:aso  # only stubs carrying this tag   (--limit N to cap the run)
```

See [references/getting-pdfs.md](references/getting-pdfs.md) for the escalation
ladder the worklist feeds into.

**Backfilling / refreshing citation metrics.** Records added before metrics
existed — or where the OpenAlex lookup hit a network blip — have no `cited_by_count`
/ FWCI. `bib refresh` fills those gaps: it finds every record with a DOI/PMID but
no `metrics` block and stamps each from OpenAlex (with an `as_of` date). Because
citation counts drift, `--stale DAYS` also re-pulls metrics older than DAYS and
`--all` re-pulls everything; both bypass the 30-day resolver cache. OpenAlex is
queried one record at a time behind a polite-pool throttle, so a sweep is gentle;
`--limit` (default 500) caps a run and re-runs skip finished records, so a large
library can be filled in chunks. Useful before a literature review, where
most-cited sources carry weight (like FWCI).

```bash
bib refresh                   # backfill metrics for records that have none
bib refresh --dry-run         # list what would be fetched; change nothing
bib refresh --stale 180       # also re-pull metrics older than 180 days
bib refresh --all --limit 0   # re-pull every eligible record, no cap
bib refresh --tag topic:aso   # only records carrying this tag
```

**Citation-graph analysis (gaps, clusters, off-topic papers).** Beyond per-paper
metrics, bibliographer can use the **citation graph** — which paper cites which
work — to surface things no metadata field can. First backfill each paper's
**outgoing reference list** (the OpenAlex ids of the works it cites) with `bib
refs`; then three read-only analyses run off it:

```bash
bib refs                       # backfill references (citation edges) for all eligible records
bib refs --dry-run             # list what would be fetched; change nothing  (--tag/--limit to scope)
bib gaps                       # works your library cites a lot but doesn't contain (candidates to add)
bib gaps --min-citing 3 --json # only works cited by ≥3 of your papers, structured
bib cluster                    # group papers into topic areas by shared references
bib cluster --write-tags       # record each cluster as a `cluster:<n>` tag
bib outliers                   # flag possibly off-topic papers (citation-isolated from the library)
```

`refs` is a **one-time backfill** (a published paper's reference list is static)
and gentle on OpenAlex. `gaps` ranks external works by how many distinct library
papers cite them — judge each and bank keepers with `bib add` (high citing-count
means *central to what you have*, not automatically *worth adding*). `cluster`
groups papers by **bibliographic coupling** (citing the same sources), a citation
signal independent of the text-embedding topics from `bib query`. `outliers`
flags papers that share (almost) no references with the rest of the library *and*
neither cite nor are cited by it — a worklist of likely mistaken inclusions; it
removes nothing. **Run `bib refresh` before `bib refs`** so every paper has an
`openalex_id` (the node id that lets edges connect back into the library). See
[references/citation-analysis.md](references/citation-analysis.md).

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

`list`, `search`, `show`, `add`, `import`, `enrich`, `query`, `discover`,
`backfill`, `refresh`, `refs`, `gaps`, `cluster`, `outliers`, `dedupe`, `check`,
and `audit` take `--json`. Prefer it when
you need to parse results, count, or feed another step. `discover --json` emits
`{"results": [...], "sources": {name: count|error}, "added": {...}}`; `backfill
--json` emits `{"checked": N, "fetched": [...], "remaining": [...]}`; `refresh
--json` emits `{"checked": N, "updated": [...], "failed": [...], "remaining": N, "ineligible": N}`.
`refs --json` mirrors `refresh` (`{"checked", "updated", "failed", "remaining", "ineligible"}`);
`gaps --json` emits `{"candidates": [...], "min_citing": N}`; `cluster --json` emits
`{"clusters": [...], "unclustered": [...], "min_shared": N, "tags_written": N|null}`;
`outliers --json` emits `{"checked": N, "isolated": [...], "no_references": N, "min_shared": N}`.

## Two ways to call it: CLI or Python — your choice

There are two equivalent surfaces; pick per task.

- **CLI** (`bib …`, or `bib … --json`) — best for a **one-shot** lookup or action.
  One line, zero install. The reflex of piping the human table to `grep`/`head` is
  usually a sign you wanted `--json` instead — reach for that first.
- **Python** (`from bibliographer import BiblioStore`) — best for **composition**:
  loops/joins over many records, or feeding results into other code. Each `bib`
  call is a fresh subprocess that cold-starts libkit; a Python session pays that
  once and then calls the store directly. `BiblioStore` **is** the structured API —
  its methods return the same dicts/lists the CLI prints under `--json`.

```python
import asyncio
from pathlib import Path
from bibliographer import BiblioStore   # see bibliographer/__init__.py

async def main():
    # read_only lets many readers run concurrently (no write lock).
    # Pass want_semantic=True only if you'll call store.query() (it builds an
    # embedder); plain reads below open FTS-only and need no backend.
    store = BiblioStore.open(Path.home() / ".bibliographer", read_only=True,
                             want_semantic=True)
    try:
        recs = await store.all_records()                  # list[dict], no embedder
        hits = await store.query("ube3a dosage", limit=8) # semantic (or FTS-only if
                                                          # store.semantic_available is False)
        rec  = await store.get_by_citekey("ni2016reciprocal")
        # …compose in-process instead of `bib show … | grep`
    finally:
        await store.close()

asyncio.run(main())
```

Run that Python in an environment that has the package (e.g.
`uv run --with-editable /path/to/skills/bibliographer python3 your_script.py`). A
semantic `store.query()` additionally needs libkit's embedding backend — the same
one the CLI uses (see *Setup* above); pure reads do not. Honor
`BIBLIOGRAPHER_HOME`/`BIBLIOGRAPHER_EMBEDDING` exactly as the CLI does. For a
single lookup, don't bother — just call `bib … --json`.

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
  refuse a *write*. (A read-only `bib query` with a mismatched embedder degrades to
  FTS-only with a warning rather than failing — reads never need the embedder.)
- **Reads don't need an embedder.** Only `add`/`import`/`fetch` (which embed) and
  semantic `bib query` need a backend; every read opens FTS-only via libkit's
  `Library.open_reader`. If a read ever fails with "needs a local model" / "needs
  DEEPINFRA_API_KEY", that's a regression — opening for a read must never construct
  an embedder. (This is why the reader path uses `open_reader`, not `open`.)
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

For the standard pattern for running a broad, intentional, re-runnable literature
sweep with `bib discover` (sub-topic decomposition, banking, other-source
latitude), see [references/literature-search.md](references/literature-search.md).
For getting a PDF when there's no open-access copy (preprint servers, PMC, the
institutional browser, and authorized peer sources), see
[references/getting-pdfs.md](references/getting-pdfs.md). For using the citation
graph to find missing / off-topic / clustered papers (`refs`/`gaps`/`cluster`/
`outliers`), see
[references/citation-analysis.md](references/citation-analysis.md). For the metadata model
and the libkit mapping, see
[references/schema.md](references/schema.md). For how bibliographer uses libkit
as its store (embedding, search, caching, the warm-cache migration), see
[references/libkit-integration.md](references/libkit-integration.md). For the
periodic correctness/hygiene procedure (and the parallel-agent audit), see
[references/auditing.md](references/auditing.md).
