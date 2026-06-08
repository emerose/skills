# Derive & claims — `data/` → `analysis/` → grounded claims

Adds (1) **re-derivable analysis** of each experiment (EC50 fits, stats, summaries, figures) traced
to `data/`, and (2) **grounded claims** — every scientific assertion linked to the exact evidence
that justifies it, re-runnable for audit, with non-binary support (strength) and a git-based
temporal history. Closes the pipeline `raw → data → analysis → claims`.

> The machinery lives in the `scientist` package as the top-level `analyst` + `experiments`
> packages plus the pytest plugin (auto-loaded via the `pytest11` entry point). Run claims
> zero-install with `uv run --with-editable skills/scientist pytest <exp>/analysis/claims`.
> Full design: [SPEC.md](../SPEC.md). Step-by-step authoring: [references/playbook.md](playbook.md).

## Two packages (the generic machinery)

- **`experiments`** — typed, tracked data access. `from experiments import k1_000000 as k` resolves
  the `K1-000000 *` folder under `$EXPERIMENTS_ROOT` and returns a `Study`. Tidy `data/` tables are
  attributes (`02_assay_summary.csv` → `k.assay_summary`, drop the `NN_`). Lazy, cached,
  **sha-pinned**, recorded as provenance on every read. `k.meta` = `experiment.yml`; `k.analysis.<name>`
  = a derived table; `k.derive` = the experiment's `analysis/derive.py`. DataFrames carry
  `.attrs["source"]`/`["sha256"]`.
- **`analyst`** — the harness + pytest plugin: `load()/data()` (tracked loader), `doc()` (record a CRO
  report PDF/docx **or a .pptx deck**; `DocRef.text()`/`.contains()` extract + quote-match it),
  `evidence(**kv)`, `uses(claim_id)` (compose, transitive provenance), `cross(study)`,
  `derivation(study, __file__)`, and the `@strength`/`@caveats`/`@kind` markers. The plugin captures
  provenance per claim, **bypass-guards** untracked reads, runs a **reconcile lint**, and emits the
  **grounding report**.

**Work in an isolated worktree, never the Drive checkout** — the Drive checkout is one shared
working tree/HEAD that GitSync owns; concurrent fan-out racing it corrupts commits. Provision an
off-Drive worktree: `eval "$(skills/scientist/scripts/new-unit.sh k1-000000)"`.

## Per-experiment layout

```
<exp>/analysis/
  derive.py            # derivation CODE: summary tables, stats, EC50/Hill fits, figures
  tables/*.csv         # derivation OUTPUTS (artifacts, analysis-provenance-tracked)
  fig/*.png            # figures (artifacts; a claim may cite one)
  claims/test_*.py     # grounding specs: each test IS a claim
```

`extract.py` stays under `data/` and is **faithful only**. If a derived value (mean/SEM/KD%/fit)
crept into it, **de-overload**: move that computation to `derive.py`, delete the computed `data/`
file + its provenance, re-run `audit.py` + `cellcov.py` to confirm CLEAN.

### derive.py — re-derivable products

Plain importable functions (no decorators) that read via `experiments` (provenance auto-captured).
A `main()` writes artifacts through a `derivation` context (records analysis provenance into
`experiment.yml`: artifact + sha, inputs = data files read + this recipe):

```python
def ec50_table(k):
    dr = k.dose_response                  # tracked read; scipy Hill fit; document exclusions in comments
    return df

def main():
    import analyst
    from experiments import k1_000000 as k
    with analyst.derivation(k, __file__) as d:
        d.write_table("ec50_summary.csv", ec50_table(k))
        d.write_fig("dose_response_fits.png", plot_fits(k))
```

### claims/test_*.py — a claim IS a pytest test

Request the `experiment` fixture (ships with the plugin; resolves the Study from the test file path):

```python
import pytest
from analyst import strength, caveats, kind, evidence, uses

@kind("result")                                   # result | design | external | interpretive
@strength("strong")                               # strong | moderate | weak | unverifiable | ...
@caveats("single positive-control series; n=2 wells at the top dose")
def test_pos_ctrl_below_criterion(experiment):
    "Positive-control guide ctrl-1 ~45% knockdown at the 100 nM top dose — below the >60% criterion."
    q = experiment.assay_summary                   # tracked read (captured as provenance)
    kd = q[(q["guide_id"]=="ctrl-1") & (q["conc_nm"]==100)]["pct_kd"].mean()
    evidence(kd_pct=round(kd,1), criterion_pct=60)
    assert kd == pytest.approx(45, abs=3) and kd < 60
```

- **docstring** = statement · **node id** = stable id · **`experiment` (+ reads)** = inputs · **body**
  = justification · **assert** = grounding/drift check · **markers** = the non-binary judgment (kept
  *out* of the assert).
- **bulk** via `@pytest.mark.parametrize`. **compose** via `uses("other_claim_id")`. **cross-experiment**:
  `from experiments import k1_000000; other = cross(k1_000000)` (reads captured, sha-pinned).
- **lifecycle** = pytest states: `@pytest.mark.xfail(strict=True)` = contradicted but on record;
  `pytest.skip(reason=…)` = unverifiable.
- **identifiers**: id columns that only look numeric (leading zeros) are preserved as **strings** —
  compare `row["guide_id"] == "73"`, not `== 73`. Measurement columns stay numeric.
- **fit determinism**: pin versions; compare derived floats with `pytest.approx` / log-tolerance.

### External claims — quoting a report or deck

`doc(path)` sha-pins the cited bytes and returns a `DocRef`. Don't hand-roll extraction: call
`ref.text()` (`.pdf`/`.docx`/`.pptx`, offline + deterministic) or `ref.contains("verbatim phrase")`
(whitespace-normalized substring — robust to prose split across lines/runs/cells).

```python
@kind("external")
@strength("strong")                                    # signed report → strong is OK
def test_report_no_mortality(experiment):
    "The signed CRL report states: 'no mortality was observed...'."
    ref = doc(os.path.join(experiment.path, REPORT))
    assert ref.contains("no mortality was observed")
```

- **Decks are weaker evidence.** A TC `.pptx` is a summary, not a signed deliverable. Cap deck-grounded
  external claims at `@strength("moderate")`, note the source in `@caveats`, and match **short** verbatim
  phrases (`ref.is_presentation` flags the deck for reviewers). Legacy `.doc`/`.ppt` aren't supported —
  re-save as `.docx`/`.pptx`/PDF.

## Run it

Claims auto-load the plugin via the `pytest11` entry point; run them zero-install with
`uv run --with-editable skills/scientist` (no persistent install):

```
uv run --with-editable skills/scientist pytest "<exp>/analysis/claims"                 # one experiment
uv run --with-editable skills/scientist pytest <exp1>/analysis/claims <exp2>/... --grounding-out DIR   # combined
uv run --with-editable skills/scientist pytest <…>/analysis/claims --check-drift       # also flag stale claims
```

Program-wide rollup: `EXPERIMENTS_ROOT=… uv run --with-editable skills/scientist python skills/scientist/scripts/rollup.py`.

Emits `grounding_report.md` + `.json` (per claim: `{id, statement, outcome, kind, strength, caveats,
evidence, inputs+shas, reconcile, drift?}`). **The grounding report is the source for indexing claims
into libkit** (see [review-audit.md](review-audit.md) and [search-index.md](search-index.md)) — each
claim becomes a searchable `kind=claim` card carrying its outcome + strength.

Program-wide rollup, drift, and the traceability story live in [review-audit.md](review-audit.md).
