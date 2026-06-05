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
  format readers (`.xlsx`, `.pzfx`); `scripts/extract.py` is the runner; nothing here
  knows about any specific experiment.
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

## Commands

```
extract.py "<exp>"            # dry run → previews in data/_preview/, repo untouched
extract.py "<exp>" --commit   # write data/*.csv + record provenance in experiment.yml
audit.py   "<exp>"            # re-extract and check data/ against raw/
```

Run with `uv run scripts/extract.py …` (PEP 723 deps: openpyxl, pyyaml).

## Output: naming & shape

`data/` files follow `NN_<assay>_<content>[__<partition>].csv` and prefer one tidy
long-format file (split dimension as a column) over many wide per-partition files.
Full convention + controlled assay vocabulary: `references/naming.md`.

## Provenance & audit

`--commit` records, per output, the raw inputs it came from (path + sha256) under
`data_provenance` in `experiment.yml`. `audit.py` then re-runs the recipe and checks:

1. **Determinism** — two runs are byte-identical.
2. **Grounding** — raw inputs exist; recorded input sha256s still match.
3. **data/ ↔ recipe** — every output is present in `data/` and byte-identical
   (i.e. `data/` *is* `extract(raw)`); files the recipe doesn't produce are flagged
   (legacy / hand-curated / non-conforming).
4. **Reconciliation** — no measurement value in `data/` is missing from the
   extraction (lost data is a finding); faithful extras are reported.
5. **Naming** — files follow the convention.

A faithful extraction is a strict, grounded superset of any prior hand-curated
`data/`; the audit surfaces where the old files dropped or reshaped real data.
