# Citation analysis: gaps, clusters, and outliers

Bibliographer can use the **citation graph** of your library — which paper cites
which work — to do three things no metadata field can:

1. **`bib gaps`** — find papers you're probably missing: works your library
   cites a lot but doesn't contain.
2. **`bib cluster`** — infer topic areas by grouping papers that cite the same
   sources (bibliographic coupling).
3. **`bib outliers`** — flag papers that may be off-topic / added by mistake,
   because they're citation-isolated from the rest of the library.

All three read one piece of data: each paper's **outgoing reference list**,
stored in `metadata['references']` as OpenAlex work ids. Populate it first.

## Step 0 — backfill the graph (`bib refs`)

```bash
bib refs                      # fetch references for every record with a DOI/PMID/OpenAlex id
bib refs --dry-run            # list what would be fetched; change nothing
bib refs --tag topic:aso      # only records carrying this tag   (--limit N caps a run)
bib refs vaswani2017attention # only these citekeys
bib refs --all                # re-pull references past the cache (rarely needed)
```

`refs` looks each work up in OpenAlex (by its OpenAlex id when known, else DOI,
else PMID) and stores `referenced_works` as `references`, plus the paper's own
`openalex_id`. A published paper's reference list is **static**, so this is a
one-time backfill — re-runs skip records that already have it, and `--all` only
matters if OpenAlex has since corrected a record. It's gentle (one OpenAlex call
per record behind the polite-pool throttle) and fully cached.

**Run `bib refresh` first if you can.** `refresh` stamps `metrics.openalex_id` on
every record; that id is what lets `references` edges connect *back into* the
library (intra-library edges), which `cluster` and `outliers` depend on. `refs`
backfills a missing id opportunistically, but a library where every paper already
has an OpenAlex id gives the sharpest graph.

## 1. Missed papers (`bib gaps`)

```bash
bib gaps                      # works cited by ≥2 of your papers but not in the library
bib gaps --min-citing 3       # raise the bar (cited by ≥3)
bib gaps --tag topic:aso      # only count citations from papers carrying this tag
bib gaps --limit 0 --json     # all candidates, structured
bib gaps --no-network         # skip OpenAlex labelling; print bare work ids
```

Ranks external works by **how many distinct library papers cite them**. The
ranking is offline (pure counting over stored `references`); only the
title/year/citation **labels** for the top candidates touch the network. Output
lists each candidate with its citing-count and which of your papers cite it, so
you can judge it and bank the keepers:

```
[5×] (2019) Adeno-associated virus delivery of CRISPR … · cited-by 1240
      cited by: chen2021aso, li2020delivery, …  doi:10.1038/s41587-019-...
```

Judge before banking — a high citing-count means *central to what you already
have*, not necessarily *worth adding*. Bank keepers with `bib add <doi-or-id>`
(it mints a citation-only stub; `bib refs` then extends the graph to it too).

## 2. Topic clustering (`bib cluster`)

```bash
bib cluster                   # report clusters (shared-reference groups), change nothing
bib cluster --min-shared 3    # stricter coupling (≥3 shared references to link)
bib cluster --write-tags      # record each cluster as a `cluster:<n>` tag
bib cluster --json
```

Two papers are **coupled** when they cite some of the same works; papers linked
by ≥`--min-shared` shared references (transitively) form a cluster. Clusters are
numbered by size (`cluster:1` is the largest) and the numbering is deterministic,
so `--write-tags` is stable across runs (it replaces any prior `cluster:*` tags).
Papers that couple with nothing are reported as *unclustered*.

Coupling is a **citation** signal, independent of the text-embedding topics you
get from `bib query`/the viewer's topic facet — the two corroborate each other.
A future enhancement is to fuse them; today `cluster` is purely citation-based.

## 3. Off-topic detection (`bib outliers`)

```bash
bib outliers                  # papers citation-isolated from the rest of the library
bib outliers --min-shared 2   # coupling below this counts as isolated (default 2)
bib outliers --tag topic:aso
bib outliers --json --all     # include the full per-paper report, not just the flagged
```

Flags a paper when it (a) shares fewer than `--min-shared` references with **any**
other library paper, **and** (b) neither cites nor is cited by any paper in the
library. Such a paper sits alone in the citation graph — a strong hint it was
filed by mistake or belongs to a different collection. It's a **worklist**:
`outliers` removes nothing; review each, then `bib rm` if it truly doesn't
belong. Papers with no fetched references are reported as unknown (`bib refs`
them to include them), never flagged.

## How it works (for agents extending this)

- The graph primitive is `metadata['references']` (OpenAlex work ids) +
  `openalex_id` per paper — see [schema.md](schema.md).
- The resolvers (`enrich_references`, `fetch_openalex_references`,
  `fetch_openalex_works`) live in `bibliographer/resolvers.py`, alongside the
  OpenAlex metrics enrichment they mirror.
- The analytics are **pure** functions in `bibliographer/citations.py`
  (`gap_candidates`, `coupling_clusters`, `isolation_report`) — no network, no
  libkit — and are unit-tested offline in `tests/test_citations.py`. Add a test
  there when you change the algorithms.
- Coupling is computed via an inverted index (work id → citing papers), not an
  O(papers²) scan, so it stays cheap on large libraries.

### Coverage caveats

- OpenAlex's `referenced_works` is good but not complete, especially for older
  works and some preprints; a paper with no OpenAlex match gets no references and
  is reported `✗ no OpenAlex match` by `bib refs`.
- A reference that points at a library paper is only recognised as intra-library
  if that paper has an `openalex_id`. Run `bib refresh` (and/or `bib refs`) so
  every paper has one; otherwise `cluster`/`outliers` slightly undercount
  intra-library edges (they still couple correctly via shared *external* refs).
