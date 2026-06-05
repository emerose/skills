---
name: extractor
description: >-
  Extract raw measurements out of CRO-supplied source files (Excel .xlsx, GraphPad
  Prism .pzfx, and more) into tidy, deterministic CSV files under an experiment's
  data/ folder, recording per-file provenance (which raw file + sha256 each output
  came from), and audit that the data/ files are faithfully grounded in raw/. Each
  experiment owns a data/extract.py recipe holding its specific layout/tweaks; this
  skill provides the generic readers + runner + audit so idiosyncrasies stay in the
  experiment, not the skill. Use it whenever the user wants to turn CRO spreadsheets/
  Prism files into clean data tables, (re)generate or refresh an experiment's data/
  CSVs, check that extracted data still matches the raw sources, or set up extraction
  for a new experiment. Companion to the archivist skill (it produces the data/ that
  archivist indexes and that analyses build on). Triggers include "extract the data
  from this CRO file," "regenerate the data/ CSVs," "is data/ still grounded in raw/,"
  "pull the numbers out of these xlsx/pzfx files."
---

# Extractor

Turns an experiment's `raw/` CRO deliverables into tidy, grounded `data/*.csv`, and
audits that grounding. It is the `raw → data` stage of the provenance pipeline (see
`archivist/ROADMAP.md`): every extracted value traces back to a specific raw file.

## Model: generic engine + per-experiment recipe

- **The skill is generic.** `scripts/_readers.py` holds the shared, deterministic
  format readers (`.xlsx`, `.xls`, GraphPad `.pzfx`); `scripts/extract.py` is the
  runner; nothing here knows about any specific experiment.
- **Each experiment owns `data/extract.py`** — a recipe defining `build(x)` that uses
  the helpers on `x`. All experiment-specific layout, column mapping, and tweaks live
  there, so the skill never accumulates idiosyncrasies.

```python
# <experiment>/data/extract.py
def build(x):
    x.sheet("01_qpcr_cp_dcp.csv", "raw/…Cp-dCp….xlsx")           # one worksheet, faithful
    x.sheet("02_qpcr_summary.csv", "raw/…qPCR….xlsx", sheet="Test ASOs")
    x.crc_long("03_crc_pct_kd.csv", "raw/…CRC graphs.pzfx")      # pzfx → tidy long
    # bespoke cases: read raw rows and emit your own table
    _, rows = x.xlsx("raw/…EC50s….xlsx", sheet="EC50s", drop_blank_rows=False)
    x.table("04_ec50_values.csv", COLS, [r[:14] for r in rows[1:] if r[0]], sources=["raw/…EC50s….xlsx"])
```

`x` helpers: `sheet(out, src, sheet=…)`, `crc_long(out, src)`, `table(out, header, rows, sources)`,
and the raw readers `xlsx(src, sheet=…)` / `pzfx(src)` for custom recipes.

**Before writing a recipe, read [`references/recipes.md`](references/recipes.md)** — the
authoring playbook with the per-experiment workflow, bespoke patterns (multi-sheet
merges, banner/multi-row headers, pzfx row-titles & analyte-in-subcolumn, in-vivo long
tables, derived values in `analysis/`), how to read the audit, the faithful-superset +
legacy rules, and how to orchestrate a multi-experiment wave.

## Commands

```
extract.py "<exp>"            # dry run → previews in data/_preview/, repo untouched
extract.py "<exp>" --commit   # write data/*.csv + record provenance in experiment.yml
audit.py   "<exp>"            # re-extract and check data/ against raw/
cellcov.py "<exp>"            # full cell-coverage: is every legacy-file value covered?
```

Run with `uv run scripts/extract.py …` (PEP 723 deps: openpyxl, pyyaml, xlrd, python-docx).
Readers: `.xlsx`/`.xls` (`x.xlsx`), GraphPad `.pzfx` (`x.pzfx`), and Word report tables
(`x.docx_tables` — for CRO studies delivered only as a `.docx`/report, no spreadsheet).

`cellcov.py` is the migration/deletion check: it re-runs the recipe in-memory and, for
each legacy `data/*.csv` the recipe does NOT produce, counts cells (integers AND text,
not just measurements) whose value is absent from the produced output. `CLEAN` (exit 0)
means every legacy value is recoverable and the file is safe to delete; uncovered cells
(exit non-zero) are either real loss or shape/redundancy artifacts to confirm by hand
(see `references/recipes.md`).

## Output: naming & shape

`data/` files follow `NN_<assay>_<content>[__<partition>].csv` and prefer one tidy
long-format file (split dimension as a column) over many wide per-partition files.
Full convention + controlled assay vocabulary: `references/naming.md`.

## Provenance & audit

`--commit` records each output as an entry in `experiment.yml`'s **unified
`provenance` list** — the same list (and entry shape) archivist uses for `README.md`.
A data entry is `artifact: data/<file>` with its raw sources **and the recipe
(`data/extract.py`)** recorded as `inputs` (path + sha256); the artifact path is the
only thing distinguishing an extraction edge (`data/…`) from a review edge
(`README.md`). So `raw → data → README` is one DAG in one place, and stock `arx audit`
checks the data edges too. `audit.py` additionally re-runs the recipe and checks:

1. **Determinism** — two runs are byte-identical.
2. **Grounding** — raw inputs exist; recorded input sha256s still match.
3. **data/ ↔ recipe** — every output is present in `data/` and byte-identical
   (i.e. `data/` *is* `extract(raw)`); files the recipe doesn't produce are flagged
   (legacy / hand-curated / non-conforming).
4. **Recipe** — the recorded recipe sha still matches the current `data/extract.py`
   (else the data may be stale w.r.t. its recipe).
5. **Reconciliation** — no measurement value in any pre-existing `data/` file is
   missing from the extraction, checked **per file** so redundant copies across
   legacy files don't inflate it (lost data is a finding); faithful extras reported.
6. **Naming** — files follow the convention.

A faithful extraction is a strict, grounded superset of any prior hand-curated
`data/`; the audit surfaces where the old files dropped or reshaped real data.
