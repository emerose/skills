---
name: regulator
description: >-
  Discover, download, organize, and reason over FDA regulatory information —
  published guidance documents, Drugs@FDA approval packages (medical /
  clinical-pharmacology / statistical reviews, approval letters, labels) for
  other programs, advisory-committee briefing documents / transcripts / rosters,
  and biographical dossiers on FDA reviewers and officials. Fetches from public
  FDA sources (openFDA, accessdata.fda.gov, the FDA guidance corpus, advisory-
  committee pages), files the documents on disk in a browsable tree, and stores
  everything in a libkit library with semantic + full-text search over the
  documents' contents — so an agent can answer regulatory questions (what does
  FDA's guidance say about X, how did a comparable drug get approved, what did
  the advisory committee debate, who reviewed it) the way the bibliographer skill
  answers questions over academic papers. Use it whenever the user wants to find,
  download, or organize FDA guidance / approval packages / advisory-committee
  materials, ask "what does FDA guidance say about …", "how was <drug> approved /
  what was in its review", "find precedent for <regulatory question>", "who is the
  FDA reviewer / division director for …", build a regulatory-intelligence library,
  or search inside FDA documents — even if they don't say "regulator." Triggers
  include "FDA guidance on …", "Drugs@FDA", "approval package / review / SBA",
  "advisory committee / AdComm briefing", "NDA/BLA precedent", "what did FDA
  require for …", and "FDA reviewer bio." For academic papers use bibliographer;
  for internal experiment data use scientist; for literature reviews use research.
---

# Regulator

Regulator manages a library of **FDA regulatory documents**: it discovers and
downloads them from public FDA sources, organizes the files on disk in a
human-readable tree, and stores everything in a **libkit** library that gives you
semantic + full-text search over the documents' contents. All of this is driven
by one bundled command-line tool, `scripts/reg.py`.

It is the regulatory-affairs counterpart to the `bibliographer` skill: where
bibliographer lets an agent reason over a collection of academic papers,
regulator lets an agent reason over FDA's published regulatory record.

## The store: libkit (no separate database)

