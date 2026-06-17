# Skills

A collection of [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills)
for Claude and other LLM agents, packaged as a
[Claude Code plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces).
Each skill is a self-contained folder under `skills/` — a `SKILL.md` (name +
description frontmatter, then instructions) plus any bundled scripts, references, and
tests.

There are two skills today, and they are complements: **bibliographer** manages the
published literature you *read*, **scientist** manages the experiments you *run*. Both
are research tools, and both keep their searchable index in **[libkit](#libkit--embeddings)**
rather than a bespoke database.

| | [bibliographer](skills/bibliographer/) | [scientist](skills/scientist/) |
|---|---|---|
| **Manages** | a personal library of published papers | a tree of internal experiments |
| **Inputs** | DOI · arXiv · PMID/PMCID · S2 id · PDF | CRO/lab files (Excel, Prism, Word/PDF/PPT) |
| **On disk** | PDFs filed into a human-readable author tree | one folder per experiment, `raw → data → analysis → claims → report` |
| **Answers** | "what do I have on X," "find the DOI for these scans" | "what's the evidence for X," "which study has the Day-29 numbers" |
| **CLI** | `bib` | `sci` (+ a pytest plugin for claims) |

## Skills

### [bibliographer](skills/bibliographer/) — a library of published papers

Add a paper from a DOI, arXiv ID, PMID/PMCID, Semantic Scholar ID, or a bare PDF, and
the metadata is fetched automatically (Crossref / arXiv / PubMed / Semantic Scholar /
Unpaywall) and the PDF filed into a human-readable author tree. From there:

- **bulk-import** a folder of PDFs, matching each to its metadata;
- **`enrich`** — recover metadata for untitled or scanned PDFs;
- **search** semantically and full-text *inside* the papers, not just over titles;
- **export BibTeX**, and **browse** the whole collection through a generated,
  self-contained HTML viewer (`index.html`);
- **audit** the library for duplicates and integrity problems.

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

## libkit & embeddings

Both skills store their searchable index in [libkit](https://pypi.org/project/libkit/),
which **embeds every document**, so an embedding backend is required. Either set
`DEEPINFRA_API_KEY` for remote embeddings (no local model), or install
`libkit[fancychunk-*]` for local ones. See [`.env.example`](.env.example) for the
available keys.

## Install

### As a Claude Code plugin (recommended)

```text
/plugin marketplace add emerose/skills
/plugin install bibliographer@emerose-skills
/plugin install scientist@emerose-skills
```

Claude Code clones this repo, discovers the skills, and invokes them automatically when
relevant (or manually via `/bibliographer:bibliographer` / `/scientist:scientist`).

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

The `scientist` skill works the same way — the `sci` CLI for the deterministic ops
(zero-install via PEP 723), and the claims/report harness via an ephemeral editable
install:

```bash
uv run skills/scientist/scripts/sci.py extract "K1-000000 - Potency"   # raw → tidy data/
uv run skills/scientist/scripts/sci.py query "dose-dependent gait effect"   # semantic search
uv run skills/scientist/scripts/sci.py trace "K1-000000 - Potency"     # claim → … → raw
uv run --with-editable skills/scientist pytest "K1-000000 - Potency/analysis/claims"
```

## Layout

```text
.claude-plugin/
  marketplace.json    # marketplace catalog (lists the plugins below)
  plugin.json         # this repo, exposed as the `bibliographer` plugin
skills/
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

## Using these skills

- **Claude Code**: install via the marketplace (above). The `description` in each
  `SKILL.md` controls when the skill triggers.
- **Claude.ai / other agents**: point your harness at the `SKILL.md` and let the agent
  run the bundled scripts (or `uv tool install` the CLI).

## Extending these skills

If you're an agent working on a skill here, read [AGENTS.md](AGENTS.md) first: capture
lessons as you go, push rote work into code, contribute generic fixes upstream by PR,
and keep stateful skills audited.

## License

[MIT](LICENSE) © Sam Quigley
