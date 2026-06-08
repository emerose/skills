# Extract — `raw/` → `data/`

Turn an experiment's `raw/` CRO deliverables into tidy, grounded `data/*.csv`, and audit that
grounding. The `raw → data` stage of the pipeline: every extracted value traces back to a specific
raw file (+ sha256), recorded in `experiment.yml`'s unified `provenance` list.

## Model: generic engine + per-experiment recipe

- **The engine is generic.** Shared deterministic format readers (`.xlsx`, `.xls`, GraphPad
  `.pzfx`/`.prism`, Word report tables) + a runner + an audit. Nothing here knows any specific experiment.
- **Each experiment owns `data/extract.py`** — a recipe defining `build(x)` using helpers on `x`.
  All experiment-specific layout/column-mapping/tweaks live there, so the engine never accumulates
  idiosyncrasies.

```python
# <experiment>/data/extract.py
def build(x):
    x.sheet("01_qpcr_cp_dcp.csv", "raw/…Cp-dCp….xlsx")           # one worksheet, faithful
    x.sheet("02_qpcr_summary.csv", "raw/…qPCR….xlsx", sheet="Test guides")
    x.crc_long("03_crc_pct_kd.csv", "raw/…CRC graphs.pzfx")      # pzfx → tidy long
    _, rows = x.xlsx("raw/…EC50s….xlsx", sheet="EC50s", drop_blank_rows=False)
    x.table("04_ec50_values.csv", COLS, [r[:14] for r in rows[1:] if r[0]], sources=["raw/…EC50s….xlsx"])
```

`x` helpers: `sheet(out, src, sheet=…)`, `crc_long(out, src)`, `table(out, header, rows, sources)`,
raw readers `xlsx(src, sheet=…)` / `pzfx(src)` for custom recipes, and `docx_tables` for CRO studies
delivered only as a Word report.

**Before writing a recipe, read the authoring playbook** —
[recipes.md](recipes.md): per-experiment
workflow, bespoke patterns (multi-sheet merges, banner/multi-row headers, pzfx row-titles &
analyte-in-subcolumn, in-vivo long tables, moving derived values to `analysis/`), how to read the
audit, the faithful-superset + legacy rules, and how to orchestrate a multi-experiment wave.

## Commands

```
uv run skills/scientist/scripts/sci.py extract "<exp>"            # dry run → previews in data/_preview/, repo untouched
uv run skills/scientist/scripts/sci.py extract "<exp>" --commit   # write data/*.csv + record provenance in experiment.yml
uv run skills/scientist/scripts/sci.py audit   "<exp>"            # re-extract and check data/ against raw/
uv run skills/scientist/scripts/sci.py cellcov "<exp>"            # full cell-coverage: is every legacy-file value covered?
```

The `sci.py` script is zero-install (PEP 723 deps: openpyxl, pyyaml, xlrd, python-docx, pdfplumber) —
`uv run` pulls them without a virtualenv. The Prism reader sniffs and routes `.pzfx` vs `.prism` by
content; legacy binary Prism raises a clear "re-export" error.

`sci.py cellcov` is the migration/deletion check: re-runs the recipe in-memory and, for each legacy
`data/*.csv` the recipe does NOT produce, counts cells (integers AND text) whose value is absent from
produced output. `CLEAN` (exit 0) = every legacy value is recoverable, safe to delete; uncovered
cells = real loss or shape/redundancy artifacts to confirm by hand.

## Output: naming & shape

`data/` files follow `NN_<assay>_<content>[__<partition>].csv` and prefer one tidy long-format file
(split dimension as a column) over many wide per-partition files. Full convention + controlled assay
vocabulary: [naming.md](naming.md).

## Provenance & audit

`--commit` records each output as an entry in the unified `provenance` list — a data entry is
`artifact: data/<file>` with its raw sources **and the recipe (`data/extract.py`)** as `inputs`
(path + sha256). The artifact path is the only thing distinguishing an extraction edge (`data/…`)
from a review edge (`README.md`), so `raw → data → README` is one DAG. `sci.py audit` re-runs the recipe
and checks: **determinism** (byte-identical reruns), **grounding** (inputs exist, shas match),
**data/ ↔ recipe** (every output present + byte-identical), **recipe sha** still current,
**reconciliation** (no measurement value in any pre-existing `data/` file is missing, checked
per-file), and **naming**. A faithful extraction is a strict grounded superset of any prior
hand-curated `data/`.
