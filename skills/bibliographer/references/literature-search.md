# Running a literature sweep

`bib discover` turns a research question into ranked, de-duplicated candidate
papers across six scholarly search APIs (OpenAlex, Semantic Scholar, Europe PMC,
PubMed, Crossref, arXiv). Those candidates are a **recall pass** — what you bank
from them is a separate, narrower, deliberate choice (step 3). This is the front
half of any serious literature review. The command is in [SKILL.md](../SKILL.md);
this doc is the **method**: how to run a sweep that is broad in what it surfaces,
selective in what it keeps, and re-runnable rather than a thin one-shot search.

The aim is not byte-for-byte reproducibility — relevance engines drift and new
papers appear, so two runs will differ. The aim is a **minimum standard**: a
sweep that is *broad in recall* (not just the headline question), *selectively
banked* (the keepers, not the whole sweep — see step 3), *recorded* (so it can be
re-run as a diff), and *grounded* (full text read before a finding is relied on).
A sweep that clears that bar leaves the library meaningfully smarter; one that
doesn't is citation-gathering wearing a literature review's clothes.

## The standard pattern

1. **Decompose the question into sub-topics, and sweep each.** A real review
   spans the *whole subject area*, not its one headline question. For "per-tissue
   knockdown of target X," that means separate sweeps for the dosage/over-
   expression biology, the disease-restoration dose-response, the delivery route,
   the biodistribution gradient, the clinical programs, the target's molecular
   biology — each its own `bib discover` call. Eight focused sub-topic sweeps
   surface ~100+ relevant papers; one search of the headline question surfaces a
   dozen. If your sweep returns only the handful you end up citing, it was too
   shallow — go wider.

2. **Run each sweep broadly — in both sources and phrasing.** Default to all six
   sources and a generous `--limit` (the default 25 is *per source*); narrow with
   `--sources`, `--year-min/--max`, or `--open-access` only when you have a reason
   (biomedical-only `--sources pubmed,europepmc`, recent-only, etc.). And **query
   with keywords, not sentences**: `discover` hands your string to keyword/boolean
   APIs (PubMed, OpenAlex, Crossref) that match *terms, not meaning*, so a
   natural-language question matches function words and may return junk or nothing,
   while a concise term query matches cleanly. Because no single phrasing surfaces
   everything, run **several keyword variations per sub-topic** — swap synonyms and
   adjacent terms (intrathecal / intracerebroventricular / CSF; oligonucleotide /
   ASO / oligodeoxynucleotide) and let the merge dedup the overlap. Breadth of
   *queries*, not just of sources, is how keyword search matches a semantic
   engine's recall.

3. **Bank selectively — don't bank the whole sweep.** The candidate list is a
   recall pass; banking is a separate judgment. A keyword sweep is wide on
   purpose, but most of what it surfaces is on-topic-by-coincidence, and every
   marginal paper you bank dilutes search for every future review. Bank a
   candidate **only if** one of these holds:
   - **It's responsive to the task at hand** — a real chance you'll read or cite
     it for *this* report/question. This is the working set, and it's most of
     what a focused sweep banks.
   - **It's further afield but germane to the program overall *and* carries a
     strong quality signal** — squarely within the program's subject area (its
     targets, modality, assays, indications), *and* from a top-tier venue, highly
     cited / high citation percentile, or a leading group whose work bears on the
     program. This is the durable-asset set: worth keeping for the next review
     even if this one never cites it — but it must clear the *quality* bar, not
     just the topic bar.

   On-topic-by-keyword but neither responsive nor germane-and-strong → leave it as
   a discovery result; don't bank it. **You** make this call, not `discover`: the
   command has no model of the task or the program — only keyword relevance and
   the rank signals (venue, citation percentile, cross-source corroboration) it
   reports per candidate. Bank the keepers explicitly with `bib add <DOI|PMID>`
   once you've judged them against the two bars; each lands as a citation-only
   **stub** (abstract stays searchable; `bib fetch` pulls the PDF when you need
   the full text). Citations remain a subset of what you bank — but what you bank
   is a subset of the sweep.

4. **Let the merge do the dedup; let the re-run be a diff.** Cross-source
   duplicates collapse automatically (DOI → PMID → arXiv → title+year), and a
   paper found by several sources ranks higher (corroboration — see `found_in`
   / `source_count`). Re-running a sweep later flags what's now `✓in-library`
   versus still net-new, so you extend coverage instead of re-reading the same
   hits.

5. **Record what you swept.** Note the queries, the sources, any filters, and the
   date, wherever the work's provenance lives (for a report, in its generation
   brief). This is what makes the sweep re-runnable and lets a later reader see
   what ground was — and wasn't — covered. (A durable, first-class "review
   object" that captures this automatically is planned; until then, record it by
   hand.)

6. **Read before you rely.** `discover` returns candidates and abstracts, not
   verified findings. Before a paper becomes load-bearing, pull its full text
   (`bib fetch <citekey>`, then `bib query`) and confirm it actually says what
   you're attributing to it. Abstract-only is fine for *background banking*, not
   for a claim you're standing on.

## Reach for other sources when the backbone misses

`discover`'s six sources are the reliable, keyless **backbone** — not a ceiling.
When a sub-topic needs something they don't cover well, add it; the standard is
breadth and discipline, not a fixed source list. Common complements:

- **Consensus** (MCP) — claim-level synthesis across papers; good for "what does
  the field conclude about X" before you go primary.
- **Elicit** (MCP) — semantic paper search with a strong relevance ranker.
- **ClinicalTrials.gov** (MCP) — trials, endpoints, sponsors; the clinical
  landscape `discover` won't surface.
- **ChEMBL** (MCP) — bioactivity, mechanism, ADMET for specific compounds.
- **bioRxiv/medRxiv** (MCP) — preprint-first sweeps (also reachable via Europe
  PMC and OpenAlex, but the dedicated server gives finer date/category control).
- **Web search** — reviews, news, and grey literature that never get a DOI.

If **Elicit** or **Consensus** is installed, it's worth running the sweep through
them as a routine supplement — even when the backbone looks complete, just in
case. Neither reaches an index `discover` can't (so it's a recall/ranking
cross-check, not new coverage), but their semantic ranking can float on-topic
papers the keyword sweep left below the cutoff.

Whatever a complement surfaces, **fold it back into the library** by identifier
(`bib add <DOI|PMID|arXiv>`), so the banked set stays the single durable record
of the sweep regardless of which tool found each paper. The off-backbone tools
are how you *find* a paper; bibliographer is where it *lives*.

## Cite and ground from the primary source

A search engine's snippet, a review's paraphrase, and another author's summary
are all **pointers**, not sources. When a sweep surfaces finding X through a
review or a relay, follow it back to the paper that established X, read that
paper, and cite *it* — the relay may have garbled the number, dropped a caveat,
or laundered a single weak result into apparent consensus. Banking the relay is
fine; relying on it without going primary is not.

## What good looks like

A sweep meets the minimum standard when: it covered the sub-topics, not just the
headline; the banked set is the responsive papers plus the germane-and-strong
ones — broader than the citation list, but not the whole sweep; the queries/
sources/date are recorded; corroboration and gaps were noted; and every load-
bearing paper was read in full, from its primary source. Short of that, widen the
sweep — or tighten what you bank — before calling the topic done.
