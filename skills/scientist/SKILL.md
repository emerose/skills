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
  library of published academic papers (DOIs, arXiv, PMIDs, PDFs), use bibliographer instead; for
  literature reviews, `[lit:]` claims grounded on published papers, or bibliometric claims about
  the literature, use research instead.
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
| Mark up a CRO's **draft protocol / study plan** — read their comments, reply in-thread, return tracked changes | [references/protocol-markup.md](references/protocol-markup.md) |
| Review provenance, audit staleness, structural check, **trace** a result raw→claims, **enforce** prose↔claims | [references/review-audit.md](references/review-audit.md) |
| &nbsp;&nbsp;↳ deep reference for the structural / staleness / semantic audit passes (`sci check` / `sci audit` / parallel-agent) | [references/auditing.md](references/auditing.md) |
| Author a human-facing **report** from grounded claims — `sci report` mechanics (cite `[claim:<id>]`, embed grounded figures, audit + render) | [references/report.md](references/report.md) |
| &nbsp;&nbsp;↳ when **drafting the report prose**: voice/structure, consulting the literature **through litreviews** (read existing / run new for a subtopic's context, per-detail `[lit:]` cites still welcome), the literature-sweep & disconfirming-evidence discipline, the generation brief, the fresh-context §3 + voice/tone reviews | [references/report-authoring.md](references/report-authoring.md) |
| Do a **literature review / survey** (`kind=litreview`), extract a paper's **attributed paper-claims**, ground a third-party fact as a `[lit:]` claim, or assert a **bibliometric** claim about the literature | → the separate **[research](../research/SKILL.md)** skill (the `res` CLI). scientist is experiments-only; a `sci report` can *cite* `[lit:]`/`[litreview:]` when research is installed (see [references/report.md](references/report.md) → `[lit:]`). |

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
- **Claims are pytest tests.** `statement(...)` = the proposition (ideally computed from the data) ·
  docstring = reviewer notes · node id = the stable id · markers =
  strength/kind/caveats · `assert` = the grounding/drift check. Running the claims captures
  provenance automatically **and indexes each claim into libkit as searchable, grounded evidence**
  (carrying its outcome + strength, so a contradicted or weak claim is never surfaced as fact).
- **Don't trust a filename for what a file contains** — verify against indexed content.

## Running the tool

The CLI is `scripts/sci.py`, a self-contained PEP-723 `uv` script (it declares its own
deps), so it runs with no install. The always-works form — **use this in scripts and as
an agent** — is `uv run /path/to/skills/scientist/scripts/sci.py <command> [args]`. To
get a real `sci` on your PATH instead of typing that absolute form, the skill ships a
launcher shim at [`bin/sci`](bin/sci) — add its `bin/` to PATH (`export
PATH="/path/to/skills/scientist/bin:$PATH"`) or symlink it once (`ln -s
/path/to/skills/scientist/bin/sci ~/.local/bin/sci`). The shim execs the script, whose
shebang resolves deps each run.

**Data-tree root** (`--home` / `$SCIENTIST_HOME`): you no longer have to export
`SCIENTIST_HOME` when running from inside the data checkout. When it is unset, `sci` (and
the grounding `experiments` accessor) infer the data-repo root by walking up from the
working directory to a checkout marker (`.scientist/`, or `LAYOUT.md` + `program/`). An
explicit `--home` or a set `$SCIENTIST_HOME` always wins; if no root is found the clear
"set SCIENTIST_HOME" error still fires. Literature-claim grounding likewise loads `~/.env`
to find `$BIBLIOGRAPHER_HOME` if it isn't already set, so no `source ~/.env` is needed.

### CLI or Python — your choice

The CLI is a thin shell over the importable `scientist` package, so the same logic is
callable both ways. Pick per task:

- **CLI** (`sci … [--json]`) — best for a **one-shot** check or render. If you catch
  yourself piping a validator's prose to `grep`/`head`/`tail` to find out "is it broken,
  and which findings?", that's the signal to call the function instead (or pass `--json`)
  and branch on the structured result.
- **Python** (`from scientist.provenance import trace`; the generic report engine is
  `from reportkit import report`) — best for **composition**: looping a check over many
  reports/experiments, or branching on fields without re-parsing text. These functions
  **return dicts** (the same payloads the CLI prints under `--json`); a paired `render_*`
  turns one into the human text. (Literature reviews / paper-claims / coverage live in the
  separate `research` skill — `from research import litreview, coverage`.)

```python
from pathlib import Path
from reportkit import report
from scientist.provenance import trace

res = report.audit(Path("program/reports/foo/report.md"), home=Path("/data"))
# res -> {report, scope, exp_id, citations, embeds, report_cites, findings, status}
if res["status"] != "GROUNDED":
    for f in res["findings"]:           # e.g. missing-claim / drifted-embed / weak-backing
        print(f["kind"], "@ line", f["line"])
# print(report.render_audit(res))       # ← same text the CLI would have shown

tr = trace.trace_report(Path("program/reports/foo/report.md"), repo_root=Path("/data"))
# tr["status"], tr["chains"], tr["breaks"]
```

Run it where the package is importable: `uv run --with-editable /path/to/skills/scientist
python3 your_script.py` (use this same editable form for anything that re-runs analysis,
e.g. `reproduce`, since it carries the pinned pandas/scipy runtime). Pass `home=` or rely
on `$SCIENTIST_HOME` exactly as the CLI does. For a single check, just call `sci … --json`.

## Maintaining this skill (for agents working ON scientist)

Read the repo-wide [AGENTS.md](../../AGENTS.md) first: improve-as-you-go, push rote work into code,
**PR your changes back** to the skills repo, contribute generic fixes **upstream to libkit** by PR
(libkit is the store substrate; this is how bibliographer drove several libkit features),
and verify changes on throwaway data. Per-phase maintenance notes live in each `references/` file;
the open direction (finer-grained provenance, program-level traceability) is in
[ROADMAP.md](ROADMAP.md) — claim↔prose enforcement, the reproduction audit, and the terminal
**report** phase (`claims → report`, see [report.md](references/report.md)) are shipped. The
**literature layer** — neutral litreviews (`kind=litreview`), per-paper paper-claims, and
`[lit:]`/bibliometric claims — has been **split out into the separate
[research](../research/SKILL.md) skill** (the `res` CLI). A `sci report` can still *cite*
`[lit:]`/`[litreview:]` when research is installed (the citation layer registers with the shared
`reportkit` engine), but scientist no longer owns the literature docs or commands; work on those in
research.
