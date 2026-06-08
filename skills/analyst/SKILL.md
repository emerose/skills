---
name: analyst
description: >-
  Add a re-derivable analysis layer and grounded, traceable scientific claims on top of
  an experiment's tidy data/ (the extractor's output). Provides `experiments` (typed, tracked
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

- **`experiments`** — typed, tracked data access. `from experiments import k1_000000 as k` resolves
  the `K1-000000 *` folder under `$EXPERIMENTS_ROOT` and returns a `Study`. Tidy `data/` tables
  are attributes: `02_assay_summary.csv` → `k.assay_summary` (drop the `NN_` prefix). Access
  is lazy, cached, **sha-pinned**, and recorded as provenance on every read. `k.meta` =
  `experiment.yml`; `k.analysis.<name>` = a derived table under `analysis/tables/`;
  `k.derive` = the experiment's `analysis/derive.py` (loaded collision-free); `dir(k)`
  lists tables (IPython tab-completion). DataFrames carry `.attrs["source"]`/`["sha256"]`.
- **`analyst`** — the harness + pytest plugin. `load()/data()` (tracked loader), `doc()`
  (record a CRO report PDF/docx **or a .pptx TC deck**; the returned `DocRef.text()` /
  `DocRef.contains()` extract + quote-match it), `evidence(**kv)`, `uses(claim_id)` (compose on another
  claim, transitive provenance), `derivation(study, __file__)` (analysis-provenance
  recorder), and the `@strength`/`@caveats`/`@kind` markers. The plugin captures
  provenance per claim, **bypass-guards** untracked source reads, runs a **reconcile
  lint**, and emits the **grounding report**.

Both are installed together: `pip install -e skills/analyst` (or `uv pip install -e`).
The pytest plugin auto-loads (a `pytest11` entry point), so a bare `pytest analysis/claims/`
works. Scientific deps are version-pinned in `pyproject.toml` for fit determinism.

**Work in an isolated worktree, never the Drive checkout.** The data repo's Drive checkout
is one shared working tree/HEAD that GitSync owns; concurrent fan-out units racing it corrupt
each other's commits and can't push (mmap). `raw/` is tracked, so provision an off-Drive
worktree and set `EXPERIMENTS_ROOT` to it: `eval "$(skills/analyst/scripts/new-unit.sh
k1-000000)"`. Commit + push from there to GitHub; see the playbook §0.

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

Plain importable functions (no decorators) that compute artifacts, reading via `experiments`
(provenance auto-captured). Analysis **choices** (fit model, point exclusions,
normalization) live in code + comments — explicit and reviewable. A `main()` writes the
artifacts through a `derivation` context, which records analysis provenance into
`experiment.yml` (artifact + sha, inputs = the data files read + this recipe):

```python
def ec50_table(k):                       # importable, IPython-friendly
    dr = k.dose_response                  # tracked read
    ...                                   # scipy Hill fit; document exclusions in comments
    return df

def main():
    import analyst
    from experiments import k1_000000 as k
    with analyst.derivation(k, __file__) as d:
        d.write_table("ec50_summary.csv", ec50_table(k))
        d.write_fig("dose_response_fits.png", plot_fits(k))
```

### claims/test_*.py — grounding specs

A claim **is** a pytest test. Request the `experiment` fixture — it resolves the Study
from the test file's path, so **no conftest is needed** (the fixture ships with the
plugin):

```python
import pytest
from analyst import strength, caveats, kind, evidence, uses

@kind("result")                                   # result | design | external | interpretive
@strength("strong")                               # strong | moderate | weak | unverifiable | ...
@caveats("single positive-control series; n=2 wells at the top dose")
def test_pos_ctrl_below_criterion(experiment):    # `experiment` = this folder's Study
    "Positive-control guide ctrl-1 ~45% knockdown at the 100 nM top dose — below the >60% criterion."  # = the statement
    q = experiment.assay_summary                   # tracked read (captured as provenance)
    kd = q[(q["guide_id"]=="ctrl-1") & (q["conc_nm"]==100)]["pct_kd"].mean()
    evidence(kd_pct=round(kd,1), criterion_pct=60) # headline numbers for the report
    assert kd == pytest.approx(45, abs=3) and kd < 60   # the grounding / drift check
```

- **docstring** = statement · **node id** = stable id · **`experiment` (+ reads)** =
  inputs · **body** = justification · **assert** = grounding/drift check · **markers** =
  the non-binary judgment (kept *out* of the assert).
- **bulk** via `@pytest.mark.parametrize`. **compose** via `uses("other_claim_id")` (pulls
  its evidence + inputs transitively). Reuse derivation helpers via `experiment.derive.fn(experiment)`.
- **cross-experiment**: import another study and wrap it in `cross(...)` to declare the
  dependency — `from experiments import k1_000000; other = cross(k1_000000)`; reads from it
  are captured as provenance (sha-pinned) and the reconcile lint treats them as expected.