libkit (>=0.5.0) **is** the store — there's no separate regulator database. Each
regulatory document is one libkit *document*; every field (`doc_type`, title, FDA
org, application number, status, …) lives in that document's free-form
`metadata` JSON. Regulator adds what libkit doesn't: the FDA-source ingesters,
the on-disk document tree, readable **citekeys**, and document-level **identity**
(dedup by a type-specific natural key, over libkit's byte identity).

A **library** is a directory (default `~/.regulator`, override with
`--home`/`REGULATOR_HOME`) holding `catalog.duckdb` (the libkit store), `docs/`
(the organized originals, grouped by source type), `guidance_index.json` (a
cached copy of the guidance corpus), and `index.html` (a self-contained browser
viewer). Each document has a **citekey** — the stable handle for
`show`/`tag`/`rm` (e.g. `NDA205834-s000-medical`, `odac-2024-09-26-briefing`,
`guidance-2022-rare-diseases-natural-history`, `person-edward-m-cox`). A document
known from a source index but not yet downloaded is a citation-only **stub**
(still searchable), upgraded to `full` when its PDF arrives.

## The four sources (and how reachable each is)

Each source is one subcommand group. They differ a lot in how machine-accessible
they are — know which tier you're on before you start:

| Source | Group | Access | What you get |
|---|---|---|---|
| **Drugs@FDA** | `reg drugsfda` | 🟢 clean API | openFDA enumerates every approval-package PDF (reviews, letters, labels) — no scraping. The richest source; start here. |
| **Guidance documents** | `reg guidance` | 🟡 one gated feed | The whole corpus is one JSON feed, but it's bot-gated — sync needs a real browser / un-flagged IP, or `--from-file`. Per-doc PDFs are ungated. |
| **Advisory committee** | `reg adcomm` | 🟡 scrape HTML | No API; scrape a meeting page (or a year hub, which we auto-recurse into) for the document links. |
| **Personnel** | `reg personnel` | 🔴 no source | No staff API. Dossiers are built from the **electronic-signature blocks** on the review PDFs you've already ingested, plus org-chart/web research. |

Details and the exact entry-point URLs per source:
[drugs-at-fda](references/drugs-at-fda.md) ·
[guidance](references/guidance.md) ·
[advisory-committees](references/advisory-committees.md) ·
[personnel](references/personnel.md).

## Setup: keys and the embedding backend

An embedding backend is needed to **ingest documents** (ingest embeds their
text) and to run **semantic `reg query`**. It is **not** needed to read or
full-text search an existing library: `list`, `search`, `show`, `text`, `check`,
and the source *discovery* commands (`drugsfda search`, `guidance search`,
`adcomm sync` without `--add`) all open the store FTS-only (or don't touch it) and
work with no key. Put keys in `~/.env` (the tool loads it automatically):

- **`DEEPINFRA_API_KEY`** + `REGULATOR_EMBEDDING=remote` — recommended: remote
  embeddings (no local model download). Alternatively install
  `libkit[fancychunk-torch]` (or `[fancychunk-mlx]` on Apple Silicon) and use the
  default `REGULATOR_EMBEDDING=local`. The model/dimension must stay consistent
  across runs (libkit enforces this; mismatch → set
  `REGULATOR_ALLOW_EMBEDDER_MISMATCH=1` only when you know they're compatible).
- **`DATALAB_API_KEY`** — **strongly recommended here.** FDA review PDFs are
  frequently scanned images; Datalab gives a high-quality parse + OCR. Without it
  libkit falls back to a weaker local reader and scanned reviews ingest poorly.
- **`OPENFDA_API_KEY`** — optional; raises the openFDA limit from 1,000/day to
  120,000/day. Get one free at open.fda.gov. Needed for bulk `drugsfda` work.
- **`REGULATOR_MAILTO`** — your email, sent as a polite User-Agent to FDA APIs.

## Running the tool

It's a self-contained PEP-723 `uv` script (deps: `libkit`, `httpx`, `pypdf`,
`diskcache`, `platformdirs`), so it runs with no install:

```bash
uv run /path/to/skills/regulator/scripts/reg.py <command> [args]
```

The examples below write `reg` for brevity — the skill ships a launcher shim at
[`bin/reg`](bin/reg); add its `bin/` to PATH or symlink the shim. Run `reg init`
once per library before first use (ingest commands also create it on demand).

## Commands

Every command is `reg <verb>` or `reg <source> <verb>`; full per-command usage
is in [references/commands.md](references/commands.md).

| Command | What it does | Detail |
|---|---|---|
| `drugsfda search` | search Drugs@FDA applications (by ingredient / sponsor / brand / appno) | [drugs-at-fda](references/drugs-at-fda.md) |
| `drugsfda add <appno>` | download + ingest an application's approval-package PDFs (`--submission`, `--type`, `--dry-run`) | [drugs-at-fda](references/drugs-at-fda.md) |
| `guidance sync` | fetch + cache the full guidance corpus (`--from-file` to bypass the bot wall) | [guidance](references/guidance.md) |
| `guidance search` `guidance add` | search the cached corpus · ingest a guidance doc by match or URL | [guidance](references/guidance.md) |
| `adcomm sync <url>` | extract a meeting/hub page's materials; `--add` to ingest (auto-recurses a year hub) | [advisory-committees](references/advisory-committees.md) |
| `personnel build` | harvest reviewer signatures from ingested reviews into dossiers (`--dry-run`) | [personnel](references/personnel.md) |
| `import [dir]` | index an existing folder of documents **in place** (no move); classifies accessdata-named PDFs as `drugsfda`, the rest as `other` (`--dry-run`) | [commands](references/commands.md) |
| `add <url\|file>` | ingest one arbitrary document (any `--type`, default `other`) — the escape hatch for PFDD reports, landscape notes, any FDA PDF the source ingesters don't cover | [commands](references/commands.md) |
| `list` `search` `show` | browse · substring metadata search · show one document (all take `--type`, `--json`) | [commands](references/commands.md) |
| `query` `text` | semantic / full-text search **inside** the documents · dump one document's stored text | [commands](references/commands.md) |
| `tag` `rm` `viewer` `check` | tag · remove · (re)build the HTML viewer · integrity check | [commands](references/commands.md) |

Most commands take **`--json`** — prefer it over scraping the human table when you
need to parse. For composition over many records, the Python API
(`from regulator import RegStore`) avoids the per-call libkit cold start.

## Good habits

- **Index an existing archive with `reg import` (dry-run first).** If the library
  home already holds curated documents (a regulatory archive), `reg import
  --dry-run` previews how each file classifies; then a real `import` ingests them
  *in place* (no move) so search/query covers them without disturbing the folder
  layout. Files named the accessdata way are recognised as `drugsfda` reviews.
- **Start from Drugs@FDA.** It's the only clean-API source and the highest-value
  one for precedent ("how did a comparable drug get approved"). `drugsfda search`
  to find the application, `drugsfda add --dry-run` to see its documents, then
  ingest the subset you need.
- **Be selective about what you ingest.** An application can have dozens of PDFs
  and review packages are large/scanned (slow + costs Datalab/embedding). Use
  `--submission` (e.g. `s000` for the original approval) and `--type` (e.g.
  `medical clinpharm summary letter`) to ingest only what answers the question.
- **Confirm the OCR landed** for scanned reviews — `reg text <citekey> | head`.
  If a review ingested as gibberish/empty, set `DATALAB_API_KEY` and re-add.
- **Surface the citekey** you assigned; it's how the user refers to the document.
- **Set `REGULATOR_MAILTO` and get an `OPENFDA_API_KEY`** before bulk work — the
  no-key openFDA limit is only 1,000 requests/day.

## Gotchas (learned the hard way)

- **Guidance corpus: use the static endpoint, not the legacy AJAX feed.** The
  search page's grid loads from a static JSON file
  (`/files/api/datatables/static/search-for-guidance.json`, ~2,800 records) that
  is **not** bot-gated — that's what `reg guidance sync` now uses. The old
  `/datatables-json/...` AJAX path *is* Akamai-gated (HTTP 503); if a future FDA
  change breaks the static path, `--from-file` still accepts a browser-saved copy
  (the static JSON is a bare list of `field_*`-keyed objects, which `parse_rows`
  handles). Individual guidance PDFs (`/media/<id>/download`) are ungated.
- **Bulk-add guidance with `--all`.** `reg guidance add "<terms>" --all` ingests
  every corpus match of a search string (dedup is automatic). Drive a curated set
  of topic queries (oligonucleotide, gene therapy, rare disease, expedited
  programs, surrogate endpoint, …) rather than adding one at a time.
- **An AdComm *year hub* has no document links — only meeting links.** The
  materials live on the per-meeting pages. `reg adcomm sync` handles both: given a
  hub it auto-recurses one level into each meeting page and aggregates (can be
  100+ materials — review before `--add`). Point it at a single meeting page for
  just that meeting.
- **FDA review PDFs are often scanned.** Without `DATALAB_API_KEY` they ingest as
  poor text and `query` won't find anything in them. This source needs OCR more
  than bibliographer's papers do.
- **Personnel: harvest with `build`, author/enrich with `add`.** `reg personnel
  build` reads the *already-ingested* Drugs@FDA reviews and parses their
  electronic-signature pages — so it only finds people whose reviews you've
  ingested (approval letters carry proxy signatures "X on behalf of Y"; we record
  Y as the person and X as `signed_by`). For the leadership chain who *signed
  nothing* (HHS Secretary, Commissioner, center/office directors), use `reg
  personnel add "<name>" --role … --division … --bio … --source …` to author a
  dossier from web research (fda.gov org charts give office/division directors).
  Both `add` and `build` **upsert/merge**: enriching a signer keeps their harvested
  signed-review list, and re-running `build` keeps a hand-authored bio. **Match the
  signature name form** (incl. middle initial, e.g. `Teresa J Buracchio`) when
  enriching a signer, or you'll create a second dossier.
- **Application numbers carry a prefix.** Pass `NDA205834` / `BLA761234`, not a
  bare number — the prefix distinguishes drug (NDA/ANDA) from biologic (BLA).

## Maintaining this skill (for agents working ON regulator)

Read the repo-wide [AGENTS.md](../../AGENTS.md) first — improve-as-you-go, push
rote work into code, **PR your skill changes back to the skills repo**, contribute
generic dependency fixes upstream by PR, and verify changes on throwaway data.
Regulator-specific notes:

- **libkit is the upstream** for store/embedding/cache bugs — issue + PR there,
  not a local workaround (same as bibliographer/scientist).
- **The source ingesters depend only on stdlib + httpx** (not libkit), so their
  parsers unit-test offline against fixtures (`tests/test_*.py`). When FDA changes
  a page/feed shape, fix the parser and add a fixture row capturing the new shape.
  Run: `uv run --with pytest --with httpx pytest skills/regulator/tests/ -q`.
- **FDA HTML/feeds drift.** When a scrape returns 0 results, fetch the page and
  check whether it's gated (503 challenge), JS-rendered, or just restructured —
  then update the regex/column map and capture a fixture. (The AdComm two-level
  hub→meeting structure and the proxy-signature format were both found this way.)

## References

- [commands.md](references/commands.md) — full per-command usage, flags, `--json` shapes, the Python (`RegStore`) surface.
- [drugs-at-fda.md](references/drugs-at-fda.md) — the openFDA + accessdata pipeline: search, enumerate, the review-type taxonomy, selective ingest.
- [guidance.md](references/guidance.md) — the guidance corpus feed, the bot-wall escape hatch, the cache, search/add.
- [advisory-committees.md](references/advisory-committees.md) — the hub→meeting page structure, material classification, ingest.
- [personnel.md](references/personnel.md) — building dossiers from signature blocks + org-chart/web research, the proxy-signature convention.
- [schema.md](references/schema.md) — the document model: `doc_type`s, per-type fields, citekeys, natural-key dedup.
