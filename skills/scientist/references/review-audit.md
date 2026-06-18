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

3. **Apply the grounded rule — a `kind=claim` is the only accepted backing.** A result is **backed** only
   if a *grounded* claim asserts it — `outcome` is `passed` or `xpass` **and** `strength` is `strong` or
   `moderate`. A raw `analysis/` cell is *grounded provenance* but not *judged evidence* (no outcome, no
   strength, nothing vouching it's the right/headline number), so it does **not** by itself back prose —
   the claim is where that judgment, drift-tracking, and search-indexing live, and a claim is what cites
   the artifact. When no grounded claim covers a result, use **artifact-tracing as triage** (not as an
   alternative backing) to set severity: does the asserted number appear verbatim (within rounding) as a
   cell in a *current, non-drifted* sha-pinned `analysis/` artifact whose edge is live in the ledger
   (confirm with `sci read <path>`; check drift with `sci trace <exp>` / `sci audit`)? Classify:
   - **backed** — a grounded claim asserts the result;
   - **artifact-only** — *no* claim asserts it, but the number traces to a current analysis artifact. A
     **finding to clear, not a pass** — but a cheap one: author the claim citing that cell (`[claim:<id>]`),
     so the result becomes judged, drift-tracked, and searchable. Name the artifact path + cell.
   - **unbacked** — *no* claim **and** *no* analysis artifact carries this result (a pure prose assertion —
     invented or from an untracked source; find the source before anything else);
   - **weak-backing** — the only backing claim is contradicted (`xfail`), drifted (`failed`),
     unverifiable (`skipped`), or weak/unspecified strength → report it *with* the claim's
     `outcome`+`strength`, so prose leaning on a contradicted result is caught, not silently passed;
   - **off-topic** — the cited claim is grounded but isn't actually *about* this sentence (a tolerability
     claim cited next to an efficacy number).
   - **derived** — the number is a *combination* the cited claims don't individually assert: an arithmetic
     function of two or more claims (e.g. "≈75% knockdown" obtained by multiplying a "2× baseline" claim
     by a "≤50% loss tolerated" claim), or a value that differs from the figure in its single cited claim
     (the sentence says 75%, the claim says 48%). Each citation *resolves and backs*, so the mechanical
     audit passes — but the asserted quantity itself is ungrounded. Treat it like an `unbacked` number:
     a derived quantity must be produced by a grounded `analysis/`/`program` **derivation** (then cited
     as the claim that wraps it), not computed inline in prose. This is the one category the per-citation
     audit structurally cannot see, so it is the §3 pass's job specifically.
   - **incommensurate-evidence** — the citation resolves and is grounded, but the conclusion *leans on it
     out of proportion to how robust it is*, and the prose doesn't say so. **For each load-bearing claim
     or bound, weigh evidentiary robustness against how much the conclusion depends on it**, and check
     that the cited evidence's *measured scope actually transfers to the use*. Grounding certifies that a
     quote backs its paraphrase — not that a single, indirect, out-of-scope, or one-lab result is *enough*
     for the weight a central conclusion puts on it. "Not robust" is broader than weak strength: a single
     group (`independent_groups<=1` / "all one lab"), an indirect (`suggestive`) or secondary/relayed
     source, an abstract-/title-only source, a contested or unreplicated result, a result used **outside
     the scope it was measured in** (a prenatal-model datum bounding a postnatal therapy), a tidy
     quantitative bound resting on one study, or an analogy doing load-bearing work — any of these, *when
     a central conclusion leans on it*, should be acknowledged in the prose **where the claim does its work**
     (the sentence that derives the ceiling), not only in an assumptions list. Flag a load-bearing
     conclusion on non-robust evidence that the prose does not visibly hedge. The `weak-load-bearing`
     audit advisory (report.md §5) mechanically raises candidates from the strength/independence/source
     fields and %/×/fold bounds — and now hands the §3 reviewer each flagged claim's `strength` and its
     review **note** (the "all one lab" caveat the author already wrote), plus the per-source robustness
     signals — but it cannot judge *which* claim is load-bearing or whether the scope transfers — that
     judgment is this pass's, specifically. This finding is **surfaced, not blocking** (see grading below):
     the reviewer must EITHER ensure the prose discusses the evidence's strength where the claim does its
     work — hedging the claim in the sentence that uses it and marking the bound provisional (or
     strengthening the evidence), **not** deleting the assumptions note — OR explicitly waive it with a
     one-line note in the assumptions/weak-support section. The point is that the text ends up
     acknowledging the weakness, not that the audit refuses; a hard block on a genuine judgment call only
     trains reflexive waive-throughs.