- **lifecycle** = pytest states: `@pytest.mark.xfail(reason=…, strict=True)` = contradicted
  but kept on record; `pytest.skip(reason=…)` = unverifiable. `@kind`/`@strength` still apply.
- **identifiers**: id columns whose values only look numeric (e.g. `guide_id` `01`/`08`,
  leading zeros) are preserved as strings by the tracked loader — compare
  `row["guide_id"] == "73"`, not `== 73`. Measurement columns stay numeric.
- **fit determinism**: pin versions (pyproject), compare derived floats with
  `pytest.approx`/log-tolerance — never byte-identical.

## Run it

```
pytest "<exp>/analysis/claims"                       # one experiment
pytest <exp1>/analysis/claims <exp2>/... --grounding-out DIR   # combined report
pytest <…>/analysis/claims --check-drift              # also flag stale claims (see below)
EXPERIMENTS_ROOT=… scripts/rollup.py [--out DIR] [--no-drift]   # PROGRAM-WIDE rollup
```

Emits `grounding_report.md` + `.json` (per claim: `{id, statement, outcome, kind,
strength, caveats, evidence, inputs+shas, reconcile, drift?}`). `pip install -e .[reports]`
adds `pdfplumber`/`python-docx`/`python-pptx` for `doc()`-based external claims that quote a
CRO report (PDF/docx) or a TC slide deck (pptx).

**Program rollup** — `scripts/rollup.py` runs *every* `<exp>/analysis/claims` under
`$EXPERIMENTS_ROOT` in one session (so cross-experiment `cross()`/`uses()` links resolve)
and aggregates into a `program_evidence.{md,json}` "state of the evidence": counts by
outcome/kind/strength, a per-experiment table, the **cross-experiment claim graph** (every
claim whose evidence spans >1 experiment), and the stale-claim (drift) list. It is the
substrate for a semantic audit (checking the program's stated conclusions against the
grounded claims).

### External claims — quoting a report or deck

`doc(path)` sha-pins the cited bytes and returns a `DocRef`. Don't hand-roll extraction:
call `ref.text()` (suffix-dispatched: `.pdf`→pdfplumber, `.docx`→python-docx,
`.pptx`→python-pptx; offline + deterministic, no LibreOffice/hosted API) or, for the
quote check, `ref.contains("verbatim phrase")` (whitespace-normalized substring match —
the robust default for prose split across lines/runs/cells):

```python
@kind("external")
@strength("strong")                                    # signed report → strong is OK
@caveats("verbatim quote from the signed CRO report (sha-pinned via doc())")
def test_report_no_mortality(experiment):
    "The signed CRL report states: 'no mortality was observed...'."
    ref = doc(os.path.join(experiment.path, REPORT))   # records the PDF/docx sha
    assert ref.contains("no mortality was observed")
```

- **Decks are weaker evidence.** A TC `.pptx` is a summary (rounded numbers, scattered
  text), not a signed deliverable. Cap deck-grounded external claims at
  `@strength("moderate")` with a `@caveats(...)` noting the source is a presentation, and
  match **short** verbatim phrases (`ref.is_presentation` flags the deck for reviewers).
  This is *authoring guidance* — the harness keeps `@strength` as your judgment, it does
  not silently cap it.
- Legacy `.doc`/`.ppt` (and other office formats) aren't supported — `ref.text()` raises
  `UnsupportedDocFormat`. Re-save as `.docx`/`.pptx`/PDF.

## Provenance, guard, temporality

- **Auto-capture**: every `experiments`/`load`/`doc` read during a claim records `(kind, path,
  sha256)`; `uses` carries another claim's inputs transitively. Never hand-maintained.
- **Bypass guard**: during a claim, a direct `open`/`pandas.read_csv` of a tracked source
  file under `$EXPERIMENTS_ROOT` is captured + flagged, so the input set can't be incomplete.
- **Reconcile lint**: warns when a claim's declared experiment (its home folder + any
  named `k1_NNNNNN` fixture) ≠ the experiments it actually read from (empty claim /
  undeclared cross-experiment read).
- **Temporal ledger = git**: editing a `@strength` or a statement across commits is a
  belief change; `git blame` + the commit message is the "as-of" rationale. No YAML.
- **Drift (`--check-drift`)**: for each claim, finds the commit that last set its
  `@strength` marker (`git blame`) and flags the claim **stale** if any captured input
  changed since then → re-judge. Off by default (keeps runs fast + git-free); when on,
  each claim gets a `drift` field (`fresh` vs `stale` + the changed inputs).

## Authoring a new experiment

See [`references/playbook.md`](references/playbook.md) for the step-by-step workflow,
the de-overload procedure, the claim-kind checklist, and fan-out guidance.
