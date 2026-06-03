# Skills

A collection of [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills)
for use with Claude and other LLM agents, packaged as a
[Claude Code plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces).
Each skill is a self-contained folder under `skills/` with a `SKILL.md` and any
bundled scripts, references, and assets.

## Skills

### [bibliographer](skills/bibliographer/)

Manage a personal collection of academic articles: add papers from a DOI, arXiv
ID, PMID/PMCID, Semantic Scholar ID, or a PDF (auto-fetching metadata from
Crossref / arXiv / PubMed / Semantic Scholar / Unpaywall), file the PDFs into a
human-readable author tree, bulk-import a folder, recover metadata for unverified
scans (`enrich`), run semantic + full-text search *inside* the papers, export
BibTeX, browse the collection through a generated self-contained HTML viewer
(`index.html`), and audit the library for correctness. Built on **libkit** as its
single store (no separate database) via a bundled `bib` CLI.

Because libkit embeds every document, an embedding backend is required: set
`DEEPINFRA_API_KEY` (remote, no local model) or install `libkit[fancychunk-*]` for
local embeddings. See [`skills/bibliographer/SKILL.md`](skills/bibliographer/SKILL.md)
and [`.env.example`](.env.example) for the available keys.

### [archivist](skills/archivist/)

Organize, index, and search a tree of scientific experiments kept as one folder
per experiment — raw lab/CRO measurements, cleaned data, protocols, reports,
analysis notebooks, and internal summaries. Indexes every file for full-text +
semantic search inside a **libkit** store (narrative files embedded whole, tabular
files as schema/preview cards, binaries as descriptors), catalogs each experiment
with its CRO study IDs / assays / ASOs / models, cross-references related studies,
reads exact values out of source spreadsheets, and keeps internal README/summary
write-ups current as the underlying data changes. Driven by a bundled `arx` CLI;
uses the same embedding backend as bibliographer. See
[`skills/archivist/SKILL.md`](skills/archivist/SKILL.md).

## Install

### As a Claude Code plugin (recommended)

```text
/plugin marketplace add emerose/skills
/plugin install bibliographer@emerose-skills
```

Claude Code clones this repo, discovers the `bibliographer` skill, and invokes it
automatically when relevant (or manually via `/bibliographer:bibliographer`).

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

The repository is simultaneously the marketplace **and** its single plugin: the
plugin `source` points at the repo root, and Claude Code discovers the skills
under `skills/` automatically.

## Using these skills

- **Claude Code**: install via the marketplace (above). The `description` in each
  `SKILL.md` controls when the skill triggers.
- **Claude.ai / other agents**: point your harness at the `SKILL.md` and let the
  agent run the bundled scripts (or `uv tool install` the CLI).

## Extending these skills

If you're an agent working on a skill here, read [AGENTS.md](AGENTS.md) first:
capture lessons as you go, push rote work into code, contribute generic fixes
upstream by PR, and keep stateful skills audited.

## License

[MIT](LICENSE) © Sam Quigley
