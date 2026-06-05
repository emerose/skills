# Archivist roadmap — the provenance pipeline (TODO)

The north star: make every internal claim **justifiable and traceable to original
measurements**, and let archivist *enforce* that — or mechanically detect where the
chain breaks. Today archivist indexes/searches/catalogs and tracks per-artifact
provenance (each artifact's `experiment.yml` `provenance` lists the input files it was
verified against, with sha256). The work below extends that into an end-to-end,
auditable chain:

```
raw/  (CRO originals)  →  data/  (extracted measurements)  →  analysis/  (re-derivations)  →  reports/ + README.md  (claims)
```

Each arrow is a generation step that records provenance; each arrow also gets an audit
that re-checks grounding. **These may end up as one skill or several** — current lean is
*separate, composable skills* sharing the existing `experiment.yml` provenance model
(each independently runnable and auditable), but the organization is still open.

## 1. Extraction — `raw/` → `data/`
A skill that extracts raw measurements (values, tables, fitted parameters) from
CRO-supplied files — `.xlsx`, GraphPad `.pzfx`, `.pptx`, `.docx`, PDF — into tidy CSV
(or similar) under `data/`, recording provenance for each output down to **which source
file + sheet / cell-range / slide / table** each value came from (extending
`provenance.inputs` toward cell/range granularity where feasible). Deterministic and
re-runnable so a re-extraction is diffable.

## 2. Extraction audit — is `data/` grounded in `raw/`?
A skill that verifies each `data/*` file is faithfully grounded in the cited CRO
originals: re-extract and diff, or spot-check sampled values against the source
cell/slide. Flags values with no traceable source, drifted source files (sha mismatch,
already surfaced by `arx audit`), and transcription errors.

## 3. Analysis — `data/` → `analysis/`
A skill that generates `analysis/` notebooks (`.ipynb`) which re-derive all important
results **from `data/` alone** — knockdowns, EC50s, statistics, selection tables — with
provenance from data inputs to each derived result. The notebook should reproduce the
numbers the reports rely on.

## 4. Analysis audit — do the analyses reproduce?
A skill that re-runs `analysis/*` and checks the outputs reproduce the stated results
(deterministic, environment-pinned). Flags analyses that don't run, don't reproduce, or
pull inputs from outside `data/`.

## 5. Claims grounding — `reports/` + `README.md` ← `analysis/`
A skill that reviews every claim in `reports/` and `README.md` and checks it is
justified by an analysis under `analysis/` (and transitively by `data/` → `raw/`). Flags
claims with no supporting analysis, numbers that disagree with the analysis output, and
missing caveats. This tightens the existing semantic-audit discipline
(`references/auditing.md`) to require a **mechanical analysis backing** — not merely
consistency with a source file.

## The DAG / enforcement
Together these form a dependency DAG `raw → data → analysis → report`, each edge
carrying provenance. Archivist should be able to:

- walk the chain for any claim back to original measurements,
- detect breaks (unsourced data value, non-reproducing analysis, ungrounded claim,
  drifted input), and
- report a per-experiment **traceability status** alongside the current structural
  (`arx check`) and staleness (`arx audit`) checks.

Build on the existing `provenance` model and `arx review` / `arx audit` rather than a
parallel mechanism.

## Open questions
- One skill or several? (Lean: separate composable skills, shared provenance model.)
- Provenance granularity for `data/` — file vs sheet vs cell-range.
- Extraction fidelity for `.pzfx` / `.pptx` / `.docx` — which need bespoke parsers vs a
  LibreOffice / markitdown conversion step.
- Reproducible-notebook environment pinning (e.g. `uv` / PEP 723 per notebook).
