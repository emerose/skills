# analyst — analysis + claim-grounding layer (draft spec)

Closes the provenance pipeline `raw → data → analysis → claims`. The extract stage already
produces trustworthy `data/`. This layer adds (1) **comprehensive, re-derivable
analysis** of each experiment (EC50 fits, stats, summaries, figures) traced to `data/`,
and (2) **grounded, traceable claims** — every scientific assertion linked to the exact
evidence that justifies it, re-runnable for audit, with non-binary support and temporal
history.

## Design principles (settled)
- **No DSL.** Grounding logic is plain Python; judgment may be LLM/human.
- **Claims are first-class, not README-derived.** They have ids; some are cross-experiment.
- **Provenance is automatic** — captured at runtime from one tracked data accessor.
- **Non-binary + temporal** — support strength is metadata; git is the assessment ledger.
- **Test-spec shaped** — pytest provides collection, fixtures, parametrize, and reporting.

## Roles & boundaries (where graphs, summary tables, etc. go)
Three distinct roles — keep them separate; **`extract` must not do analysis**:
- **extract (`raw → data/`)** — FAITHFUL only: reshape / merge / rename / tidy. Every
  output value traces to a cell in raw (this is what cellcov audits). No computed values,
  no fits, no choices.
- **analysis / derivation (`data/ → analysis/`)** — COMPUTATION with choices: summary
  tables (per-target KD/EC50), stats, curve fits, **and figures/graphs**. Re-derivable,
  provenance-tracked *products* — but **not claims**; most are cited by no claim.
- **claims (`data/`+analysis → assertions)** — the thin pytest layer. `analysis ⊃ claims`;
  a figure is an analysis artifact a claim may *cite*, not a claim itself.

The line between extract and analysis: **does the value already exist in the raw
deliverable?** If yes, pulling it into `data/` is faithful extraction; if the recipe
*computes* it (mean, SEM, KD%, an EC50 fit) it is analysis and moves to the analysis layer.

**De-overloading `extract`:** some recipes currently compute derived tables inside
`extract.py` (e.g. K1-000001's `03_dose_response` = per-dose mean/SEM/KD% computed
from the pzfx, with point-exclusion choices). Building the claims layer pulls those
derivations out of `extract.py` into `analysis/`, restoring extraction to pure
faithfulness; the computed tables become analysis outputs (exposed via `k.analysis.*`).

## Components

### 1. `experiments` — typed, tracked data access (one generic module)
`from experiments import k1_000000 as k` → a `Study` bound to the experiment folder (resolved by
glob on the id). Attributes are the tidy tables as pandas DataFrames; lazy, cached,
**sha-pinned**, and every access routes through the tracked loader so it is recorded as
provenance.
- `02_assay_summary.csv` → `k.assay_summary`; `k.meta` → `experiment.yml`; `__dir__()` lists
  tables (IPython tab-completion). DataFrames carry `.attrs["source"]`/`["sha256"]`.
- Root via `SCIENTIST_HOME` env. Later: `k.analysis.<name>` exposes derived outputs the same way.

### 2. `analysis/` — comprehensive derivations (products, not claims)
Plain importable functions per experiment (no decorators) that **compute** the derived
artifacts: EC50/Hill fits, group stats, per-target summary tables, **and figures**. Each
reads via `experiments` (provenance auto). Analysis **choices** (fit model, point exclusions,
normalization) live in code + comments — explicit and reviewable. Outputs are written as
artifacts — derived tables (`analysis/tables/*.csv`) and figures (`analysis/fig/*.png`) —
each recording its inputs+shas + the deriving code version (analysis provenance, parallel
to extraction provenance), and exposed via `k.analysis.*`. These are deliverables in their
own right; **most are cited by no claim.** Real env required: pandas, scipy, plotting.
Determinism: pin versions; compare derived floats within tolerance (not byte-identical).
Reusable in IPython; the claim specs ground over these.

### 3. `analysis/claims/test_*.py` — grounding specs (pytest)
A claim **is** a pytest test:
- **statement** = docstring · **id** = pytest node id (stable, free) · **inputs** = the
  `experiments`/fixtures it reads (captured) · **body** = the derivation/justification ·
  **assertion** = the grounding/drift check ("does the data still match the statement?").
- **markers carry the judgment** (kept *out* of the assert so it stays non-binary):
  `@strength("strong|moderate|weak|...")`, `@caveats("…")`, `@kind("result|design|external|interpretive")`.
  Lifecycle via pytest states: `xfail(reason=)` = contradicted/retracted (kept on record);
  `skip(reason=)` = unverifiable.
