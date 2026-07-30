# Command reference (per-command usage)

The full `bib` command surface, grouped by job. The skill body
([SKILL.md](../SKILL.md)) carries only the inventory; this is the detail. Deep
topics (the PDF-fetch ladder, the literature-sweep method, the citation graph,
the audit procedure, the store internals) each have their own reference, linked
from the relevant command below. Every command writes `bib` for brevity — see
[SKILL.md](../SKILL.md) ("Running the tool") for the real `uv run …` form.

## Adding a paper — `add`

The most common task. Give it a DOI, arXiv id, PMID, PMCID, Semantic Scholar id,
an arxiv.org/doi.org URL, or a PDF path — `add` figures out which:

```bash
bib add 10.1038/nphys1170                  # DOI       -> Crossref
bib add arXiv:1706.03762                    # arXiv id  -> arXiv API
bib add PMID:8627575                        # PMID      -> Crossref/PubMed (DOI optional)
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
  skip. For a stub that had no OA copy, `bib fetch <citekey>` retries those sources
  later. See [getting-pdfs.md](getting-pdfs.md) for the full ladder.
- For a local file, `add` **copies** it into the author tree (`--move` to move
  instead) and tries to recover an identifier from the PDF; if nothing resolves
  it falls back to the PDF's embedded metadata and marks the record unverified —
  tell the user, and consider supplying the DOI/arXiv id to enrich it.
- When a record has a DOI/PMID, `add`/`import` also stamp a **`metrics`** sub-dict
  from OpenAlex (best-effort): `cited_by_count`, field-weighted citation impact +
  percentile, a Retraction-Watch `is_retracted` flag, OA status, journal-trust
  signals, and an `as_of` date. `bib show` surfaces them; `--no-network` skips
  them. Counts drift, so they carry the `as_of` stamp and can be topped up with
  `bib refresh`. See [schema.md](schema.md).
- `add` takes **several identifiers in one call** — `bib add <DOI> <PMID> …` —
  which is how you bank the keepers from a `discover` sweep. An already-present
  paper is skipped and reported, not an error, so sweep overlap never aborts the
  batch — and the same holds for a **transient resolve failure**: one id that
  errors (e.g. a Semantic Scholar 429) is reported on its own line and the rest
  of the batch is still banked. The end-of-run summary counts `… N failed`, and
  `--json` lists each failure as a `{"status": "error", "identifier", "error"}`
  row alongside the added/duplicate rows.
- A **PMID/PMCID resolves even without a DOI**: `add` tries DOI→Crossref first,
  then PubMed directly (NCBI ESummary for title/authors/year/venue, EFetch for
  the abstract), and only then Semantic Scholar. So a DOI-less PubMed paper banks
  from PubMed's own metadata and never hard-depends on S2's throttled
  `/paper/{id}` lookup endpoint.

## Discovering papers on a topic — `discover`

`add` needs an identifier you already have; `discover` is the other direction —
give it keyword terms and it finds candidate papers across six scholarly search
APIs, merges and de-duplicates them, and flags which are already in your library.
It is a **recall pass** and banks **nothing**: you judge the candidates and bank
the keepers with `bib add`.

```bash
bib discover "UBE3A antisense oligonucleotide Angelman syndrome"     # ranked candidates (nothing banked)
bib discover "ASO CNS biodistribution" --year-min 2018 --open-access # filter by year / OA
bib discover "transferrin receptor ASO delivery" --sources openalex,pubmed,europepmc
bib add 10.1038/s41586-020-2649-2 33301246                           # bank the keepers you judged (one call, many ids)
bib discover "ASO CNS biodistribution" --json                        # structured candidates + per-source report
```

Give it **keyword terms, not a full-sentence question** — the sources match terms,
not meaning. It fans out concurrently over **OpenAlex, Semantic Scholar, Europe
PMC, PubMed, Crossref, and arXiv** (`--sources` to subset; `--limit` is per-source,
default 25); one source failing never sinks the sweep. Discovery is wide for
*recall*; **what you bank is a narrower, deliberate choice** — bank a candidate
only if it's *responsive to the task* or *germane to the program and highly ranked*,
never just on-topic-by-keyword. Re-running a sweep is a diff (`✓in-library` vs
net-new), not a fresh guess.

The full method — decomposing a question into sub-topics, sweeping each broadly,
reaching for other sources when the six-source backbone misses, banking
selectively, and recording the sweep so it re-runs — is in
[literature-search.md](literature-search.md).

## Bulk-importing a folder — `import`

Always dry-run first:

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

## Recovering metadata for unverified records — `enrich`

Files with no extractable identifier (old scans, junk filenames) land as
*unverified* records — filed under `papers/Unknown/` with an `anon…` citekey.
`enrich` recovers their real metadata:

```bash
bib enrich --dry-run                         # show proposed matches for all unverified, change nothing
bib enrich                                   # auto-apply verified matches, list the rest to review
bib enrich vaswani2017attention --doi 10.x/y # force a specific id for one record (also accepts s2:<id> / pmid:<id>)
```

For each unverified record `enrich` builds a Crossref query from the original
`author_year_title` filename, then **verifies the candidate against the document's
actual parsed content** before applying — essential because **filenames in a pile
can lie** (a file named `…punt_2022…` may actually contain a different paper). A
verified match updates the record, regenerates the citekey, and re-files the PDF
(`--no-refile` to skip the move). Anything that doesn't verify is listed for you to
resolve with `--doi`. Documents that aren't journal articles (GeneReviews,
StatPearls, technical bulletins, supplements) won't resolve — tag them by kind
instead, e.g. `bib tag <citekey> --add type:genereview`. See [schema.md](schema.md)
and the parallel-agent backstop in [auditing.md](auditing.md).

## Finding things — `list` / `search` / `query` / `show` / `text`

**Choosing between them: ask what you KNOW, not what you want.**

- **Something printed on the card** — a surname, year, venue, tag, DOI, or a word from
  the title → **`search`**. Prefer the *field* that holds it (`--author` / `--year` /
  `--tag`) over the free-text query, which is only a substring match over everything
  at once. A surname belongs in `--author`, not in a sentence.
- **Something the paper *says*** — an argument, a method, a measured result →
  **`query`**. It reads the papers' text; `search` cannot see inside them at all.
- **"Is this specific paper in the library?"** → *neither, on its own.* That is a
  presence question, and one call cannot answer it — see
  [Establishing that a paper is NOT in the library](#establishing-that-a-paper-is-not-in-the-library).

```bash
bib list                       # everything
bib search transformer         # LITERAL SUBSTRING of the record's metadata
bib search --author hinton --year 2015
bib search --tag topic:nlp
bib search 10.1002/aur.1284    # identifiers and citekeys are matchable too
bib query "why do transformers scale"   # SEMANTIC + full-text search INSIDE the papers (libkit)
bib show vaswani2017attention            # full record
bib show vaswani2017attention --bibtex   # one BibTeX entry
bib text shao2021antisense               # a bounded excerpt of the stored text (default, token-safe)
bib text shao2021antisense --offset 8000 # page to a later window (each ~4000 chars by default)
bib text shao2021antisense --chars 1000  # a smaller window from --offset
bib text shao2021antisense --all         # the ENTIRE stored text (opt-in; ~20k tokens for a full paper)
bib text shao2021antisense --all | grep -in "knockdown"   # locate a phrase (note goes to stderr)
```

### `bib search` is a substring matcher — feed it ONE word

**The commonest mistake is not phrasing — it is skipping the field.** The incident
below began with a surname handed to the free-text query instead of to `--author`:

```bash
bib search 'Urraca interstitial duplication characteristic EEG'   # ✗ a sentence, at a substring matcher
bib search --author urraca                                        # ✓ the surname, in the surname field
```

`search` is **not** a search engine and **not** semantic. It lowercases your query and
tests it as **one literal substring** of each record's `title + authors + venue +
abstract`, then its `citekey` and identifiers (DOI/arXiv/PMID/…), then its `tags` —
all concatenated into one blob. So a multi-word query only hits when those words
appear **verbatim and adjacent** in that blob:

```bash
bib search Urraca                             # ✓ phrase hit — one distinctive word
bib search 'Characteristic EEG Signature'     # ✓ phrase hit — verbatim + adjacent in the title
bib search 'EEG Signature Urraca'             # ✓ phrase hit (!) — spans the title→authors boundary
bib search 'Urraca interstitial EEG'          # ~ phrase MISSES; the relaxed retry finds it
bib search 'distinctive brainwave pattern'    # ✗ paraphrase — nothing here is semantic
```

When the literal phrase misses, `search` **retries as all-words-anywhere** and warns
on stderr that it relaxed. That rescues most natural-language queries — but it is
still an AND over substrings, so one absent or paraphrased word zeroes the query.
**Query with a single distinctive token** — a surname, a gene, an unusual noun — then
narrow with `--author` / `--year` / `--tag`. Note that citation-only stubs and records
with no fetched abstract expose only a title/authors/venue/tags, so most of a paper's
*content* is unmatchable by `search` at all; that is what `query` is for.

**Never conclude a paper is absent from a zero-result `search`.** A phrasing miss says
nothing about the library — this exact mistake once had an agent report papers missing
that were in the collection all along. So a zero is never bare: `search` resolves your
**rarest matching word to actual records and shows them**, so you usually recognise the
paper without searching again:

```
$ bib search 'Urraca interstitial duplication characteristic EEG zebrafish'
warning: no match for '…' — this is NOT evidence the paper is absent (searched 1790 record(s)).
  closest single word 'urraca' matches 3 record(s) — is one of these yours?
    [urraca2013interstitial] (2013) The Interstitial Duplication 15q11.2-q13 Syndrome Includes…
  words that DO match records: urraca (3), interstitial (24), duplication (140), eeg (61)
  words matching nothing: zebrafish
