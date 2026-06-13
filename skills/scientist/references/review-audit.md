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

### Prose ↔ claims check (a semantic-pass discipline — you do this, no CLI)

Keep a `README.md` / `reports/*.md` sentence from **asserting a result without a grounded `kind=claim`
backing it**. This is a *procedure you (the reviewing agent) run*, not a tool — the whole check is
"read the claims, apply a fixed rule." There's nothing here a CLI does better than you reading the
grounding report, so there is deliberately no `sci enforce-prose` command. For each prose doc (the root
`README.md` and any `reports/*.md`):

1. **Find the evidentiary conclusions.** Read the prose and pick out the sentences that assert a *result*
   — quantitative (a %, fold-change, p-value, `n=`, dose, IC50…) *or* qualitative ("well tolerated",
   "sustained knockdown", "comparable to vehicle", "dose-dependent"). Skip background / method /
   motivation prose ("6 animals per group", "incubated 30 min", "we designed ASOs targeting X"). This
   is a judgment call — it's exactly why it's left to you and not a regex.

2. **Map each to a claim.** A result should carry an explicit citation **`[claim:<id>]`** in the prose
   (the stable `claim_id` `<exp>::<test-file>::<node>`, or its trailing node name). Pull the claims with
   `sci query "<topic>" --kind claim` or `sci list --kind claim --experiment <exp> --json` (live index),
   or read `<exp>/analysis/grounding_report.json` directly (each claim: `{id, statement, outcome,
   strength, kind}`). For an *un-cited* result, find the claim it ought to map to by reading the
   statements — if none exists, that's the finding.

3. **Apply the grounded rule.** A result is **backed** only if its claim is *grounded* — `outcome` is
   `passed` or `xpass` **and** `strength` is `strong` or `moderate`. Otherwise flag it:
   - **unbacked** — no claim asserts this result (or the result has no citation and no matching claim);
   - **weak-backing** — the only backing claim is contradicted (`xfail`), drifted (`failed`),
     unverifiable (`skipped`), or weak/unspecified strength → report it *with* the claim's
     `outcome`+`strength`, so prose leaning on a contradicted result is caught, not silently passed;
   - **off-topic / wrong-claim** — the cited claim is grounded but isn't actually *about* this sentence
     (a tolerability claim cited next to an efficacy number). Only you can catch this — it's the reason a
     dumb citation-resolver wouldn't be enough.

4. **Grade severity, then report.** An *unbacked qualitative* conclusion is **advisory** (note it; soft
   prose is fuzzy and high-volume, so don't treat a missing citation as a failure). An unbacked numeric
   result, a `weak-backing`, an `off-topic` citation, or any contradicted backing is **blocking** — fix
   the prose or the citation. Don't rewrite silently; report each finding with its doc, line, the
   sentence, the claim it maps to (or that it's missing), and the claim's outcome/strength.

The rule and the `claim_id` format are the *same* ones `index-claims`, `sci query --kind claim`, and
`sci trace` use — so this check stays consistent with how the rest of the pipeline judges "grounded."
The planned report phase (`sci report`) runs the identical procedure over generated report Markdown.

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