- **bulk** via `@pytest.mark.parametrize` (one body → many guides/regions/timepoints).
- **composition / cross-experiment** via fixtures that pull another claim's recorded
  evidence (`uses`).
- bodies record headline numbers via `evidence(**kv)` for the report.

```python
# analysis/claims/test_K1_000000.py
@strength("strong")
@caveats("single positive-control series; n=2 wells at top dose")
def test_pos_ctrl_below_criterion(k1_000000):
    "Positive-control guide ctrl-1 ~45% knockdown at the 100 nM top dose — below the >60% criterion."
    q  = k1_000000.assay_summary                       # load + sha-pin + provenance, one access
    kd = q[(q["guide_id"]=="ctrl-1") & (q["conc_nm"]==100)]["pct_kd"].mean()
    evidence(kd_pct=round(kd,1), criterion_pct=60)
    assert kd == pytest.approx(45, abs=3) and kd < 60
```

## Harness (`analyst` package + pytest plugin)
- API: `data()/load()` (tracked loader, via `experiments`), `uses(claim_id)`,
  `doc(path)` (→ `DocRef`; `.text()`/`.contains()` extract + quote-match a PDF/docx/pptx),
  `evidence(**kv)`, and the `strength`/`caveats`/`kind` markers.
- **Provenance capture:** a per-claim context records every `(kind, path, sha)` loaded;
  transitive through `uses`. The claim id + captured inputs + recorded evidence form a
  computed record (never hand-maintained).
- **Bypass guard:** during a claim run, patch `open`/`pandas.read_csv` so untracked source
  reads fail (or are captured) — guarantees the captured input set is complete.
- **Reconcile lint:** if a claim also declares inputs (fixtures), warn when declared ≠ captured.
- **Audit/report:** `uv run --with-editable skills/scientist pytest <exp>/analysis/claims` → a grounding report per claim
  `{id, statement, pass/fail/xfail/skip, evidence, inputs+shas, strength, caveats}`, with a
  markdown/JSON export.
- **Drift:** compare a claim's captured input shas to the shas at the commit where its
  `strength` was last set (from git) → flag stale → re-judge. (Optional generated lock; not
  a maintained YAML.)

## Temporality / no YAML
Git history of `analysis/claims/` is the assessment ledger: `strength` edits across commits
are belief changes (`git blame` + commit message = the "as-of" rationale); editing a
statement or `xfail`-ing it is supersession, with the prior version preserved. Nothing is
hand-maintained beyond the specs themselves.

## Layout
```
skills/scientist/
  experiments/__init__.py      # typed, tracked data access
  analyst/__init__.py          # data/uses/doc/evidence/strength
  analyst/plugin.py            # pytest11 plugin: capture, reconcile, grounding report
  SPEC.md
<data repo>/<exp>/analysis/
  derive.py  (or per-assay *.py)   # derivation CODE (importable, IPython-friendly)
  tables/*.csv  fig/*.png          # derivation OUTPUTS (artifacts, provenance-tracked; NOT claims)
  claims/test_*.py                 # grounding specs (assert; cite data/ + analysis outputs)
```
`extract.py` stays under `<exp>/data/` and is FAITHFUL only — derivations that crept into
it (e.g. K1-000001's computed dose-response) move here under `analysis/`.

## Open questions (defer; v1 works without resolving)
- Cross-experiment claims home — a `program/` specs dir + a libkit store, or just files?
  pytest collection + git suffice for v1.
- Fit determinism — version pinning vs tolerance compare. Decide in the pilot.
- Whether to emit a generated drift-lock — start without.

## Pilot (validate before going comprehensive)
Build the harness + `experiments` + the pytest plugin, then author derivations + claim specs for
**3 experiments**:
- **K1-000000** (in-vitro EC50) — a real Hill fit + quantitative results/design claims.
- **K1-000001** (the temporal case) — re-derivation changed potency; exercise a `strength`
  edit + drift detection. Also the **de-overload demo**: move its computed dose-response
  (mean/SEM/KD%/exclusions) out of `extract.py` into `analysis/`, leaving `extract.py`
  faithfully dumping only the pzfx cells.
- **one in-vivo** (K1-000002) — groups / quantigene / clinical: covers design, results,
  external, and interpretive claim kinds.

**Success criteria:** `pytest` emits a grounding report; provenance is auto-captured and
bypass-guarded; at least one claim of each kind exists; the K1-000001 strength change is
legible in git; and everything runs cleanly in IPython (`from experiments import k1_000000 as k`).
Refine the API on these three, then fan out comprehensively (one experiment per session).
