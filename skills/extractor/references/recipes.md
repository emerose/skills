# Writing extraction recipes — playbook & lessons

Operational guide for extracting one experiment (`raw/` → tidy `data/`). Distilled
from real waves across qPCR, CRC, EC50, immunotox/cytokine, protein (Jess), in-vivo
ddPCR/QuantiGene, and neuro-observation data. Read this before writing a recipe.

## The model in one line

You write `<exp>/data/extract.py` defining `build(x)`. The skill's runner
(`scripts/extract.py`) constructs an `Extraction` `x` bound to the experiment, calls
`build(x)`, then writes the files and records provenance. **Recipes are pure
declarations** — no imports, file I/O, or git; just calls on `x`. Run via
`uv run scripts/extract.py "<exp>"` (dry-run) / `--commit`; verify with
`scripts/audit.py "<exp>"`.

## Per-experiment workflow

1. **Inspect `raw/`** — list files; for each `.xlsx`/`.xls` dump sheet names + dims +
   row 1 (openpyxl, or `x.xlsx`); for each `.pzfx` list table titles + columns
   (xml.etree, or `x.pzfx`). Note where the real data lives vs setup/metadata sheets.
2. **Inspect the legacy `data/`** — names, headers, row counts. The legacy files tell
   you what was considered the deliverable, but they were **hand-curated and are not
   authoritative** (they drop rows, duplicate across files, use ad-hoc names). Treat
   them as a cross-check target, not ground truth.
3. **Map raw → tidy `data/`** following `references/naming.md`
   (`NN_<assay>_<content>[__partition].csv`, lowercase, **one long file per assay** with
   split dimensions as columns).
4. **Write `build(x)`**, `extract --commit`, `audit`. **Iterate until the audit shows
   `reconcile lost = 0`** with determinism/grounding/recipe/naming all good. The only
   acceptable finding is `N legacy/non-conforming file(s) in data/` (legacy kept until
   the migration pass).
5. If `lost > 0`, open the raw source and fix the recipe — a real value isn't
   reproduced. (Don't "fix" it by moving/deleting legacy files; see below.)

## Helper API (`x`)

- `x.sheet(out, src, sheet=None, drop_blank_rows=True)` — emit one worksheet faithfully.
- `x.crc_long(out, src)` — GraphPad pzfx where **table = plate, Y column = ASO,
  subcolumn = replicate** → tidy long `[plate, aso, log_conc, replicate, pct_kd]`. Use
  ONLY when that shape genuinely fits (concentration-response %KD). It does not fit
  most non-CRC pzfx.
- `x.table(out, header, rows, sources)` — emit any table you build yourself. The path
  for everything bespoke. `sources` = the raw paths it came from (drives provenance).
- `x.xlsx(src, sheet=None, drop_blank_rows=True) -> (header, rows)` — raw rows
  (handles `.xlsx` and `.xls`).
- `x.pzfx(src) -> [(table_title, x_values, [(y_title, [subcolumns])])]` — structured
  pzfx. **Note: it does NOT expose the `RowTitlesColumn`** (see pzfx section).

`src` paths are relative to the experiment dir (e.g. `"raw/Foo.xlsx"`).

## Recipe patterns (seen in real data)

- **Multi-sheet / multi-file merge → one long file.** Sheets/workbooks with the same
  schema (plates, batches, species, channels) → concatenate with a partition column
  (`plate`, `batch`, `species`, `channel`) via `x.xlsx(...)` + `x.table(...)`. Prefer
  this over many `__partition` files. (K1-220803 species; K1-230203 batches×channels.)
- **Banner / multi-row headers.** Some sheets have a merged banner above the real
  header, or a 2-row (group + sub) header. Read with `drop_blank_rows=False`, pick the
  real header row, build flat column names, emit via `x.table`. (K1-210701 EC50
  group+sub header; cytokine MSD banner row.)
- **Derived values live in `analysis/`, not `raw/`.** Means/SEM/EC50 fits/curve params
  that aren't in any raw instrument file usually come from an analyst workbook under
  `analysis/`. Extract **that workbook** (and record it as the input) rather than
  recomputing — recomputation isn't faithful and won't reconcile. (K1-230203.)
- **In-vivo → long with subject columns.** Reshape to one row per
  observation with `tissue, region, dose_group, treatment, animal, timepoint, target`
  as columns. (K1-230102, K1-241201.)
- **Integer-only assays (e.g. neuro scores 0–5).** The audit's `reconcile` counts only
  non-integer numerics, so it reports `0 measurements` — that's expected, not a bug.
  Verify the superset claim yourself (every legacy score reproduced). (K1-241101.)

## GraphPad `.pzfx` — the tricky format

- A `.pzfx` is XML with one `<Table>` per graph. Each table has X-family columns
  (`XColumn`/`XAdvancedColumn`), `YColumn`s (each with `Subcolumn`s), and often a
  `RowTitlesColumn`.
- **`x.pzfx` / `read_pzfx_structured` deliberately omit the `RowTitlesColumn`.** When
  the row titles carry meaning (sample/ASO IDs, animal IDs), re-parse them from the XML
  yourself and align positionally to each Y subcolumn:

  ```python
  import xml.etree.ElementTree as ET
  def row_titles(path):
      tag = lambda t: t.split("}")[-1]
      out = {}
      for t in [e for e in ET.parse(path).getroot().iter() if tag(e.tag) == "Table"]:
          title = next((tag(c.tag) == "Title" and "".join(c.itertext()).strip() or None
                        for c in t if tag(c.tag) == "Title"), "")
          rt = next((c for c in t if tag(c.tag) == "RowTitlesColumn"), None)
          out[title] = ([(d.text or "").strip() for sc in rt if tag(sc.tag)=="Subcolumn"
                         for d in sc if tag(d.tag)=="d"] if rt is not None else [])
      return out
  ```
- **Layout varies a lot.** Seen: table=plate / Y=ASO / subcol=replicate (CRC, fits
  `crc_long`); Y=dose-group / subcol=plate; Y=donor×timepoint with the **analyte as the
  row index within each subcolumn** against a fixed panel order (MSD cytokines); row
  titles = samples, Y = run/timepoint. **Inspect every table before assuming a shape.**
- **Comma decimals.** Some pzfx write `1,23` not `1.23` — normalize `,`→`.` or values
  won't read as numeric (and won't reconcile). (K1-241201.)
