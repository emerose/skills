# Analyst authoring playbook

How to add the analysis + claims layer to one experiment. Mirror the three pilot
experiments (`K1-210701`, `K1-230203`, `K1-230402`) — they cover the common shapes.

## 0. Setup (once)

```
uv venv && uv pip install -e "skills/analyst[reports]"
export KICHO_ROOT="…/05 - Scientific Data"
```

Smoke test: `python -c "from kicho import k1_210701 as k; print(dir(k), k.qpcr_summary.shape)"`.

## 1. Understand the experiment

- `dir(k)` lists the `data/` tables; read `k.meta` (experiment.yml) and the README.
- Decide what analysis the experiment needs: group stats, a curve fit (EC50/Hill), a
  per-ASO summary, figures. **Most analysis artifacts are cited by no claim** — they are
  deliverables in their own right.
- Decide the handful of claims worth grounding. Aim for **≥1 of each kind that applies**:
  - **result** — a measured outcome (a knockdown %, an EC50, a significant difference).
  - **design** — a fact about how the study was run (n/group, dose, #doses), grounded in
    the data shape / the treatment key.
  - **external** — a quote from a CRO report, via `doc(path)` + a substring check on the
    extracted text (sha-pinned). Needs `[reports]` extras.
  - **interpretive** — a judgment (a caveat, a mechanism), `strength="weak"`, grounded in
    a data signature where possible (e.g. bimodal response ⇒ inconsistent delivery).

## 2. Write `analysis/derive.py`

- Plain importable functions, reading via `kicho` (provenance auto). Put **choices** in
  code + comments (fit model, point exclusions, normalization, which probe/replicate).
- A `main()` writes artifacts through `analyst.derivation(k, __file__)`:
  `d.write_table(name, df)` and `d.write_fig(name, fig)`. This records analysis provenance
  into `experiment.yml` (artifact + sha, inputs = data files read + the recipe).
- **Fit determinism**: fix `p0`/bounds, pin versions (pyproject); the pilot's refit
  reproduced the CRO's reported EC50s to <0.01 log units. Compare with tolerance, never
  byte-identical.
- Run it: `python "<exp>/analysis/derive.py"`. Re-running must be stable.

## 3. Write `analysis/claims/`

- `conftest.py`: the 4-line fixture exposing the Study (copy from a pilot; rename the id).
- `test_*.py`: one test per claim. Docstring = statement; `@kind/@strength/@caveats`;
  body re-derives the number via `kicho` (or `k.analysis.*` / `k.derive.fn(k)`); `assert`
  is the grounding/drift check; `evidence(**kv)` records headline numbers.
- Reuse derivation helpers via **`k.derive.fn(k)`** (loads `analysis/derive.py` collision-
  free) — never `sys.path.insert` + `import derive` (every experiment's file is named
  `derive`, so they collide in `sys.modules` when run together).
- `pytest "<exp>/analysis/claims"` → check the grounding report renders and the reconcile
  lint is quiet (no dead fixtures / undeclared reads / bypasses).

## 4. De-overloading `extract.py` (only if a derived table lives in it)

If `data/extract.py` *computes* a table (mean/SEM/KD%/a fit), move it here:

1. If the computation needs geometry the faithful dump dropped, **enrich the dump** so it
   is recoverable (e.g. K1-230203's `02` gained a `row` column to keep slot A/B) — this
   keeps `02` a faithful *superset*, not a new computation.
2. Re-extract: `uv run skills/extractor/scripts/extract.py "<exp>" --commit`.
3. Delete the computed `data/NN_*.csv` and strip its `experiment.yml` provenance entry.
4. Re-derive it in `analysis/derive.py` from the (enriched) faithful dump; confirm it
   reproduces the old values (diff against the git copy).
5. **Re-verify**: `audit.py "<exp>"` and `cellcov.py "<exp>"` must stay CLEAN.

If this ripples too far (large README rewrites, many consumers), **leave `extract.py`
as-is and recommend the de-overload as a follow-up** instead.

## 5. Temporal corrections (when a value changes)

When re-derivation changes a number (the K1-230203 case: ASO 73 81%→88.7% after restoring
a dropped top dose), encode the change as a **git edit**, not a silent overwrite:

- Commit the corrected statement/value and the updated `@strength` together, with a commit
  message stating the as-of rationale. `git blame` on the marker + `git log -L` on the
  test function are the belief-change ledger. No YAML, no hand-maintained history.

## Gotchas

- **Identifier columns** (`aso` ids like `01`, `08`) round-trip through pandas inference
  as floats. Coerce in the claim (`int(float(row["aso"]))`) or compare numerically. A
  spec refinement (dtype hints) is noted in the pilot report.
- The data repo's `.git` is on a Google-Drive mount: `commit`/`add` work in place, but
  `push`/`fetch` fail with "mmap timed out". Push via
  `cp -R .git /tmp/x.git && git --git-dir=/tmp/x.git push origin <branch>`.
- `grounding_report.{md,json}` and `__pycache__` are generated — gitignore them in each
  `analysis/`.
