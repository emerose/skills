# Analyst authoring playbook

How to add the analysis + claims layer to one experiment. Mirror the three pilot
experiments (`K1-000000`, `K1-000001`, `K1-000002`) — they cover the common shapes.

## 0. Setup

**Never work in the Google-Drive checkout of the data repo.** It is one working tree /
index / HEAD that GitSync keeps fast-forwarded to `origin/main`, and every concurrent
fan-out unit shares it — so HEAD wanders mid-task, a plain `git commit` sweeps a sibling's
staged files, and pushing off the Drive hits "mmap timed out". `raw/` is tracked in git, so
a unit needs nothing from the Drive: provision an isolated, off-Drive worktree instead.

```
# install the package once (in the skills repo)
uv venv && uv pip install -e "skills/analyst[reports]"

# provision a per-unit worktree off a local clone of origin/main, and point
# EXPERIMENTS_ROOT at it (clones ~1GB the first time, then each unit is ~instant):
eval "$(skills/analyst/scripts/new-unit.sh k1-000000)"
```

Do all edits, `python derive.py`, and `pytest` in that worktree; commit there (scoped to
your experiment dir) and `git -C "$EXPERIMENTS_ROOT" push origin analyst/k1-000000` straight
to GitHub; open one PR. After it merges, `skills/analyst/scripts/new-unit.sh --remove
k1-000000`. (See the header of `scripts/new-unit.sh` for env overrides + teardown.)

Smoke test: `python -c "from experiments import k1_000000 as k; print(dir(k), k.assay_summary.shape)"`.

## 1. Understand the experiment

- `dir(k)` lists the `data/` tables; read `k.meta` (experiment.yml) and the README.
- Decide what analysis the experiment needs: group stats, a curve fit (EC50/Hill), a
  per-target summary, figures. **Most analysis artifacts are cited by no claim** — they are
  deliverables in their own right.
- Decide the handful of claims worth grounding. Aim for **≥1 of each kind that applies**:
  - **result** — a measured outcome (a knockdown %, an EC50, a significant difference).
  - **design** — a fact about how the study was run (n/group, dose, #doses), grounded in
    the data shape / the treatment key.
  - **external** — a quote from a CRO report (PDF/docx) **or a TC slide deck (.pptx)**,
    via `doc(path)` → `ref.contains("verbatim phrase")` (a sha-pinned, whitespace-normalized
    substring check; `ref.text()` if you need the raw text). Don't hand-roll pdfplumber/
    python-docx/python-pptx — `DocRef` dispatches on suffix. Needs `[reports]` extras.
    A `.pptx` deck is **weaker** evidence than a signed report: cap at `@strength("moderate")`
    with a caveat that the source is a presentation, and match *short* phrases (deck text is
    scattered across shapes/notes; `ref.is_presentation` flags it). Legacy `.doc`/`.ppt`
    aren't supported (re-save as `.docx`/`.pptx`/PDF).
  - **interpretive** — a judgment (a caveat, a mechanism), `strength="weak"`, grounded in
    a data signature where possible (e.g. bimodal response ⇒ inconsistent delivery).

## 2. Write `analysis/derive.py`

- Plain importable functions, reading via `experiments` (provenance auto). Put **choices** in
  code + comments (fit model, point exclusions, normalization, which probe/replicate).
- A `main()` writes artifacts through `analyst.derivation(k, __file__)`:
  `d.write_table(name, df)` and `d.write_fig(name, fig)`. This records analysis provenance
  into `experiment.yml` (artifact + sha, inputs = data files read + the recipe).
- **Fit determinism**: fix `p0`/bounds, pin versions (pyproject); the pilot's refit
  reproduced the CRO's reported EC50s to <0.01 log units. Compare with tolerance, never
  byte-identical.
- Run it: `python "<exp>/analysis/derive.py"`. Re-running must be stable.

## 3. Write `analysis/claims/`

- **No conftest needed.** Request the `experiment` fixture — it resolves the Study from
  the test file's path. `test_*.py`: one test per claim. Docstring = statement;
  `@kind/@strength/@caveats`; body re-derives the number via `experiment` (or
  `experiment.analysis.*` / `experiment.derive.fn(experiment)`); `assert` is the
  grounding/drift check; `evidence(**kv)` records headline numbers.
- Reuse derivation helpers via **`experiment.derive.fn(experiment)`** (loads
  `analysis/derive.py` collision-free) — never `sys.path.insert` + `import derive` (every
  experiment's file is named `derive`, so they collide in `sys.modules` when run together).
- Cross-experiment claims import another study and wrap it in `cross(...)`:
  `from experiments import k1_000000; other = cross(k1_000000)`.
- `pytest "<exp>/analysis/claims"` → check the grounding report renders and the reconcile
  lint is quiet (no empty claims / undeclared reads / bypasses). Add `--check-drift` to
  flag claims whose inputs changed since their `@strength` was last set.

## 4. De-overloading `extract.py` (only if a derived table lives in it)

If `data/extract.py` *computes* a table (mean/SEM/KD%/a fit), move it here:

1. If the computation needs geometry the faithful dump dropped, **enrich the dump** so it
   is recoverable (e.g. K1-000001's `02` gained a `row` column to keep slot A/B) — this
   keeps `02` a faithful *superset*, not a new computation.
2. Re-extract: `uv run skills/extractor/scripts/extract.py "<exp>" --commit`.
3. Delete the computed `data/NN_*.csv` and strip its `experiment.yml` provenance entry.
4. Re-derive it in `analysis/derive.py` from the (enriched) faithful dump; confirm it
   reproduces the old values (diff against the git copy).
5. **Re-verify**: `audit.py "<exp>"` and `cellcov.py "<exp>"` must stay CLEAN.

If this ripples too far (large README rewrites, many consumers), **leave `extract.py`
as-is and recommend the de-overload as a follow-up** instead.

## 5. Temporal corrections (when a value changes)

When re-derivation changes a number (e.g. a value corrected from ~60% to ~70% after restoring a dropped top-dose point), encode the change as a **git edit**, not a silent overwrite:

- Commit the corrected statement/value and the updated `@strength` together, with a commit
  message stating the as-of rationale. `git blame` on the marker + `git log -L` on the
  test function are the belief-change ledger. No YAML, no hand-maintained history.

## Gotchas

- **Identifier columns** (`guide_id` ids like `01`, `08`) are preserved as strings by the
  tracked loader (it detects integer-looking columns that inference would corrupt — a
  leading zero, or blanks forcing a float — and keeps the exact text). Compare them as
  strings (`row["guide_id"] == "73"`). Measurement columns stay numeric.
- The data repo's `.git` is on a Google-Drive mount: `commit`/`add` work in place, but
  `push`/`fetch` fail with "mmap timed out". Push via
  `cp -R .git /tmp/x.git && git --git-dir=/tmp/x.git push origin <branch>`.
- `grounding_report.{md,json}` and `__pycache__` are generated — gitignore them in each
  `analysis/`.
