# Reports — `claims → report`: a grounded human narrative (§5)

Turns grounded claims into a human-facing **report**: a readable, compelling Markdown
narrative — potentially fanning in across experiments — that arranges existing claims into
an argument and embeds figures/tables as exhibits, rendered to a polished **PDF**. A report
is the terminal phase of the pipeline `raw → data → analysis → claims → report`.

> Built by `sci report` (this skill's CLI). The command **validates → audits → renders** in
> one pass; it reads the per-experiment + program `grounding_report.json` claim index and the
> `experiment.yml` provenance ledgers (no libkit store needed for the audit). Full design:
> [SPEC.md](../SPEC.md) / [ROADMAP.md](../ROADMAP.md) §5.

## The one rule: no quantitative prose without a backing

A report **cites existing grounded claims only** — it never re-litigates grounding and can't
drift ahead of the evidence. The unit of backing is a `kind=claim` that is *grounded*
(outcome `passed`/`xpass` **and** strength `strong`/`moderate`), exactly as in the prose↔claims
audit ([review-audit.md](review-audit.md) §3). A raw `analysis/` artifact cell is grounded
provenance but **not** judged evidence, so it never backs a sentence. **To assert something
new, author the claim first** — authoring claims is out of scope for the report itself
(see [derive-claims.md](derive-claims.md)).

Embedding a figure/table as an *exhibit* is distinct from *backing a sentence*: the sentence
that draws a conclusion from an exhibit still carries a claim citation.

## Authoring — Markdown + two inline syntaxes

A report is git-diffable Markdown with optional YAML front matter (`title`/`author`/`date`):

- **Claim citation** — `[claim:<id>]` backs a quantitative assertion. `<id>` is the stable
  `claim_id` `<exp>::<test-file>::<node>` (e.g.
  `K1-230102::test_K1_230102.py::test_wt_neocortex_knockdown`,
  `program::test_program_lead.py::test_lead_titrates_into_rescue_window`), the raw pytest
  nodeid, or the **trailing node name** when it's unambiguous across the tree. Prefer the full
  `claim_id` for cross-experiment reports — short node names can collide (e.g. `test_design_*`).
- **Exhibit embed** — a Markdown image `![caption](relpath)` embeds a **figure**;
  `[table:relpath]` embeds a **table** (its CSV is rendered inline). Paths are relative to the
  report's own directory.

Reports live in **both** scopes, citing the same way:
`program/reports/<slug>/report.md` (cross-experiment) or `<exp>/reports/<slug>/report.md`
(per-experiment). One `report.md` per directory (or pass the file explicitly).

```markdown
---
title: "Why ASO 154 is the lead"
date: "2026-06-14"
---
A single P1 ICV 10 µg dose knocked down Ube3a by ~45–51% in WT neocortex (Welch p<0.01)
[claim:K1-230102::test_K1_230102.py::test_wt_neocortex_knockdown].

![Fig 1. 154 vs 64 across the progression.](../../analysis/fig/lead_vs_backup_window.png)

Source data:

[table:../../analysis/tables/lead_vs_backup_progression.csv]
```

## Report-specific figures via a program-level derivation

A cross-experiment report usually needs a *new* comparison plot/table no single experiment
produced. Make it through a **grounded derivation** — the same `grounding.derivation(...)`
machinery as `derive.py` — so the artifact is sha-pinned and its inputs recorded; the report
then embeds the tracked artifact. **No ad-hoc, untracked graphics.**

A *program-level* derivation opens the derivation on the `program` accessor and writes under
`program/analysis/`, recording provenance into `program/experiment.yml`:

```python
# program/analysis/derive.py
from scientist.experiments import program, canonical, k1_230301, k1_251001  # etc.

def main():
    from scientist import grounding
    with grounding.derivation(program, __file__) as d:          # program-level
        df = progression_table()                                # reads other exps' analysis tables
        d.write_table("lead_vs_backup_progression.csv", df)
        d.write_fig("lead_vs_backup_window.png", progression_figure(df))
```

Join heterogeneous labels with the canonical-id convention (`program.canonical` /
`conventions.yml: entity_resolution`) so e.g. in-vivo `ASO-64` and in-vitro `ASO3607_64`
resolve to the same molecule.

## `sci report` — validate, audit, render

```bash
SCIENTIST_HOME=<data root> uv run --with-editable skills/scientist \
  skills/scientist/scripts/sci.py report <report.md|dir> [--audit-only] [--format pdf|md] \
  [--out PATH] [--strict] [--json]
```

What it checks (a finding fails the run, exactly as `sci trace` flags a broken chain):

1. **Citations resolve to live grounded claims.** Each `[claim:<id>]` must resolve in the
   claim index; an unresolved or ambiguous id is **blocking**. A resolved claim that is
   **not** grounded — contradicted (`xfail`), drifted (`failed`), unverifiable (`skipped`),
   or weak/unspecified strength — cited as positive support is **blocking**, surfaced with its
   real `outcome/strength` (a contradicted claim is never presented as fact).
2. **Exhibits are current sha-pinned artifacts.** Each embedded figure/table must exist and
   its bytes must match an `artifact_sha256` recorded in some `experiment.yml` provenance
   ledger. An untracked (ad-hoc) or drifted exhibit is **blocking** → regenerate the
   derivation and re-embed.
3. **Uncited quantitative prose** (advisory). A paragraph asserting a quantitative result with
   no `[claim:]` anywhere in it is flagged for the agent's authoritative semantic pass
   ([review-audit.md](review-audit.md) §3). Advisory by default; `--strict` makes it fail.

Then it **renders** the validated Markdown to PDF: pandoc + a LaTeX engine when on PATH
(best quality), else a pure-Python `markdown` + `xhtml2pdf` fallback (the `[report]` extra).
Citations become a numbered **"Grounded claims"** appendix — each cited claim's statement +
`claim_id` + outcome/strength — so the human PDF is itself traceable. Exit 0 on a clean
audit + successful render, 1 otherwise.

Run the claims first so the index is current:

```bash
pytest "<exp>/analysis/claims" --grounding-out "<exp>/analysis"   # per cited experiment
pytest "program/claims" --grounding-out "program/analysis"        # the program spine
sci report program/reports/<slug>            # audit + render
```

## Maintaining (for agents working ON scientist)

- The report node sits atop the provenance DAG; its citations extend `sci trace` (claim →
  artifact → data → raw). Indexing the finished report into libkit (`kind=report`) is the
  remaining §5 follow-up — not yet wired.
- **`sci reproduce` is per-experiment-scoped.** A program-level derivation legitimately reads
  *other experiments'* `analysis/` tables, so reproduce's `reads_only_data` invariant reports
  `no` for it (the `runs`/`reproduces` verdicts still hold). Treat the cross-experiment read
  set as expected for program derivations; a program-aware reproduce mode is open work.
- The audit's uncited-prose check is a deliberately conservative **heuristic** (per paragraph,
  not per line) — the authoritative "every result maps to a grounded claim" pass is the agent's
  semantic review, identical to §3.