4. **Grade severity, then report.** Three tiers:
   - **blocking** — an `unbacked` numeric result, a `weak-backing`, an `off-topic` citation, a `derived`
     inline quantity, or any contradicted backing. Fix the prose or the citation.
   - **finding (clear it)** — an `artifact-only` result: the number is real but uncovered by a claim →
     author the claim citing the cell. Not blocking (the evidence exists), but not a pass either — it
     stays on the worklist until a claim covers it.
   - **advisory (address or explicitly waive)** — an *unbacked qualitative* conclusion (soft prose with no
     number; note it, not a failure), and an `incommensurate-evidence` finding (a load-bearing claim/bound
     on non-robust evidence the prose doesn't hedge). Like `missing-disconfirmer`, this is not a hard
     auto-fail gate: either ensure the prose discusses the claim's evidentiary strength where it does its
     work, or waive it with a one-line note in the assumptions/weak-support section. A hard block on a
     genuine judgment call trains reflexive waive-throughs; the goal is that the text acknowledges the
     weakness, not that the audit refuses.

   Don't rewrite silently; report each finding with its doc, line, the sentence, the claim it maps to (or
   that it's missing, plus the artifact cell when `artifact-only`), and the claim's outcome/strength.

The grounded rule and `claim_id` format match `index-claims` / `sci query --kind claim` / `sci trace`. The
claim is the sole backing unit; it cites the sha-pinned artifact, and `sci trace` walks the full chain
(claim → artifact → data → raw). The report phase (`sci report`, §5) runs the identical procedure over
generated report Markdown — reports cite claims the same way, never a bare artifact.

**Run this pass in a fresh-context subagent, not as author self-review.** The author of a draft is the
worst grader of it: having built an inference (especially a `derived` number — "I chained these two claims,
so it's fine"), the author re-reading its own prose carries the same reasoning and the same finish-line
bias, and waves the inference through — the exact way an ungrounded derived quantity survives to a GROUNDED
audit. A subagent given a *fresh context* never sees that reasoning trace and has no stake in defending it,
so it reads "75% [cites claims saying 48% and ≤50%]" cold. Fresh context defeats *motivated* and
*contextual* misjudgment; it does not, alone, defeat a *systematic* model bias (same model, same priors) —
so prompt it **adversarially and specifically** ("list every number whose cited claim does not itself
contain that value; list every quantitative sentence that combines two or more claims"), and **hand it the
claim contents** (the grounding report for everything cited), not just the prose — without the claims'
actual numbers *and their evidence's robustness signals* (per-source `group`/`primary`/`test`/`mode`/
`tier`, the claim's `strength`, and the review **note** the `weak-load-bearing` advisory now surfaces) it
can only re-check citation presence, the part that already passed, and can't weigh whether a load-bearing
claim's support is commensurate with its centrality. Make the mechanical findings (`unbacked`, `off-topic`,
`weak-backing`, `derived`, claim-number ≠ sentence-number) **blocking** so "empty list" is an objective
stop condition rather than "the author is satisfied"; the soft `advisory` items — including
`incommensurate-evidence` — are surfaced for the author to address or explicitly waive. For the report
phase this subagent pass is **required** — see [report.md](report.md).

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

## Claims coverage — is the grounding keeping up with the library?

```bash
sci coverage [--since 2026-06-16] [--home H] [--json]   # library papers cited by NO grounded claim
```
The completeness counterpart to `sci report`: the audit checks that the citations a report *wrote*
resolve; `coverage` checks the opposite gap — papers banked into the bibliographer library that **no
grounded literature claim cites**. A literature sweep that grows the library by dozens of papers while
the claim set stays put is the silent failure (the library looks like diligence, the audit stays green,
the grounding quietly stagnates). It diffs the library citekeys (`bib list --json`) against the
citekeys any claim cites (`evidence.lit_sources`), and prints coverage plus the uncited papers
newest-first — flagged by `--since` (everything banked on/after a date) or the most recent N.

It is a **worklist generator, not a gate** (always exit 0): the set-difference is mechanical, but
judging which uncited papers are load-bearing enough to deserve a claim is an agent's job — ideally a
fresh-context **completeness critic** that reads the flagged papers and proposes claims (or strength
upgrades where a paper adds an independent group). Run it after a sweep; treat a pile of recently-banked
uncited papers as the prompt to write the claims the sweep earned. The bibliographer CLI is found via
`--bib`, `$SCIENTIST_BIB_CMD`, the sibling `bib.py`, or `bib` on PATH (it needs `$BIBLIOGRAPHER_HOME`).

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

### Report-rooted trace

```bash
sci trace <report.md> [--home H] [--json]   # report -> each cited claim -> analysis -> data -> raw
```

Pass a **report Markdown** instead of an experiment and `trace` puts a **report node atop the DAG**:
it parses the report's `[claim:<id>]` citations, resolves each to a live claim (across every
experiment's grounding report under `--home`), and reuses the per-claim walk to chain each cited claim
to raw. Fans in across experiments; the report is **GROUNDED** only when every cited claim resolves and
its chain is unbroken (`{report, terminals:[{cite, claim_id, experiment, path_to_raw, breaks}], breaks,
status}`). This is the traceability half of the **report phase** — see below.

## Report phase — `claims → report`

The terminal phase: a human-facing narrative built *from* grounded claims, holding the same grounding
discipline. `sci report <report.md>` **mechanizes** what's mechanical — it validates that every inline
`[claim:<id>]` citation resolves to a *live, grounded* claim (the identical grounded rule + `claim_id`
format this file's prose↔claims check uses) and every `![..](..)` figure/table embed is a *current*
sha-pinned `analysis/` artifact (drifted / untracked / dangling embeds fail) — then renders the
validated report to a PDF (pandoc) and indexes it as `kind=report`. The **semantic** "is every result
cited / on-topic / not over-reaching" judgment stays the prose↔claims semantic pass above (the same
discipline, now over `reports/*.md`); `sci report` does **not** re-introduce assertion-detection. Full
authoring model, the grounded-derivation figure model, and verdicts: [report.md](report.md).

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
