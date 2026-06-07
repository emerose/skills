---
name: analyst
description: >-
  Add a re-derivable analysis layer and grounded, traceable scientific claims on top of
  an experiment's tidy data/ (the extractor's output). Provides `kicho` (typed, tracked
  access to data/ tables as sha-pinned pandas DataFrames) and `analyst` (a claim-grounding
  harness + pytest plugin). Each experiment owns an analysis/ folder: derive.py computes
  summary tables, stats, curve fits (EC50/Hill) and figures from data/ with analysis
  provenance; claims/test_*.py are grounding specs where each pytest test IS a claim
  (docstring = statement, node id = id, fixtures = inputs, markers = the non-binary
  judgment, assert = the grounding/drift check). Running pytest captures provenance
  automatically, bypass-guards untracked reads, and emits a grounding report (markdown +
  JSON). Use it to compute/curve-fit/summarize an experiment beyond faithful extraction,
  to assert a scientific claim with traceable evidence and strength, to re-derive a value
  the extractor should not be computing (de-overload extract.py), or to audit whether a
  claim still matches its data. Companion to the extractor (which produces the data/ this
  builds on). Triggers: "fit an EC50/dose-response," "summarize/derive analysis for this
  experiment," "make a grounded claim about this result," "is this claim still supported,"
  "move this computed table out of extract.py into analysis."
---

# Analyst

Closes the provenance pipeline `raw → data → analysis → claims`. The extractor produces
trustworthy `data/`; this layer adds (1) **re-derivable analysis** of each experiment
(EC50 fits, stats, summaries, figures) traced to `data/`, and (2) **grounded claims** —
every scientific assertion linked to the exact evidence that justifies it, re-runnable
for audit, with non-binary support (strength) and a git-based temporal history.

**Read [`SPEC.md`](SPEC.md) for the full design.** This file is the operating manual.

## Two packages (the generic machinery)

- **`kicho`** — typed, tracked data access. `from kicho import k1_210701 as k` resolves
  the `K1-210701 *` folder under `$KICHO_ROOT` and returns a `Study`. Tidy `data/` tables
  are attributes: `02_qpcr_summary.csv` → `k.qpcr_summary` (drop the `NN_` prefix). Access
  is lazy, cached, **sha-pinned**, and recorded as provenance on every read. `k.meta` =
  `experiment.yml`; `k.analysis.<name>` = a derived table under `analysis/tables/`;
  `k.derive` = the experiment's `analysis/derive.py` (loaded collision-free); `dir(k)`
  lists tables (IPython tab-completion). DataFrames carry `.attrs["source"]`/`["sha256"]`.
- **`analyst`** — the harness + pytest plugin. `load()/data()` (tracked loader), `doc()`
  (record a CRO report PDF/docx), `evidence(**kv)`, `uses(claim_id)` (compose on another
  claim, transitive provenance), `derivation(study, __file__)` (analysis-provenance
  recorder), and the `@strength`/`@caveats`/`@kind` markers. The plugin captures
  provenance per claim, **bypass-guards** untracked source reads, runs a **reconcile
  lint**, and emits the **grounding report**.

Both are installed together: `pip install -e skills/analyst` (or `uv pip install -e`).
The pytest plugin auto-loads (a `pytest11` entry point), so a bare `pytest analysis/claims/`
works. Scientific deps are version-pinned in `pyproject.toml` for fit determinism.

## Per-experiment layout (what you author)

```
<exp>/analysis/
  derive.py            # derivation CODE: summary tables, stats, EC50/Hill fits, figures
  tables/*.csv         # derivation OUTPUTS (artifacts, analysis-provenance-tracked)
  fig/*.png            # figures (artifacts; NOT claims — a claim may cite one)
  claims/
    conftest.py        # 4-line fixture exposing the Study (see below)
    test_*.py          # grounding specs: each test IS a claim
```

`extract.py` stays under `<exp>/data/` and is **faithful only**. If a derived table crept
into it (a mean/SEM/KD%/fit), move that computation here (the "de-overload": enrich the
faithful dump if needed so the value is re-derivable, delete the computed `data/` file +
its provenance, re-run `extractor/scripts/audit.py` + `cellcov.py` to confirm CLEAN).

