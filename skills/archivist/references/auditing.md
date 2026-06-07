# Auditing the data folder

Archivist's store drifts as experiments are added, re-analyzed, and re-summarized.
Two complementary passes keep it honest — a fast deterministic one and a semantic
one that actually reads the data. Both **only report**; fixes are applied through
`arx` (and land as PRs).

## 1. Structural — `arx check`

Deterministic, instant, no embeddings. Flags, per experiment:

- `missing:readme` — no `README.md`.
- `file-missing:<path>` — an indexed file is gone from disk.
- `unindexed:N` — N on-disk files aren't in the index → run `arx index <exp>`.
- `thin-metadata` — no CRO / study IDs / assays / ASOs extracted (often a stub or a
  README that needs filling).
- `layout:root-file:<name>` — a stray file at the experiment root (only `README.*`
  belongs there; everything else goes in `raw/ data/ protocol/ reports/ analysis/`).
- `redundant-archive:<zip>` — a zip whose members are already extracted in the same
  folder (the `raw.zip` pattern). Verify, then delete the zip (the originals also
  live in `Attic/`).

Run `arx check --json` to get a worklist you can drive fixes from.

## 2. Staleness — `arx review` + `arx audit`

Each `experiment.yml` records, under `provenance`, an **explicit list of the input
files** the README was verified against — each with its `sha256` at review time, plus
the README's own `sha256` and the `reviewed_at` date — written by **`arx review
<exp>`** once you've confirmed the prose matches the data. Inputs are the experiment's
in-folder data files plus any external dependency declared with `--input` (e.g. CRO
slides under `Shared/`). `arx audit` re-hashes them and compares:

- `up-to-date` — every input and the README unchanged since the last review.
- `stale` — the report names each input that **changed** / went **missing** / was
  **added** (new in-folder data not yet recorded), and flags if the README itself was
  edited since review. Run `arx fingerprint <exp>` to see the current input set +
  hashes, re-verify the prose, then `arx review <exp>` to re-stamp.
- `no-provenance` — never reviewed; warrants a semantic look.
- `no-/invalid-experiment-yml` — create or fix the sidecar (`arx meta <exp> --suggest`).

## 3. Semantic — the parallel-agent pass (authoritative for content)

Hashing tells you an input *changed*, not whether the *prose is still true*. For
that, read the data. `arx audit --json` emits, per experiment, its `source_files`.
Fan out one agent per experiment:

> Read this experiment's `README.md` and its `source_files` (and any other files it
> cites — e.g. CRO TC decks/minutes under `Shared/`). For each specific claim, number,
> and caveat, decide whether the data **contradicts** it or merely differs in
> **framing/labeling**. Report only genuine contradictions, stale numbers, and missing
> major caveats — classify each as `contradiction` vs `imprecision`. Also return the
> exact list of files you actually relied on. Don't rewrite; report.

The verdict is a **pointer to where to look**, not the truth — always re-verify against
the primary data before changing prose (the agent can over-read; see the discipline
below). For each confirmed contradiction, correct the README, run `arx review <exp>`
(declaring every file you relied on as an input — see below), and open a one-experiment
PR with `arx pr`. This is the **authoritative** content check — the fingerprint is a
change signal, not proof of (in)correctness.

## 4. Correction discipline (learned doing real reviews)

Crispness without nitpicking — fix what's wrong, clarify what's merely loosely stated,
leave what's defensible:

- **Re-verify against the primary data, not the audit verdict.** Open the actual
  CSV/deck and check the file, column, and value. (A flag of "only 3 plates" turned out
  to be "6 cell plates → 3 qPCR plates" — both true at different layers.)
- **Separate a contradiction from a framing/labeling nuance.** Fix claims the data
  *contradicts* or that *overstate certainty*. Don't flip a number to a different but
  equally-defensible one, or "correct" a labeling choice the CRO itself used (e.g.
  "22 hits" where the 22nd is carried as a control → clarify, don't just assert 21 is
  "right" and 22 "wrong").
- **Read for internal consistency first.** READMEs often contradict *themselves*
  ("8,000 selected" vs "Exp 4 used 6,000 and 8,000"; "22 hits" while listing one of
  them as a control) — that alone often pinpoints the error before you touch the data.
- **For "passed / accepted / robust" claims the data seems to undercut, find the
  rationale before declaring the prose wrong.** Check the CRO decision docs (TC
  decks/minutes). Often the shortfall was known and explicitly accepted — state it
  honestly *with the source* (e.g. "positive control 53%, below the >60% criterion, but
  the CRO deemed it consistent per TC07") rather than either parroting "all passed" or
  flatly calling it a failure.
- **Distinguish "passed a threshold" from "was selected" from "ranked top."** Don't
  conflate "164 exceeded 50% KD" with "22 were carried forward" with "the 5 highest-KD."
- **Check sibling/related experiments for the established convention** when a
  count/structure is ambiguous (same CRO/SOW/phase often share a plate-replication
  scheme).
- **Preserve hard-won caveats**; prefer an honest hedge over a clean falsehood.
- **One experiment per PR**, and cite the exact evidence (file + column + value) in the
  PR body so the correction is independently checkable.

## 5. Provenance: list everything you relied on

When you verify or correct a README, **declare every file you consulted as a provenance
input** so the prose's evidentiary basis is recorded and drift-tracked — not just the
in-folder data. In-folder data files are auto-included by `arx review`; add external
ones explicitly:

    arx review <exp> --input "Shared/Vendor A/SOW1/TC Meetings/TC07 - Vendor A Sync.pptx"

If a corrected claim cites a TC deck or minutes, that file belongs in `inputs`. (The
semantic-pass agent's "files relied on" list, above, is exactly this set.)

**Gitignored ≠ excluded from provenance.** Whether a file lives in git and whether it's a
declared provenance input are *independent* — provenance records a path + sha256, which
needs no bytes in git. So a bulky source file kept out of git (over the 100 MB limit,
Attic-only, LFS-excluded) must still be declared an input when the prose rests on it;
don't let a size-based `.gitignore` silently drop it from the evidentiary record. `arx
review` auto-includes in-folder data files regardless of `.gitignore`, so a re-`review`
folds them back in (this is what an `audit` `added:` flag for such a file is telling you).
Caveat: `audit` can only re-hash an input where its bytes physically exist (the working
copy / Attic) — a bare clone missing the file reports it `missing`, not drifted. That's
fine for a Drive-backed repo; know it before trusting `audit` in CI.

**Re-stamp after a batch of cross-referencing fixes.** When one experiment declares
another's file as an input (e.g. a study citing a sibling's README as precedent) and both
are corrected in the same batch, the input sha can be captured *before* the sibling's fix
lands — so `audit` flags it `changed` afterward even though nothing is actually wrong.
After merging a batch where fixes reference each other, run `arx audit` and re-`review`
any experiment whose flagged drift is just a stale cross-reference sha.

## Cadence

Run `arx check` after every `intake` or big change, and a full `arx audit` (plus the
semantic pass on anything flagged) periodically — especially after re-analyses that
change results the summaries depend on.
