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

Each `experiment.yml` records, under `provenance`, a `data_fingerprint` over the
experiment's evidence files (see `scripts/_experiment.py` for the exact, reproducible
algorithm) plus the `reviewed_at` date — written by **`arx review <exp>`** once you've
confirmed the README prose matches the data. `arx audit` recomputes the fingerprint
and compares:

- `up-to-date` — evidence unchanged since the last review.
- `stale` — the fingerprint differs; the report shows the recorded vs current
  fingerprint, the input-count delta, and the last review date. Run `arx fingerprint
  <exp> --manifest` to see exactly which files/hashes feed it, re-verify the prose,
  then `arx review <exp>` to re-stamp.
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
