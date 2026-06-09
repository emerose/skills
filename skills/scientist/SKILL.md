---
name: scientist
description: >-
  Manage a tree of scientific experiments end to end — one folder per experiment holding raw
  lab/CRO deliverables, extracted data, re-derived analysis, grounded claims, reports, and
  internal summaries — as one provenance-tracked pipeline raw→data→analysis→claims. Extract raw
  measurements out of CRO files (Excel .xlsx/.xls, GraphPad Prism .pzfx/.prism, Word/PDF/
  PowerPoint) into tidy deterministic data/ CSVs; re-derive analysis (EC50/Hill fits, stats,
  summaries, figures) from that data; assert grounded scientific claims (each a re-runnable
  pytest spec linking a statement to sha-pinned evidence with a strength); and index everything
  into a libkit store for semantic + full-text search — with claims and internal summaries the
  highest-value searchable content. Use this skill whenever the user wants to turn CRO
  spreadsheets / Prism / reports into clean data, (re)generate or audit an experiment's data or
  analysis, fit a dose-response, make or check a grounded claim, ask "what's the evidence for X,"
  "which study has the Day-29 knockdown numbers," or "everything we ran with ASO 7," file a new
  CRO/lab delivery, scaffold a new experiment folder, keep a README/summary current, or trace a
  result back to the original measurements — even if they don't say "scientist." For a personal
  library of published academic papers (DOIs, arXiv, PMIDs, PDFs), use bibliographer instead.
---

# Scientist

Manages a tree of scientific experiments — one folder per experiment — as a single
**provenance-tracked pipeline**:

```
raw/  →  data/  →  analysis/  →  claims + README        (each arrow records provenance)
```

`raw/` = CRO/lab originals · `data/` = tidy *faithful* CSVs (no computation) · `analysis/` =
re-derivations (EC50/Hill fits, stats, summaries, figures) · **claims** = grounded scientific
assertions, each a re-runnable pytest spec · `README.md` = the human/agent summary. Everything is
indexed into a **libkit** store for semantic + full-text search, with **claims and summaries the
highest-value searchable content**.

The only caller is an LLM agent. The bundled tools exist to make a sprawling, heterogeneous data
folder *mechanical, repeatable, and auditable* — and *answerable* ("which file has the lumbar-cord
knockdown numbers," "what's the evidence for the dose-dependent gait effect," "is this summary
still true").

## Pick the task → load the reference

Each phase's detail lives in `references/` and is loaded only when you need it. Start here:

| You want to… | Read |
|---|---|
| Extract raw CRO files → tidy `data/` CSVs, and audit that `data/` is grounded in `raw/` | [references/extract.md](references/extract.md) |
| Re-derive analysis (fits/stats/figures) and author grounded scientific **claims** | [references/derive-claims.md](references/derive-claims.md) |
| Index / search / catalog the tree, file a delivery, scaffold a new experiment | [references/search-index.md](references/search-index.md) |
| Review provenance, audit staleness, structural check, **trace** a result raw→claims | [references/review-audit.md](references/review-audit.md) |

`data/` naming convention + assay vocabulary: [references/naming.md](references/naming.md).
Private CRO vocabulary (your real vendor names): [references/vocab.example.yml](references/vocab.example.yml).

## Core invariants (true across every phase)

- **Durable truth in git, derived layer in libkit.** The `experiment.yml` provenance, the
  `extract.py`/`derive.py` recipes, the claims tests, and the `data/` CSVs are durable and
  git-diffable. The **libkit store** (embeddings, search index, experiment/file/**claim** cards)
  is *rebuildable* — wipe it, reindex, and you're whole. Never make the cache load-bearing for truth.
- **One provenance ledger.** Each experiment's `experiment.yml` holds a unified `provenance` list.
  Every generation step — extract (`data/…`), derive (`analysis/…`), review (`README.md`) — appends
  an edge: an `artifact` plus its `inputs` (each `path` + `sha256`). So `raw → data → analysis →
  README` is **one DAG in one place**, and a single audit can walk it.
- **Faithful vs. derived.** `data/` is a strict, grounded *superset* of `raw/` with **no
  computation**. Any mean/SEM/%-knockdown/fit belongs in `analysis/`, never in `data/`.
- **Claims are pytest tests.** docstring = the statement · node id = the stable id · markers =
  strength/kind/caveats · `assert` = the grounding/drift check. Running the claims captures
  provenance automatically **and indexes each claim into libkit as searchable, grounded evidence**
  (carrying its outcome + strength, so a contradicted or weak claim is never surfaced as fact).
- **Don't trust a filename for what a file contains** — verify against indexed content.

## Maintaining this skill (for agents working ON scientist)

Read the repo-wide [AGENTS.md](../../AGENTS.md) first: improve-as-you-go, push rote work into code,
**PR your changes back** to the skills repo, contribute generic fixes **upstream to libkit** by PR
(libkit is the store substrate; this is how bibliographer/archivist drove several libkit features),
and verify changes on throwaway data. Per-phase maintenance notes live in each `references/` file;
the open direction (reproduction audit, finer-grained provenance, claim/prose enforcement) is in
[ROADMAP.md](ROADMAP.md).
