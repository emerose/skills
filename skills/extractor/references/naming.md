# `data/` file naming & shape convention

Extracted measurement files under an experiment's `data/` follow one convention so
a folder listing reads cleanly and tooling can predict names.

```
NN_<assay>_<content>[__<partition>].csv
```

## Rules

1. **lowercase `snake_case`, `.csv`, ASCII, no spaces.**
2. **No redundant source IDs in the name.** Drop the experiment id (`K1-…`), CRO
   phase/experiment numbers (`Phase_5_`, `Exp10_`), and vendor names (`McSwiggen_`,
   `PYW001_`). The folder already identifies the experiment and `experiment.yml`
   provenance records the exact source file. Keep a vendor/source token *only* when
   it genuinely distinguishes two same-assay datasets in the same experiment.
3. **`NN_`** — two-digit prefix giving **pipeline order**: low→high reads
   raw→processed→derived (`01_` raw per-observation, then summaries, then fits).
   This orders the listing top-to-bottom. Renumber on mid-sequence inserts (rare
   once an experiment is extracted).
4. **`<assay>`** — short tag from the controlled vocabulary below (extensible; add
   new tags here when a new assay appears, don't invent ad-hoc ones).
5. **`<content>`** — concise descriptor of what the table holds: `cp_dcp`,
   `summary`, `pct_kd`, `values`, `fold_change`, `concentrations`, `viability`, …
6. **`__<partition>`** — optional suffix, only when a dataset is *genuinely* split
   across files (see shape rule). Consistent keys joined with `_`: `__24h_donor1`,
   `__plate2`, `__cortex`.
7. **One assay + measurement per file.**

## Shape: prefer one tidy long-format file

Default to **a single long-format (tidy) file** with the split dimension as a
**column**, rather than many wide per-partition files. E.g. the per-plate CRC tables
from a `.pzfx` become **one** `0N_crc_pct_kd.csv` with `plate, aso, log_conc,
replicate, pct_kd` columns — not `…__plate1/2/3.csv`. One row per observation; the
machine-readable partition lives in a column, where it's queryable and joinable.

Use `__<partition>` files **only** when the partitions can't be cleanly merged into
one table (incompatible columns, or sources that are inherently separate and
carry no shared key). When you do split, say why in the extraction recipe.

## Controlled assay vocabulary (extend as needed)

`qpcr` · `ddpcr` (droplet/digital PCR) · `quantigene` (bead-based bDNA mRNA) ·
`crc` (concentration-response) · `ec50` · `protein` (capillary/Simple Western, e.g.
Jess) · `viability` · `cytokine` · `immunotox` · `mea` · `pk` · `biodist` · `tox` ·
`bw` (body weight) · `hematology` · `chemistry` (clinical chemistry) ·
`clinical` (clinical observations) · `histo` · `neuro` ·
`survival` (in-vivo animal survival) · `seq` (sequencing/variants) ·
`design` (ASO design tables) · `sample` (sample/tissue inventory & QC) ·
`summary` (cross-assay per-subject rollup tables)

## Content descriptors (common)

`cp_dcp` (raw qPCR Cp/ΔCp per well) · `summary` (per-ASO averages / %CV / %KD) ·
`pct_kd` · `ec50` / `values` · `fold_change` · `concentrations` · `ranking`.

## Examples

**K1-211101 (Rat Fibroblast ASO Validation)**
- `01_qpcr_cp_dcp.csv` — raw per-well Cp / VIC / ΔCp
- `02_qpcr_summary.csv` — per-ASO AVE/STDEV/%CV (the "Test ASOs" sheet)
- `03_crc_pct_kd.csv` — long: `plate, aso, log_conc, replicate, pct_kd` (from the 3 pzfx plate tables)

**K1-210701 (Potency Determination)**
- `01_qpcr_cp_dcp.csv`
- `02_qpcr_summary.csv`
- `03_ec50_values.csv`
- `04_crc_pct_kd.csv` — long, with a `plate` column (from the 4 pzfx plate tables)

## Migration note

This convention applies to data produced by the extractor. Existing `data/` files
are renamed/reshaped to match **as each experiment is (re)processed through
extraction** — not in a separate mass-rename. The extraction audit reports legacy
files that don't yet conform.