### derive.py — re-derivable products

Plain importable functions (no decorators) that compute artifacts, reading via `kicho`
(provenance auto-captured). Analysis **choices** (fit model, point exclusions,
normalization) live in code + comments — explicit and reviewable. A `main()` writes the
artifacts through a `derivation` context, which records analysis provenance into
`experiment.yml` (artifact + sha, inputs = the data files read + this recipe):

```python
def ec50_table(k):                       # importable, IPython-friendly
    crc = k.crc_pct_kd                    # tracked read
    ...                                   # scipy Hill fit; document exclusions in comments
    return df

def main():
    import analyst
    from kicho import k1_210701 as k
    with analyst.derivation(k, __file__) as d:
        d.write_table("ec50_by_aso.csv", ec50_table(k))
        d.write_fig("crc_fits.png", plot_fits(k))
```

### claims/test_*.py — grounding specs

A claim **is** a pytest test. `conftest.py` exposes the Study as a fixture:

```python
import pytest
from kicho import k1_210701 as _study
@pytest.fixture
def k1_210701(): return _study
```

```python
from analyst import strength, caveats, kind, evidence, uses

@kind("result")                                   # result | design | external | interpretive
@strength("strong")                               # strong | moderate | weak | unverifiable | ...
@caveats("single positive-control series; n=2 wells at the top dose")
def test_pos_ctrl_below_criterion(k1_210701):
    "Positive control UBE3A ASO1 ~53% KD at 100 nM — below the >60% criterion."  # = the statement
    q = k1_210701.qpcr_summary                    # tracked read (captured as provenance)
    kd = q[(q["ASO ID"]=="UBE3A ASO1") & (q["ASO Concentration (nM)"]==100)]["AVE KD"].mean()
    evidence(kd_pct=round(kd,1), criterion_pct=60) # headline numbers for the report
    assert kd == pytest.approx(53, abs=3) and kd < 60   # the grounding / drift check
```

- **docstring** = statement · **node id** = stable id · **fixtures** = declared inputs ·
  **body** = justification · **assert** = grounding/drift check · **markers** = the
  non-binary judgment (kept *out* of the assert).
- **bulk** via `@pytest.mark.parametrize`. **compose** via `uses("other_claim_id")` (pulls
  its evidence + inputs transitively). Reuse derivation helpers via `k.derive.fn(k)`.
- **lifecycle** = pytest states: `@pytest.mark.xfail(reason=…, strict=True)` = contradicted
  but kept on record; `pytest.skip(reason=…)` = unverifiable. `@kind`/`@strength` still apply.
- **fit determinism**: pin versions (pyproject), compare derived floats with
  `pytest.approx`/log-tolerance — never byte-identical.

## Run it

```
pytest "<exp>/analysis/claims"                       # one experiment
pytest <exp1>/analysis/claims <exp2>/... --grounding-out DIR   # combined report
```

Emits `grounding_report.md` + `.json` (per claim: `{id, statement, outcome, kind,
strength, caveats, evidence, inputs+shas, reconcile}`). `pip install -e .[reports]` adds
`pdfplumber`/`python-docx` for `doc()`-based external claims that quote a CRO report.

## Provenance, guard, temporality

- **Auto-capture**: every `kicho`/`load`/`doc` read during a claim records `(kind, path,
  sha256)`; `uses` carries another claim's inputs transitively. Never hand-maintained.
- **Bypass guard**: during a claim, a direct `open`/`pandas.read_csv` of a tracked source
  file under `$KICHO_ROOT` is captured + flagged, so the input set can't be incomplete.
- **Reconcile lint**: warns when declared fixtures ≠ captured inputs (dead fixture /
  undeclared cross-experiment read).
- **Temporal ledger = git**: editing a `@strength` or a statement across commits is a
  belief change; `git blame` + the commit message is the "as-of" rationale. No YAML.

## Authoring a new experiment

See [`references/playbook.md`](references/playbook.md) for the step-by-step workflow,
the de-overload procedure, the claim-kind checklist, and fan-out guidance.