```

It also reports each word's own record count (words that hit plenty prove the *phrasing*
failed; only words matching nothing are weak evidence of absence) and, when a query
matched but `--author`/`--tag`/`--year` emptied it, says so — that is a filter problem,
not a wording one. Before reporting anything missing: retry the rarest word alone, and
check `bib query`.

### Establishing that a paper is NOT in the library

A zero-result `search` is **never** sufficient. Presence is a different question from
retrieval, and answering it takes three steps against three different surfaces — a
paper invisible to one is often plainly visible to the next:

1. **Field-scoped `search`.** `bib search --author <surname>` (add `--year` if the
   surname is common). This is the step the incident skipped, and on its own it would
   have answered the question. Free-text `search` is a substring match over one blob;
   `--author` tests the authors field alone, so word order and phrasing cannot defeat it.
2. **`query` on a distinctive title phrase or the paper's actual subject.** `search`
   sees only the catalog card; `query` reads the papers' text, so it reaches everything
   the card omits. Raise `--limit` (default 8) when you are trying to prove a negative —
   a truncated top-N is not an exhaustive answer.
3. **Identifier lookup, if you have one.** `bib search <doi>` / `<arxiv id>` / `<pmid>` /
   `<citekey>` — all are matchable. This is the **only exact test**: identifiers are
   assigned, not phrased, so a genuine identifier miss is real evidence of absence in a
   way that no wording-based miss ever is.

Only after all three may you write "not in the library" — and **say which steps you
ran**, so a reader can tell a searched-and-absent claim from an unsearched one. "No
paper by Urraca is in the collection" is a *negative claim*, and it needs its evidence
attached like any other. If you ran one search and got a zero, what you know is that
your phrasing missed; nothing more.

`--json` returns an **envelope**, not a bare list, because an empty `[]` reads as absence
to whatever parses it: `{query, filters, matched_by, searched, count, results}` — where
`matched_by` is `"phrase"`, `"all-words"` (the relaxed retry fired) or `null` — plus a
`diagnostic` object on a zero carrying `absence_supported: false`, `per_word`,
`closest_word` and `candidates`. **Read `matched_by` and `diagnostic`**; a zero-length
`results` on its own means nothing.

Use `search` for fast metadata lookup; use `query` when the user wants to find
*passages/concepts inside* the papers (it embeds the query and runs libkit's hybrid
vector + BM25 search). With no usable embedder, `query` falls back to BM25-only and
says so loudly (`[FTS-only]` on stdout, a warning on stderr, `"mode":"fts","semantic":false`
in `--json` — results under a `results` key); it never returns keyword matches
dressed up as semantic ones.

`bib text` prints one paper's **stored library text** — the exact string a scientist
`[lit:]` quote-check reads (`source(citekey, quote=...)`). Use it to pick a real
verbatim phrase before authoring a literature claim, instead of guessing and
re-running the grounding pytest. **By default it prints a bounded excerpt** (~4000
chars) so a naive call never dumps a whole paper (~20k tokens) into context. When the
excerpt is only the opening of a longer body, the **stderr** note says so loudly —
`FULL TEXT is stored (44,069 chars …) — showing first 4,000 of 44,069 chars; 40,069
not shown — use --all …` — so an excerpt is never mistaken for "abstract only".
`--offset`/`--chars` page through it; `--all` prints the whole text (the clean-pipe
path, e.g. `bib text K --all | grep`) and prints no truncation notice. `--json`
returns the window plus `total` / `shown` / `remaining` / `truncated` (and the
back-compat `content_total` / `content_chars`). A citation-only **stub** has no full
body — `bib text` prints its metadata + abstract and flags (`no full text ingested`)
that quotes can only come from the abstract. Caveat:
`bib text … | grep` is a *coarse locator*, not the verdict — shell `grep` does not
fold unicode dashes / markdown emphasis / split whitespace the way the quote-check
does, so a grep miss is not authoritative; `source(... quote=...)` stays the authority.

## Organizing and exporting — `tag` / `rm` / `export` / `viewer`

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

## Backfilling full text for stubs — `backfill` / `fetch`

A library grown by banking sweep keepers (`bib add` without a PDF) accumulates
**stubs** — abstract searchable, no full text. `bib backfill` is the bulk
counterpart to `fetch`: it finds every stub, runs the keyless open-access ladder
over each, attaches each PDF it finds, and prints a **worklist** of the stubs with
no OA copy (which you escalate via the browser, or — only with the user's explicit
authorization — a peer source, then `bib fetch <ck> --pdf <file>`).

```bash
bib backfill                  # attach OA PDFs to all stubs; list the rest for manual fetch
bib backfill --dry-run        # list the stubs that would be attempted; fetch nothing
bib backfill --tag topic:aso  # only stubs carrying this tag   (--limit N to cap the run)
```

The escalation ladder the worklist feeds into is in [getting-pdfs.md](getting-pdfs.md).

## Backfilling / refreshing citation metrics — `refresh`

Records added before metrics existed — or where the OpenAlex lookup hit a network
blip — have no `cited_by_count`/FWCI. `bib refresh` fills those gaps: it finds every
record with a DOI/PMID but no `metrics` block and stamps each from OpenAlex (with an
`as_of` date). Because counts drift, `--stale DAYS` re-pulls metrics older than DAYS
and `--all` re-pulls everything (both bypass the 30-day resolver cache). Useful
before a literature review, where most-cited sources carry weight.

```bash
bib refresh                   # backfill metrics for records that have none
bib refresh --dry-run         # list what would be fetched; change nothing
bib refresh --stale 180       # also re-pull metrics older than 180 days
bib refresh --all --limit 0   # re-pull every eligible record, no cap
bib refresh --tag topic:aso   # only records carrying this tag
```

OpenAlex is queried one record at a time behind a polite-pool throttle, so a sweep
is gentle; `--limit` (default 500) caps a run and re-runs skip finished records, so
a large library fills in chunks.

## Citation-graph analysis — `refs` / `gaps` / `cluster` / `outliers`

Beyond per-paper metrics, bibliographer can use the **citation graph** to surface
things no metadata field can. First backfill each paper's outgoing reference list
with `bib refs`; then three read-only analyses run off it:

```bash
bib refs                       # backfill references (citation edges) for all eligible records
bib refs --dry-run             # list what would be fetched; change nothing  (--tag/--limit to scope)
bib gaps                       # works your library cites a lot but doesn't contain (candidates to add)
bib gaps --min-citing 3 --json # only works cited by ≥3 of your papers, structured
bib cluster                    # group papers into topic areas by shared references
bib cluster --write-tags       # record each cluster as a `cluster:<n>` tag
bib outliers                   # flag possibly off-topic papers (citation-isolated from the library)
```

**Run `bib refresh` before `bib refs`** so every paper has an `openalex_id` (the
node id that lets edges connect back into the library). `gaps` ranks external works
by how many distinct library papers cite them — judge each and bank keepers with
`bib add` (high citing-count means *central to what you have*, not automatically
*worth adding*). The full procedure and the algorithm notes are in
[citation-analysis.md](citation-analysis.md).

## Keeping the library healthy — `dedupe` / `check` / `audit` / `compact`

```bash
bib dedupe     # report probable duplicate groups (review, then `bib rm`)
bib check      # missing files, changed file bytes, orphan files, citation-only/unverified records
bib audit      # deeper review: misfiling, thin metadata, content-vs-title mismatch (a worklist)
bib audit --json   # structured worklist to drive fixes (incl. a parallel-agent pass)
bib compact --dry-run   # report catalog.duckdb size + bloat estimate (change nothing)
bib compact             # reclaim that bloat by rewriting the store (rebuilds a compact HNSW/FTS index)
```

`dedupe`, `check`, and `audit` only report; they never delete. Run `audit`
periodically (especially after a big import) — see [auditing.md](auditing.md) for
the full procedure, including fanning out parallel agents to verify each document's
*content* against its stored metadata. `bib compact` rewrites `catalog.duckdb` to
reclaim the disk bloat DuckDB leaves behind (the mechanism, the measured comparison
of alternatives, and why a COPY-rewrite is the only thing that shrinks the file, are
in [libkit-integration.md](libkit-integration.md)). It refuses while a writer holds
the lock, so don't run it concurrently with `add`/`import`/`refresh`. Empty folders
under `papers/` are pruned automatically after every command.

## Machine-readable output (`--json`)

`list`, `search`, `show`, `add`, `import`, `enrich`, `query`, `discover`,
`backfill`, `refresh`, `refs`, `gaps`, `cluster`, `outliers`, `dedupe`, `check`,
`audit`, and `compact` take `--json`. Prefer it when you need to parse results,
count, or feed another step (the reflex of piping the human table to `grep`/`head`
is usually a sign you wanted `--json`). Shapes:

- `discover --json` → `{"results": [...], "sources": {name: count|error}, "added": {...}}`
- `backfill --json` → `{"checked": N, "fetched": [...], "remaining": [...]}`
- `refresh --json` → `{"checked": N, "updated": [...], "failed": [...], "remaining": N, "ineligible": N}`
- `refs --json` mirrors `refresh` (`{"checked", "updated", "failed", "remaining", "ineligible"}`)
- `gaps --json` → `{"candidates": [...], "min_citing": N}`
- `cluster --json` → `{"clusters": [...], "unclustered": [...], "min_shared": N, "tags_written": N|null}`
- `outliers --json` → `{"checked": N, "isolated": [...], "no_references": N, "min_shared": N}`
- `compact --json` → `{"size_before", "size_after", "reclaimed", "documents", "chunks", "elapsed_s", "backup", ...}`
  (for `--dry-run`: `{"size_before", "block_stats", "reclaimable_hint", "would_do", "writer_active"}`, no `size_after`)

## Calling it from Python instead of the CLI

For a **one-shot** lookup or action, the CLI (`bib … --json`) is best — one line,
zero install. For **composition** (loops/joins over many records, or feeding results
into other code), use the Python API: each `bib` call is a fresh subprocess that
cold-starts libkit, whereas a Python session pays that once and then calls the store
directly. `from bibliographer import BiblioStore` **is** the structured API — its
methods return the same dicts/lists the CLI prints under `--json`.

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
semantic `store.query()` additionally needs libkit's embedding backend; pure reads
do not. Honor `BIBLIOGRAPHER_HOME`/`BIBLIOGRAPHER_EMBEDDING` exactly as the CLI does.
For a single lookup, don't bother — just call `bib … --json`.
