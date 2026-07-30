---
name: bibliographer
description: >-
  Manage a personal collection of academic articles — add papers from a DOI,
  arXiv ID, PMID/PMCID, Semantic Scholar ID, or a PDF (auto-fetching metadata
  from Crossref/arXiv/PubMed/Semantic Scholar), file PDFs into a human-readable
  author tree, bulk-import a folder, search and browse the library, run semantic
  search inside the papers, generate BibTeX, and run dedupe/integrity checks. It
  also **discovers new papers** on a topic across many scholarly search APIs
  (OpenAlex, Semantic Scholar, Europe PMC, PubMed, Crossref, arXiv) and banks
  them, and analyzes the **citation graph** to find missing, off-topic, or
  topically-clustered papers. Use it whenever the user wants to save, file,
  organize, look up, or tidy research papers, build a bibliography or reading
  list, run a literature search, recover metadata for scanned/untitled PDFs,
  search inside their papers, export citations, or mine citations — even if they
  don't say "bibliographer." Triggers include "add this paper," "save this arXiv
  link," "what papers do I have on X," "find papers on X," "do a literature
  search," "import these PDFs," "make a bibliography," "search my papers for X,"
  "check for duplicates," "export BibTeX," "what papers am I missing," or "are any
  of these papers off-topic." For a tree of internal scientific experiments (raw
  lab/CRO data, extracted measurements, analysis, grounded claims), use the
  scientist skill instead.
---

# Bibliographer

Bibliographer manages a collection of academic articles: it fetches
bibliographic metadata from public sources, organizes the PDFs on disk in a
human-readable tree, and stores everything in a **libkit** library that also
gives you semantic + full-text search over the papers' contents. All of this is
driven by one bundled command-line tool, `scripts/bib.py`.

## The store: libkit (no separate database)

libkit **is** the store — there's no separate bibliographer database. Each paper is
one libkit *document*; every bibliographic field (DOI, authors, venue, tags,
citekey, file path, …) lives in that document's `metadata`. Bibliographer adds what
libkit doesn't: metadata **resolvers**, the on-disk **author tree**, and paper-level
**identity** (citekeys + dedup by DOI/arXiv/PMCID, over libkit's byte identity).
Requires **libkit ≥ 0.2.2**. Full data model →
[references/schema.md](references/schema.md); how the store works (embedding,
search, caching, the disk-bloat/`compact` story) →
[references/libkit-integration.md](references/libkit-integration.md).

A **library** is a directory (default `~/.bibliographer`, override with
`--home`/`BIBLIOGRAPHER_HOME`) holding `catalog.duckdb` (the libkit store), `papers/`
(the organized originals, `papers/<First Author, Given>/<Authors> (<Year>) -
<Title>.pdf`), and `index.html` (a self-contained browser viewer regenerated on
every change). Each article has a **citekey** (e.g. `vaswani2017attention`) — the
stable handle for `show`/`tag`/`rm`/`export`. A paper with no file yet is a
citation-only **stub** (still searchable, full metadata), upgraded to `full` when a
PDF arrives.

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

Two more things, both detailed in
[references/libkit-integration.md](references/libkit-integration.md): libkit
**enforces embedder identity** — changing `BIBLIOGRAPHER_EMBEDDING`/`_EMBED_MODEL`
makes a *write* fail with a clear error (match the original, or set
`BIBLIOGRAPHER_ALLOW_EMBEDDER_MISMATCH=1` when you *know* they're vector-compatible;
a read-only `bib query` just warns and falls back to FTS-only). And libkit's
content-addressed **cache** keys parses by file+loader and embeddings by
embedder+chunk, so re-ingesting a document — or one already processed by another
libkit tool — skips the expensive work (`LIBKIT_CACHE_DIR` relocates it).

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

## Commands

Every command is `bib <verb>`; the full per-command usage — examples, flags, the
`--json` shapes, and the Python (`BiblioStore`) surface — lives in
[references/commands.md](references/commands.md). Deep topics each have their own
reference (last column). Run `bib init` once per library before first use.

| Verb(s) | What it does | Detail |
|---|---|---|
| `add` | add a paper by DOI/arXiv/PMID/PMCID/S2/URL/PDF (auto-fetches an OA PDF, refuses duplicates, many ids per call) | [commands](references/commands.md) · [getting-pdfs](references/getting-pdfs.md) |
| `discover` | find candidate papers on a topic across 6 search APIs — a recall pass that **banks nothing**; you judge + `bib add` the keepers | [literature-search](references/literature-search.md) |
| `import` | bulk-import a folder into the author tree (**dry-run first**; moves by default) | [commands](references/commands.md) |
| `enrich` | recover metadata for unverified scans / junk-filename records (content-verified) | [commands](references/commands.md) · [schema](references/schema.md) |
| `list` `search` `query` `show` `text` | browse · **literal-substring** metadata match (feed it ONE word) · semantic-search *inside* papers · show a record · dump stored text | [commands](references/commands.md) |
| `tag` `rm` `export` `viewer` | tag · remove · BibTeX export · (re)build the HTML viewer | [commands](references/commands.md) |
| `fetch` `backfill` | get a PDF for one stub · bulk-attach OA PDFs to all stubs + worklist the rest | [getting-pdfs](references/getting-pdfs.md) |
| `refresh` | backfill / refresh OpenAlex citation metrics | [commands](references/commands.md) |
| `refs` `gaps` `cluster` `outliers` | citation graph: backfill edges, then find missing / clustered / off-topic papers | [citation-analysis](references/citation-analysis.md) |
| `dedupe` `check` `audit` | report-only hygiene: duplicate groups · integrity · misfiling + content-mismatch worklist | [auditing](references/auditing.md) |
| `compact` | rewrite `catalog.duckdb` to reclaim disk bloat (refuses while a writer is active) | [libkit-integration](references/libkit-integration.md) |

Most commands take **`--json`** — prefer it over grepping the human table when you
need to parse, count, or feed another step. For composition over many records, the
Python API (`from bibliographer import BiblioStore`) avoids the per-call libkit
cold-start; for a one-shot lookup, just call `bib … --json`. Both, with examples,
are in [references/commands.md](references/commands.md).

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

- **`bib search` is a substring matcher, not a search engine.** Put what you know in
  the field that holds it — `--author <surname>`, `--year`, `--tag` — and give the
  free-text query ONE distinctive word; a natural-language sentence misses even when
  the paper is right there. **Never conclude a paper is absent from a zero-result
  search** (an agent once did, and told the user papers were missing that were in the
  collection): a zero prints its findings to stderr — read them — and establishing real
  absence takes the three steps in
  [commands](references/commands.md#establishing-that-a-paper-is-not-in-the-library).
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

## References

- [commands.md](references/commands.md) — full per-command usage: examples, flags, `--json` shapes, the Python (`BiblioStore`) surface.
- [literature-search.md](references/literature-search.md) — the `bib discover` sweep method: sub-topic decomposition, selective banking, other-source latitude, re-runnable diffs.
- [getting-pdfs.md](references/getting-pdfs.md) — the PDF-fetch ladder when there's no open-access copy (preprint servers, PMC, institutional browser, authorized peer sources).
- [citation-analysis.md](references/citation-analysis.md) — the citation graph: `refs`/`gaps`/`cluster`/`outliers` to find missing / clustered / off-topic papers.
- [schema.md](references/schema.md) — the metadata model and the libkit document mapping.
- [libkit-integration.md](references/libkit-integration.md) — how the store works: embedding, search, caching, and the disk-bloat/`compact` story.
- [auditing.md](references/auditing.md) — the periodic correctness/hygiene procedure, including the parallel-agent content audit.
