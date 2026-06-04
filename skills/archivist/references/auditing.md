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

> Read this experiment's `README.md` and its `source_files`. Does every claim, number,
> and caveat in the README still match the data? List specific contradictions (claim
> vs. what the data shows), missing caveats, and stale numbers. Don't rewrite — report.

Collect the contradictions; for each confirmed one, edit the README prose (preserving
the human caveats that still hold), run `arx review <exp>` to re-stamp provenance, and
open a PR with `arx pr`. This is the same technique bibliographer uses to verify a
paper's content against its metadata, and it's the **authoritative** check — the
fingerprint is a change signal, not proof of (in)correctness.

## Cadence

Run `arx check` after every `intake` or big change, and a full `arx audit` (plus the
semantic pass on anything flagged) periodically — especially after re-analyses that
change results the summaries depend on.