- A `.pzfx` may be the **binary** Prism format (starts `PCFFGRA4…`), not XML — not
  parseable here; skip it (its data is usually also in an xlsx).

## Faithful superset + legacy handling

- The extraction must reproduce **every** legacy measurement (`lost = 0`) and may add
  more (`faithful extras` — rows/cols the curation dropped). It is a strict superset.
- **Do NOT move, delete, or relocate legacy `data/` files** to make the audit pass.
  The standing `N legacy/non-conforming file(s)` finding is expected and is cleared
  later by the dedicated migration pass (which also fixes references). Hiding legacy
  in a subfolder is not a fix — it just dodges the check.

### Migration / deletion: information-coverage, not byte-coverage

When deciding whether a legacy file is safe to delete, the bar is **information
coverage**: every value it holds must be recoverable from the kept (recipe-produced)
files — *modulo shape*. The float-only `lost` count in the audit is necessary but not
sufficient; run a full cell-coverage check (every cell incl. integers AND text,
numbers normalized to float, text casefolded, dates compared by value) before deleting.
Three things that look like "loss" but are not, and must NOT drive you to denormalize
the tidy files:

- **Date formatting.** Excel date cells come through as dates (`2023-05-19`), not
  `2023-05-19 00:00:00` — the engine renders a midnight datetime date-only (see
  `_readers._fmt_dt`). Hand-curated legacy date columns are then byte-comparable.
- **Tidy reshape.** Legacy `neocortex-l` becomes `tissue=Neocortex` + `hemisphere=Left`;
  a constant experiment-level column (e.g. `ASO-154` on every row) is dropped as
  redundant (it's the `exp_id`). The *information* is present; the literal combined
  string is not. This is correct — do not re-add denormalized columns to satisfy a
  byte check.
- **GraphPad RowTitle indices.** `read_pzfx_structured` omits the leading unlabeled
  row-title column by design; stray values like `0` or `11` in a legacy pzfx-derived
  CSV are these indices, not measurements.

**Some legacy files are primary, not derived.** If a value is *nowhere in `raw/`* —
e.g. a hand-entered QC `Pass`/`Fail` verdict, a study-design group→treatment map, or an
animal-accession ID series not in any CRO file — it cannot be re-extracted. Such a file
**is** source data: keep it (optionally record it in `experiment.yml` provenance as a
curated input with no raw source), never delete it. Only delete legacy files whose every
value is genuinely recoverable from the extraction.

## Reading the audit

`audit.py "<exp>"` re-extracts and reports:
- **determinism** — two runs byte-identical.
- **grounding** — raw inputs exist; recorded input sha256s still match.
- **recipe** — recorded recipe sha == current `data/extract.py` (else data is stale
  w.r.t. its recipe; re-`--commit`).
- **data/ ↔ recipe** — each output present and byte-identical; non-recipe files flagged.
- **reconcile** — per pre-existing (non-recipe) file, every non-integer measurement
  appears in the extraction. `lost > 0` ⇒ a real value isn't reproduced. Compared
  per file so duplicate copies across legacy files don't inflate it.
- **naming** — outputs match the convention.

## Running a wave (orchestration)

When extracting many experiments at once:
1. **Sync first.** `git pull` both the data repo and skill to `origin/main` *before*
   starting — the Drive auto-sync (GitSync) pauses on a dirty tree, so local `main` can
   silently fall behind a merged PR. Building on a stale base causes a rebase tangle.
2. **One agent per experiment**, briefed with: this file, `naming.md`, a couple of
   committed recipes as examples, the helper API, and the constraints (no git, don't
   move/delete legacy, iterate to `lost = 0`, return the recipe + audit lines).
3. Agents only write their own `data/extract.py` + run `extract/audit`; **you commit
   centrally** (branch off current `main`, add the experiment folders, one PR).
4. New audit edge cases will surface (NaN cells, double-counting, comma decimals were
   all found this way) — fix them in the skill, not with per-experiment hacks.

## Format support

- **Supported:** `.xlsx` (openpyxl), `.xls` (xlrd), GraphPad `.pzfx` (XML).
- **Deferred / not extracted (raw instrument or no extractable measurements):** ABI
  `.eds` (QuantStudio — but its processed data is in the accompanying xlsx, so extract
  those), Axion `.spk` (MEA spikes), Apple `.numbers`, images (`.jpg/.tiff/.png`),
  binary Prism `.pzfx`. The heterogeneous ASO-design set (`.pptx/.docx/.vcf/.txt`) is
  its own problem. Record these as raw, don't block extraction on them.
