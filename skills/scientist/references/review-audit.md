# Review, audit & trace — keep the chain honest

Maintains the link between what the experiment *claims* and the data that justifies it, and walks the
`raw → data → analysis → claims` DAG to find where grounding breaks. Three layers: per-artifact
**provenance review**, **staleness/structural audit**, and end-to-end **traceability** (`sci trace`).

> review/audit/check/fingerprint/trace run via `sci` (this skill's CLI); program rollup + drift via
> `skills/scientist/scripts/rollup.py` and `pytest --check-drift` (these fold into `sci` in a later
> stage). `sci trace` ties the provenance DAG and the grounding report together — see "Trace" below.

## Provenance: the one ledger

Each `experiment.yml` holds a unified `provenance` list. Every generation step appends an edge —
`artifact` + its `inputs` (each `path` + `sha256`) — for `data/…` (extract), `analysis/…` (derive),
and `README.md` (review). The inputs list is **explicit and per-file** (not an opaque roll-up), so
drift is inspectable file by file.

## README review & staleness (the prose ↔ data link)

```bash
sci fingerprint K1-000000   # the input files (+ current sha256) review would record now
sci review K1-000000 [--input <repo-rel path>]   # stamp provenance after verifying README vs data
sci audit  [K1-000000] [--json]          # staleness vs recorded provenance + a semantic worklist
```

- **`review`** — run after you've verified the README still matches the data. Records, per artifact
  (the README), each input file with its `sha256`, plus the README's own `sha256` and the date. Inputs
  = the experiment's in-folder data files (everything except a root `README.*` and the sidecar) **plus**
  any external dependency you declare with `--input` (repeatable; e.g. CRO slides under `Shared/`).
  External inputs persist across re-reviews.
- **`audit`** — re-hashes every recorded input + the README and reports `up-to-date`, `stale` (naming
  each input that **changed** / went **missing** / was **added**, and whether the README itself was
  edited since review), `no-provenance` (never reviewed), or `no-/invalid-experiment-yml`. `--json` adds
  a per-experiment `source_files` worklist for the **semantic pass**: fan out an agent per experiment to
  read the data, verify the prose, and run the **prose ↔ claims check** below — the authoritative content
  check (see [auditing.md](auditing.md)).

### Prose ↔ claims check

Keep a `README.md` / `reports/*.md` sentence from **asserting a result without a grounded `kind=claim`
backing it**. As part of the semantic pass, for each prose doc (the root `README.md` and any
`reports/*.md`):

1. **Find the evidentiary conclusions.** Pick out the sentences that assert a *result* — quantitative
   (a %, fold-change, p-value, `n=`, dose, IC50…) *or* qualitative ("well tolerated", "sustained
   knockdown", "comparable to vehicle", "dose-dependent"). Skip background / method / motivation prose
   ("6 animals per group", "incubated 30 min", "we designed ASOs targeting X").

2. **Pull the experiment's claims once, then map each result.** Don't query per sentence — read the whole
   claim set for the experiment up front (`sci list --kind claim --experiment <exp> --json`, or
   `<exp>/analysis/grounding_report.json` directly: each claim `{id, statement, outcome, strength, kind}`;
   `sci query "<topic>" --kind claim` is for *finding* the right claim when a statement is hard to match),
   then map each result against that in-context list. A result should carry an explicit citation
   **`[claim:<id>]`** in the prose (the stable `claim_id` `<exp>::<test-file>::<node>`, or its trailing
   node name). For an *un-cited* result, find the claim it ought to map to by reading the statements.

3. **Apply the grounded rule (claim first, then artifact).** A result is **claim-backed** only if its
   claim is *grounded* — `outcome` is `passed` or `xpass` **and** `strength` is `strong` or `moderate`.
   If no grounded claim covers it, check the **analysis-artifact** path before flagging it unbacked: does
   the asserted number appear verbatim (within rounding) as a cell in a *current, sha-pinned* `analysis/`
   artifact — a recorded `analysis/tables/*.csv` whose edge is live in the provenance ledger? Confirm the
   value with `sci read <path>` and that the artifact isn't drifted (`sci trace <exp>` / `sci audit`). This
   is the §5 backing unit (a grounded `kind=claim` **or** a sha-pinned analysis artifact). Classify:
   - **backed** — a grounded claim (or, secondarily, a live sha-pinned artifact) supports the result;
   - **artifact-only** — the number traces to a current analysis artifact but *no* claim asserts it. The
     evidence exists, so this isn't drift — but author the claim so the result is drift-tracked and
     surfaced in search (`sci query --kind claim` / `sci trace`). Name the artifact path + cell.
   - **unbacked** — *no* claim and *no* analysis artifact carries this result (a pure prose assertion);
   - **weak-backing** — the only backing claim is contradicted (`xfail`), drifted (`failed`),
     unverifiable (`skipped`), or weak/unspecified strength → report it *with* the claim's
     `outcome`+`strength`, so prose leaning on a contradicted result is caught, not silently passed;
   - **off-topic** — the cited claim is grounded but isn't actually *about* this sentence (a tolerability
     claim cited next to an efficacy number).

4. **Grade severity, then report.** An *unbacked qualitative* conclusion and any **artifact-only** result
   are **advisory** (note them; a missing citation on soft prose, or a number that's genuinely grounded in a
   sha-pinned artifact but lacks an authored claim, isn't a failure — author the claim to clear it). An
   unbacked numeric result, a `weak-backing`, an `off-topic` citation, or any contradicted backing is
   **blocking** — fix the prose or the citation. Don't rewrite silently; report each finding with its doc,
   line, the sentence, the claim *or artifact* it maps to (or that it's missing), and the outcome/strength.

The grounded rule and `claim_id` format match `index-claims` / `sci query --kind claim` / `sci trace`; the
analysis-artifact backing path is the same sha-pinned grounding `sci trace` walks (claim → artifact → data
→ raw). The planned report phase (`sci report`) runs the identical procedure over generated report Markdown.

## Structural check

```bash
sci check [K1-000000] [--json]   # structural integrity; never mutates
```
Flags missing `README.md`/`experiment.yml`, on-disk files not indexed, layout drift, thin metadata,
and **redundant archives** (a zip whose members are already extracted in-folder — the `raw.zip` case).
Emits a worklist.

## Data-extraction audit

The `data/` edges have their own re-extraction audit (determinism, grounding, recipe-sha, data/↔recipe,
reconciliation, naming) plus the full cell-coverage check — see [extract.md](extract.md) (`audit.py`,
`cellcov.py`). Because the data edges live in the same `provenance` list, stock `sci audit` checks them too.

## Claims: grounding report, rollup, drift

Running the pytest claims emits `grounding_report.{md,json}` (per claim: `{id, statement, outcome, kind,
strength, caveats, evidence, inputs+shas, reconcile, drift?}`).

```bash
pytest <…>/analysis/claims --check-drift        # flag stale claims (input changed since @strength set)
SCIENTIST_HOME=… rollup.py [--out DIR] [--no-drift]   # PROGRAM-WIDE rollup
```

- **Program rollup** runs *every* `<exp>/analysis/claims` in one session (so cross-experiment
  `cross()`/`uses()` links resolve) and aggregates into `program_evidence.{md,json}`: counts by
  outcome/kind/strength, a per-experiment table, the **cross-experiment claim graph** (every claim whose
  evidence spans >1 experiment), and the stale-claim list. The substrate for a semantic audit of the
  program's stated conclusions.
- **Drift (`--check-drift`)** — for each claim, finds the commit that last set its `@strength` marker
  (`git blame`) and flags the claim **stale** if any captured input changed since then → re-judge.
- **Temporal ledger = git.** Editing a `@strength` or a statement across commits is a belief change;
  `git blame` + the commit message is the "as-of" rationale. No YAML.

**Claims feed the store.** After running the claims, `sci index-claims <exp>` indexes each claim from the
grounding report into libkit as a `kind=claim` card (statement embedded; outcome + strength + claim kind
as metadata; stale claims pruned), so `sci query "…" --kind claim` surfaces grounded evidence directly —
and never surfaces a contradicted (`xfail`) or weak claim as fact without its status.

## Trace — end-to-end

```bash
sci trace <exp> [--json] [--claim <id>] [--report PATH]   # claim → analysis → data → raw, with breaks
```

`sci trace` walks the one provenance ledger to connect each terminal back to the original measurements —
**claim → analysis artifact(s) → `data/` file(s) → `raw/` source(s)** — and flags every break. It is a
**pure provenance walk: it needs NO libkit store** (reads only `experiment.yml` + an optional
`grounding_report.json`) and never re-runs an analysis (reproduction is out of scope — this is a static
DAG + drift walk).

- **Terminals.** With a grounding report present (default search: `<exp>/analysis/grounding_report.json`,
  then `<exp>/grounding_report.json`; override with `--report`), each *claim* is a terminal and its cited
  `inputs` are its backing. `--claim <id>` traces just one claim (full nodeid or its trailing name). With
  no report, the README + top `analysis/` artifacts are the terminals.
- **Break categories** (each names the offending file):
  - `missing` — a recorded input file is absent on disk;
  - `drifted` — a recorded input's bytes differ from its recorded sha (reuses `staleness`);
  - `unsourced` — a `data/` edge with no `raw/` input, or an `analysis/` edge with no `data/` input;
  - `dangling` — a claim/edge references an artifact or data file that no edge produces and that isn't on disk;
  - `ungrounded` — a claim whose inputs include no `data/` or `analysis/` artifact (a pure assertion).
- **Output.** Human-readable per-terminal chain-to-raw + breaks + an overall **GROUNDED / BROKEN** verdict;
  with `--json`, `{experiment, chains:[{terminal, kind, path_to_raw, breaks}], breaks, status}`. Exit 0 if
  fully grounded, 1 if any break. (Lives in `provenance/trace.py` — provenance-level and store-free.)

## Reproduce — does the analysis actually re-run?

```bash
# the editable install brings the PINNED analysis runtime (pandas/scipy/matplotlib);
# the bare `uv run sci.py …` PEP723 env does NOT have it — `reproduce` re-executes derive.py.
SCIENTIST_HOME=… uv run --with-editable skills/scientist \
  skills/scientist/scripts/sci.py reproduce <exp> [--json] [--rtol R] [--atol A]
```

`sci trace` is *static* — it checks recorded shas still match but executes nothing. `sci reproduce`
is the **executable** complement: it **re-runs `<exp>/analysis/derive.py main()`** in the pinned
environment and checks the regenerated `analysis/tables|fig/*` reproduce the recorded artifacts, and
that the derivation read only from `data/`. It turns "the recipe sha still matches" into "the recipe
still produces the numbers." (Lives in `provenance/reproduce.py`; store-free, like `trace`.)

- **Pure re-run, never destructive.** The derivation re-runs under a *derivation-audit* context
  (`grounding.audit_derivations`): `write_table`/`write_fig` are redirected to a temp scratch dir, **no**
  provenance is written, and the recorded `analysis/` artifacts + `experiment.yml` are never touched.
- **Three independent verdicts** per experiment:
  - **runs** — `derive.main()` executed without raising (a recipe that errors is flagged);
  - **reproduces** — every recorded `analysis/` artifact regenerated within tolerance;
  - **reads_only_data** — every input the derivation read is the experiment's own `data/` (plus
    `experiment.yml` config + the program convention/reference facts the canonical-id boundary uses).
- **Extends the bypass guard to derivations.** The same capture/guard that flags untracked or
  out-of-`data/` reads during *claims* (`grounding.plugin`) stays live for the whole re-run, so a
  derivation that reaches into `raw/`, into a derived `analysis/` artifact, into another experiment, or
  does any untracked read is flagged as an **off-data read** — naming the file and why.
- **How artifacts are compared:**
  - **tables (`.csv`)** — exact sha first (a deterministic table reproduces byte-for-byte), else a
    numeric-tolerant cell-by-cell compare (identical columns + shape; numeric cells within `--rtol`
    `--atol`, mirroring the `pytest.approx` convention claims use for Hill/EC50 fits; both-NaN equal;
    non-numeric cells exact). Mismatches name the first differing cells.
  - **figures (`analysis/fig/*`)** — figures are **not** byte-compared: a PNG embeds
    matplotlib/freetype/libpng versions (and the *numbers* a figure draws are already covered by the
    table check), so bytes differ across pinned-but-distinct environments without anything having moved.
    Instead we confirm the figure **regenerated** and that its decoded pixel dimensions match the
    recorded figure within a few px (read straight from the PNG `IHDR`, stdlib only). A different format
    degrades to an existence-only "regenerated" verdict.
- **Output.** Human-readable per-artifact verdicts (`exact` / `approx` / `regenerated` / `MISMATCH` /
  `NOT REGENERATED`) + any off-data reads + an overall **REPRODUCES / BROKEN** (or `NO-DERIVATION`)
  status; with `--json`, `{experiment, recipe, runs, reproduces, reads_only_data, artifacts,
  off_data_reads, status}`. Exit 0 if `REPRODUCES`, 1 otherwise.

## Changes land as reviewable PRs

```bash
sci pr "title" <paths…> [--dry-run]   # branch, commit, push, open a PR for you to review & merge
```
The data folder is a git repo with a private GitHub remote; scientist never writes silently to `main`.
The libkit store (`.scientist/`) is gitignored. `--dry-run` shows the git/gh steps first.

## Maintaining (for agents working ON scientist)

For the periodic correctness/hygiene procedure — structural `check`, deps-staleness `audit`, and the
parallel-agent semantic pass — see [auditing.md](auditing.md).
Keep stateful stores healthy (repo-root AGENTS.md): a fast deterministic pass for structure + a
parallel-agent pass that actually reads the data, both emitting a structured worklist.
