# Skills

A collection of [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills)
for Claude and other LLM agents, packaged as a
[Claude Code plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces).
Each skill is a self-contained folder under `skills/` — a `SKILL.md` (name +
description frontmatter, then instructions) plus any bundled scripts, references, and
tests.

Three skills make up one workflow — running a scientific-data program and writing it up
honestly:

- **[bibliographer](skills/bibliographer/)** manages the published literature you *read*.
- **[scientist](skills/scientist/)** manages the experiments you *run*.
- **[research](skills/research/)** manages the *literature layer* that sits between them —
  reviews and grounded citations of the published record.

A fourth, **[regulator](skills/regulator/)**, is a parallel library for the **FDA
regulatory record** — guidance documents, Drugs@FDA approval packages, advisory-committee
materials, and reviewer dossiers — letting an agent reason over FDA's published
regulations the way bibliographer reasons over academic papers.

They share two internal pieces: **[reportkit](skills/reportkit/)**, the grounded-report
engine, and **[libkit](#libkit--embeddings)**, the single searchable store the skills
index into instead of a bespoke database.

| | [bibliographer](skills/bibliographer/) | [scientist](skills/scientist/) | [research](skills/research/) |
|---|---|---|---|
| **Manages** | a personal library of published papers | a tree of internal experiments | the literature layer — reviews + grounded citations |
| **Inputs** | DOI · arXiv · PMID/PMCID · S2 id · PDF | CRO/lab files (Excel, Prism, Word/PDF/PPT) | papers already in the bibliographer library |
| **On disk** | PDFs filed into a human-readable author tree | one folder per experiment, `raw → data → analysis → claims → report` | PRISMA reviews, per-paper claim stores, `[lit:]` claims |
| **Answers** | "what do I have on X," "find the DOI for these scans" | "what's the evidence for X," "which study has the Day-29 numbers" | "what does Smith 2020 actually say," "does this quote support this paraphrase" |
| **CLI** | `bib` | `sci` (+ a pytest plugin for claims) | `res` (+ a pytest plugin for claims) |

## The skills

### [bibliographer](skills/bibliographer/) — a library of published papers

Add a paper from a DOI, arXiv ID, PMID/PMCID, Semantic Scholar ID, or a bare PDF, and
the metadata is fetched automatically (Crossref / arXiv / PubMed / Semantic Scholar /
Unpaywall) and the PDF filed into a human-readable author tree. From there:

- **bulk-import** a folder of PDFs, matching each to its metadata;
- **`enrich`** — recover metadata for untitled or scanned PDFs;
- **search** semantically and full-text *inside* the papers, not just over titles;
- **discover** new papers on a topic across many scholarly APIs (OpenAlex, Semantic
  Scholar, Europe PMC, PubMed, Crossref, arXiv) and bank them into the library;
- **mine the citation graph** — find papers the collection probably *should* have (works
  it cites a lot but doesn't contain), cluster papers into topics, flag off-topic ones;
- **export BibTeX**, **browse** the whole collection through a generated, self-contained
  HTML viewer (`index.html`), and **audit** for duplicates and integrity problems.

Built on **libkit** as its single store — no separate database — through a bundled
`bib` CLI. See [`skills/bibliographer/SKILL.md`](skills/bibliographer/SKILL.md).

### [scientist](skills/scientist/) — a provenance-tracked experiment tree

Manage a tree of scientific experiments — one folder per experiment — end to end as a
single pipeline where **every arrow records provenance**:

```text
raw/  →  data/  →  analysis/  →  claims  →  report
```

- **`raw/`** — the CRO/lab originals, untouched.
- **`data/`** — tidy, *faithful* CSVs extracted from `raw/` (Excel `.xlsx`/`.xls`,
  GraphPad Prism `.pzfx`/`.prism`, Word/PDF/PowerPoint). No computation lives here; an
  extraction audit checks that every `data/` value is grounded in `raw/`.
- **`analysis/`** — re-derivations from `data/`: EC50/Hill fits, stats, summaries, and
  figures, each sha-pinned to the inputs and recipe that produced it.
- **claims** — grounded scientific assertions, each a **re-runnable pytest spec** that
  links a statement to sha-pinned evidence with a judged *strength*. Run them and a
  claim either still holds or has drifted.
- **report** — a human-facing narrative built *from* claims: cite a grounded claim with
  `[claim:<id>]`, ground a third-party fact on a library paper with `[lit:<id>]`, embed
  only grounded figures/tables, and render to PDF/HTML via pandoc.

Everything is indexed into a **libkit** store for semantic + full-text search — with
**claims and summaries the highest-value searchable content** — so you can ask "what's
the evidence for the dose-dependent effect" or "which study has the lumbar-cord
numbers." `sci trace` walks any claim or report back down to the original measurements,
and `sci reproduce` re-runs a derivation to confirm its artifacts still reproduce.

One `sci` CLI drives the deterministic operations; a pytest plugin runs the claims. See
[`skills/scientist/SKILL.md`](skills/scientist/SKILL.md), and the per-phase detail under
[`skills/scientist/references/`](skills/scientist/references/).

### [research](skills/research/) — the literature layer

Where scientist owns *experiments*, research owns everything *literature*. It turns the
published record — papers already in the bibliographer library — into the same kind of
grounded, re-runnable, auditable artifacts:

```text
bibliographer library  →  [lit:] claims / paper-claims  →  litreview (PRISMA survey)  →  cited by a report
   (the paper PDFs)         (grounded third-party facts)      (a neutral evidence map)
```

- **`[lit:]` claims** ground a load-bearing third-party statement on a paper by pinning a
  *verbatim quote* plus a recorded support verdict — so "back this with the literature"
  becomes a re-runnable check, not a footnote you take on faith.
- **paper-claims** extract a paper's assertions once into an attributed per-paper store,
  reusable across reviews and reports.
- **litreviews** are *conclusion-free* PROSPERO/PRISMA surveys: a pre-registered search
  protocol, a screening log, and a thesis-independent map of what the field reports, how
  strong each piece is, and where it disagrees or is silent. Reviews compose into a
  **review tree** via `[litreview:]` edges.
- **bibliometric claims** (most-cited, rarely-replicated) assert facts about the
  literature *itself*, off stored OpenAlex metrics.

Driven by a `res` CLI plus the shared claims pytest plugin. See
[`skills/research/SKILL.md`](skills/research/SKILL.md) and
[`skills/research/references/`](skills/research/references/).

### [regulator](skills/regulator/) — a library of FDA regulatory documents

The regulatory-affairs counterpart to bibliographer. It discovers, downloads, and
organizes FDA regulatory information from public sources, then indexes it into libkit for
semantic + full-text search — so an agent can answer "what does FDA guidance say about X,"
"how was a comparable drug approved / what was in its review," or "who reviewed it." Four
sources, in descending order of machine-accessibility:

- **Drugs@FDA** (`reg drugsfda`) — openFDA enumerates every approval-package PDF (medical /
  clin-pharm / statistical reviews, approval letters, labels); accessdata serves them. A
  clean API end-to-end.
- **Guidance documents** (`reg guidance`) — the whole corpus is one JSON feed (bot-gated;
  has a `--from-file` escape hatch), with ungated per-document PDFs.
- **Advisory committee** (`reg adcomm`) — scrape a meeting page (or a year hub, auto-
  recursed) for briefing docs, transcripts, and rosters.
- **Personnel** (`reg personnel`) — no staff API; dossiers are derived from the electronic-
  signature blocks on ingested review PDFs, enriched by org-chart/web research.

Built on **libkit** through a bundled `reg` CLI. See
[`skills/regulator/SKILL.md`](skills/regulator/SKILL.md) and
[`skills/regulator/references/`](skills/regulator/references/).

## How they fit together

The skills are layered, not tangled. From the bottom up:

```text
pytest-grounding   (PyPI)     the claim-grounding core — re-runnable pytest specs that
                              pin a statement to sha-anchored evidence
        ↓
reportkit          (in-repo)  the generic grounded-report engine — parse a report's
                              citations + embeds, audit each against live evidence,
                              render via pandoc, trace down to raw measurements
        ↓
scientist + research          the two domain skills — experiments and literature.
                              Each plugs its own citation kinds into reportkit's
                              registry; neither imports the other.

bibliographer                 stands alone; research reaches it (read-only) for the
                              paper library a [lit:] claim grounds against.
```

[`reportkit`](skills/reportkit/) is deliberately domain-blind: it natively resolves
`[claim:]`, `[report:]`, and `![..](..)` embeds, and knows nothing about literature or
libraries. Every other citation scheme — including research's `[lit:]`/`[litreview:]` —
plugs in through a `register_citation(...)` registry, the seam that keeps each domain
skill decoupled from the engine and from the others. The grounding core, by contrast, *is*
generic: [`pytest-grounding`](https://pypi.org/project/pytest-grounding/) is a published
PyPI package usable outside this repo.

## Design philosophy

A few principles run through all of these skills. They are less about science than about
how to build a tool an LLM agent operates and a human (or a second agent) has to trust.

### Spend the context window deliberately

An agent's context is its scarcest resource, so the skills are built to load detail only
when it's needed. A `SKILL.md` is a thin overview that routes to `references/` files
loaded on demand, not a wall of instructions read every time. The CLIs print terse,
pipe-friendly output and offer `--json` for programmatic reads, so an agent can
`head`/`grep` a result instead of pulling a verbose dump into context. The goal is that
the *first* page an agent reads tells it which second page to read — and no more.

### Encode the mechanical, leave the judgment to the model

Anything deterministic — extracting cells out of a Prism file, fitting a Hill curve,
moving PDFs into the author tree, auditing that every `data/` value traces to `raw/`,
rendering pandoc — lives in Python (`bib`/`sci`/`res`), where it is fast, consistent,
testable, and the same every run. Anything that is genuinely a *judgment* — does this
quote fairly support this paraphrase, is this paper on-topic, how strong is this evidence
— is left to the LLM, guided by a prose rubric in `references/` rather than a brittle
heuristic in code. Crucially the skills **don't bury the model's judgment inside a
script**: a verdict an LLM makes is *recorded by the caller*, pinned to the evidence it
judged (e.g. a support verdict keyed by the quote's `evidence_sha`), so it can be
reviewed and re-run. When a rule is objective, prefer a mechanical check; when it's
semantic, prefer a lightweight artifact plus a fresh-context critic over an
over-engineered gate.

### Ground claims in two directions

"Grounded" here means more than a citation. First, **traceability backward**: every
load-bearing statement pins to sha-anchored evidence — a `[claim:]` to its derivation, a
`[lit:]` to a verbatim quote in a real paper — and `sci trace` walks a finished report
all the way down to the original measurements. Second, **documented forward**: the
*analysis itself* is provenance-pinned and re-runnable, so the statistical manipulation
between a raw number and a reported one is never a black box. `analysis/` artifacts are
sha-pinned to their inputs and recipe; `sci reproduce` re-runs a derivation to confirm it
still produces the same figures. A future reviewer sees not just *the number* but *how it
was computed* — the discipline is "no quantitative prose without a backing."

### Build for a skeptical, independent reviewer

The artifacts are shaped so that a fresh agent — a clean session with none of the original
context — can audit and judge them. Claims are **re-runnable pytest specs**: an
independent reviewer doesn't take a claim on trust, they execute it and watch it hold or
drift. Literature reviews carry a pre-registered PRISMA protocol and a screening log, so
the selection of evidence is itself inspectable. Reviews and reports are checked by
**fresh-context critics** (e.g. unsurveyed-subtopic, conflict-survival,
internal-consistency) rather than by gates the author wrote for themselves. And the audit
passes emit a **structured worklist**, so fixes can be driven by code or fanned out across
parallel agents. The whole pipeline is designed for the second reader, human or model, who
shows up later asking "why should I believe this?"

These show up as concrete working rules in [AGENTS.md](AGENTS.md): push rote work into
code, contribute generic fixes upstream rather than working around them locally, keep
docs in sync with code in the same change, and give every stateful skill an audit it can
re-run.

## libkit & embeddings

All three skills store their searchable index in [libkit](https://pypi.org/project/libkit/),
which **embeds every document**, so an embedding backend is required. Either set
`DEEPINFRA_API_KEY` for remote embeddings (no local model), or install
`libkit[fancychunk-*]` for local ones. See [`.env.example`](.env.example) for the
available keys.

## Install

### As a Claude Code plugin (recommended)

```text
/plugin marketplace add emerose/skills
/plugin install sq@emerose-skills
```

This repo ships as a single `sq` plugin. Claude Code clones it, discovers the bundled
skills, and invokes them automatically when relevant (or manually via `/sq:bibliographer`,
`/sq:scientist`, `/sq:research`). For **Claude.ai or another harness**, point the agent at
the relevant `SKILL.md` and let it run the bundled scripts (or `uv tool install` the CLI
below).

### The `bib` CLI, as a standalone tool

Install the bundled command with [uv](https://docs.astral.sh/uv/) so it's on your
`PATH` everywhere:

```bash
uv tool install "git+https://github.com/emerose/skills#subdirectory=skills/bibliographer"
bib --help
```

Or run it once, without installing, via `uvx`:

```bash
uvx --from "git+https://github.com/emerose/skills#subdirectory=skills/bibliographer" bib --help
```

### No install at all

Every script here is a [PEP 723](https://peps.python.org/pep-0723/) `uv` script that
declares its own dependencies, so you can run it straight from a checkout — no
virtualenv, no install:

```bash
uv run skills/bibliographer/scripts/bib.py init
uv run skills/bibliographer/scripts/bib.py add arXiv:1706.03762
uv run skills/bibliographer/scripts/bib.py import ~/papers --dry-run
uv run skills/bibliographer/scripts/bib.py query "why do transformers scale"
```

The `scientist` and `research` skills work the same way — the `sci`/`res` CLIs for the
deterministic ops (zero-install via PEP 723), and the claims harness via an ephemeral
editable install:

```bash
uv run skills/scientist/scripts/sci.py extract "K1-000000 - Potency"   # raw → tidy data/
uv run skills/scientist/scripts/sci.py query "dose-dependent gait effect"   # semantic search
uv run skills/scientist/scripts/sci.py trace "K1-000000 - Potency"     # claim → … → raw
uv run --with-editable skills/scientist pytest "K1-000000 - Potency/analysis/claims"
```

## Layout

```text
.claude-plugin/
  marketplace.json    # marketplace catalog
  plugin.json         # this repo, exposed as the single `sq` plugin
skills/
  bibliographer/      # the paper library            (CLI: bib)
  scientist/          # the experiment pipeline       (CLI: sci)
  research/           # the literature layer          (CLI: res)
  reportkit/          # the shared grounded-report engine (internal, not on PyPI)
  <skill-name>/
    SKILL.md          # name + description frontmatter, then instructions
    scripts/          # bundled executable helpers (PEP 723 uv scripts)
    references/       # docs loaded on demand
    evals/            # test prompts for the skill
    tests/            # unit tests for the bundled scripts
    pyproject.toml    # optional: lets the CLI be `uv tool install`-ed
```

The repository is simultaneously the marketplace **and** its single plugin: the plugin
`source` points at the repo root, and Claude Code discovers the skills under `skills/`
automatically.

## Extending these skills

If you're an agent working on a skill here, read [AGENTS.md](AGENTS.md) first: capture
lessons as you go, push rote work into code, contribute generic fixes upstream by PR,
and keep stateful skills audited.

## License

[MIT](LICENSE) © Sam Quigley
